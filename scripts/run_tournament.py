"""Run a heads-up round robin between all available bots and write the
results to data/tournament_results.json.

Usage:
  .venv/Scripts/python.exe scripts/run_tournament.py [deals]
  .venv/Scripts/python.exe scripts/run_tournament.py --deals 20 --shard 0/4

A shard run writes data/tournament_shard_<i>.json; merge the shards with
scripts/merge_tournament.py. A single run writes data/tournament_results.json
with the matches, the per-deal values, the head-to-head matrix and the table.
"""

import argparse
import importlib
import json
import os
import pickle
import random
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "python"))
sys.path.insert(0, os.path.join(ROOT, "..", "poker_bot"))

from card_utils import full_deck  # noqa: E402
from engine import play_hand  # noqa: E402
from evals.stats import ci95, mean_and_se  # noqa: E402

SEED = 20261111

# The 5 fork bots use the shared engine; the competition teams use their own.
# The second item is the module in python/ that defines the Model.
FORK_BOTS = {
    "conservative": "model_a",
    "workhorse": "model",
    "adaptive": "model_c",
    "mixed": "model_d",
    "aggressive": "model_e",
}

# Teams whose pickle is needed because model.Model() reloads a data file that
# was not committed. Everything else uses the submitted class directly.
PKL_TEAMS = {"guinnessssss"}

TEAM_MODULES = (
    "model", "pbbrain", "pbcore", "pbpre", "pb_cards", "pb_equity",
    "pb_opponents", "pb_preflop", "fasteval", "pf_table", "pf_tables",
    "pf_data", "preflop_table", "preflop_tables", "preflop_data", "preflop_ranges",
    "equity_engine", "postflop_policy", "preflop_policy", "sidepot_equity",
    "all_in_equity", "features", "poker_eval", "pokey_strategy", "hand_eval",
    "handeval", "pokerlib", "policy", "botcore", "brain", "ranges", "equity",
    "cards", "decision", "opponents", "policy_net", "policy_weights", "strength",
    "tables", "evaluator", "strategy", "tracker", "bot_brain", "bot_decide",
    "bot_eval", "bot_opp", "bot_preflop", "bot_tables", "poker_math", "river_policy",
    "poker_strength", "pk_cards", "pk_equity", "pk_estimate", "pk_eval",
    "pk_opponent", "pk_postflop", "pk_preflop", "pk_ranges", "pk_tables",
    "core", "opp", "policy_v1", "blueprint", "translation", "hunter_chart",
    "hunter_eval", "hand_history", "bucket_data", "buckets", "arena_sdk",
    "arena_translate", "combo_bot", "core_allin", "core_equity", "core_interface",
    "main_allin", "main_bot", "main_fallback", "main_opponent", "main_postflop",
    "team_equity_table", "team_opponent", "team_preflop", "team_ranges",
)


def _clear_team_modules():
    for mod in list(sys.modules):
        if mod in TEAM_MODULES:
            try:
                del sys.modules[mod]
            except KeyError:
                pass


def _clear_team_paths():
    base = os.path.join(ROOT, "all-teams")
    sys.path[:] = [path for path in sys.path if not path.startswith(base)]


SKIP_IMPORT = {
    "model", "engine", "card_utils", "game_state", "game_interface",
    "player_action", "submit", "conftest", "arena", "evals",
}


def _preload_modules(folder):
    """Import the team's local modules so a pickle can name their classes."""
    for name in sorted(os.listdir(folder)):
        if not name.endswith(".py"):
            continue
        stem = name[:-3]
        if stem in SKIP_IMPORT or stem.startswith("test_"):
            continue
        try:
            importlib.import_module(stem)
        except Exception:  # noqa: BLE001
            pass


def _pick_pickle(folder, team):
    pkls = [name for name in os.listdir(folder) if name.lower().endswith(".pkl")]
    if not pkls:
        return None
    team_key = team.lower().replace("_", "-")
    for name in pkls:
        if os.path.splitext(name)[0].lower().replace("_", "-") == team_key:
            return os.path.join(folder, name)
    return os.path.join(folder, pkls[0])


def load_model(team):
    """Return a zero-argument factory for a fresh model.

    The submitted pickle is preferred: it is exactly what the competition ran,
    and it avoids models that reload data files in __init__. Teams that did not
    commit a pickle fall back to model.Model().
    """
    folder = os.path.join(ROOT, "all-teams", team)
    if not os.path.isdir(folder):
        return None, "no folder"

    _clear_team_modules()
    _clear_team_paths()

    if team in FORK_BOTS:
        module_name = FORK_BOTS[team]
        sys.path.insert(0, os.path.join(ROOT, "python"))
        try:
            module = importlib.import_module(module_name)
            return module.Model, folder
        except Exception as e:  # noqa: BLE001
            return None, f"import error: {e}"

    sys.path.insert(0, os.path.join(ROOT, "python"))
    sys.path.insert(0, folder)
    try:
        importlib.import_module("model")
        _preload_modules(folder)
        pickle_path = _pick_pickle(folder, team) if team in PKL_TEAMS else None
        if pickle_path:
            def load_pickle():
                with open(pickle_path, "rb") as handle:
                    return pickle.load(handle)
            return load_pickle, folder
        module = importlib.import_module("model")
        return module.Model, folder
    except Exception as e:  # noqa: BLE001
        return None, f"import error: {e}"


def play_match(team_a, team_b, deals, seed):
    """Duplicate heads-up match. Returns per-deal bb/100 values for each side."""
    model_a_cls, error_a = load_model(team_a)
    model_b_cls, error_b = load_model(team_b)
    if model_a_cls is None or model_b_cls is None:
        return None, None, 0, 0, 0

    # Keep both team folders importable while the hand runs: some models import
    # their own modules lazily inside seed/run.
    folder_a = os.path.join(ROOT, "all-teams", team_a)
    folder_b = os.path.join(ROOT, "all-teams", team_b)
    sys.path.insert(0, folder_a)
    sys.path.insert(0, folder_b)
    try:
        model_a = model_a_cls()
        model_b = model_b_cls()
        samples_a = [0.0] * deals
        samples_b = [0.0] * deals
        faults_a = 0
        faults_b = 0
        hands = 0
        for d in range(deals):
            deck = full_deck()
            random.Random(f"{seed}:{d}").shuffle(deck)
            for k in range(2):
                if k == 0:
                    models = [model_a, model_b]
                    teams = [team_a, team_b]
                else:
                    models = [model_b, model_a]
                    teams = [team_b, team_a]
                result = play_hand(models, deck, [500, 500], 0, 10, 20, teams)
                hands += 1
                if k == 0:
                    samples_a[d] += result.net[0]
                    samples_b[d] += result.net[1]
                else:
                    samples_a[d] += result.net[1]
                    samples_b[d] += result.net[0]
                for seat in result.faults:
                    if (k == 0 and seat == 0) or (k == 1 and seat == 1):
                        faults_a += 1
                    else:
                        faults_b += 1
        values_a = [x / 2 / 20 for x in samples_a]
        values_b = [x / 2 / 20 for x in samples_b]
        return values_a, values_b, faults_a, faults_b, hands
    finally:
        sys.path[:] = [path for path in sys.path if path not in (folder_a, folder_b)]


def verdict(mean, half):
    if mean is None or half is None:
        return "tie"
    if mean - half > 0:
        return "a"
    if mean + half < 0:
        return "b"
    return "tie"


def summarize(matches, values_by_team):
    """Build the standings table and the two-way head-to-head matrix."""
    standings = []
    for team in sorted(values_by_team):
        values = values_by_team[team]
        if len(values) >= 2:
            mean, se = mean_and_se(values)
            score = 100 * mean
            half = 100 * ci95(se, len(values))
        else:
            score = None
            half = None
        wins = draws = losses = 0
        opponents = set()
        for match in matches:
            if match["a"] == team:
                opponents.add(match["b"])
                result = match["verdict"]
                if result == "a":
                    wins += 1
                elif result == "b":
                    losses += 1
                else:
                    draws += 1
            elif match["b"] == team:
                opponents.add(match["a"])
                result = match["verdict"]
                if result == "b":
                    wins += 1
                elif result == "a":
                    losses += 1
                else:
                    draws += 1
        standings.append({
            "team": team,
            "score": score,
            "ci95": half,
            "wins": wins,
            "draws": draws,
            "losses": losses,
            "opponents_played": len(opponents),
            "samples": len(values),
            "faults": sum(
                match["faults_a"] for match in matches if match["a"] == team
            ) + sum(
                match["faults_b"] for match in matches if match["b"] == team
            ),
        })
    standings.sort(key=lambda row: (row["score"] is None, -(row["score"] or 0), row["team"]))
    for rank, row in enumerate(standings, 1):
        row["rank"] = rank

    matrix = {}
    for match in matches:
        a, b = match["a"], match["b"]
        matrix.setdefault(a, {})[b] = {
            "mean_bb": match["mean_bb"],
            "ci95_bb": match["ci95_bb"],
            "hands": match["hands"],
            "deals": match["deals"],
            "verdict": match["verdict"],
        }
        flipped = {"a": "b", "b": "a", "tie": "tie"}[match["verdict"]]
        matrix.setdefault(b, {})[a] = {
            "mean_bb": None if match["mean_bb"] is None else -match["mean_bb"],
            "ci95_bb": match["ci95_bb"],
            "hands": match["hands"],
            "deals": match["deals"],
            "verdict": flipped,
        }
    return standings, matrix


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("positional_deals", nargs="?", type=int, default=None)
    parser.add_argument("--deals", type=int, default=None)
    parser.add_argument("--seed", type=int, default=SEED)
    parser.add_argument("--teams", default=None, help="comma-separated team names")
    parser.add_argument("--focus", default=None, help="only play pairs that include this team")
    parser.add_argument("--exclude", default=None, help="comma-separated team names to skip")
    parser.add_argument("--shard", default="0/1", help="i/n, run every n-th pair")
    parser.add_argument("--out", default=None)
    args = parser.parse_args()

    deals = args.deals or args.positional_deals or 20
    exclude = {name.strip() for name in (args.exclude or "").split(",") if name.strip()}
    shard_text = args.shard
    shard_index, shard_count = (int(x) for x in shard_text.split("/"))
    if shard_count < 1 or not (0 <= shard_index < shard_count):
        raise SystemExit(f"bad --shard {shard_text}")

    teams = sorted(
        entry for entry in os.listdir(os.path.join(ROOT, "all-teams"))
        if os.path.isdir(os.path.join(ROOT, "all-teams", entry))
    )
    if args.teams:
        wanted = {name.strip() for name in args.teams.split(",") if name.strip()}
        teams = [team for team in teams if team in wanted]

    started = time.time()
    matches = []
    values_by_team = {team: [] for team in teams}
    number = 0
    pair_index = 0
    for i in range(len(teams)):
        for j in range(i + 1, len(teams)):
            this_pair = pair_index
            pair_index += 1
            if this_pair % shard_count != shard_index:
                continue
            team_a = teams[i]
            team_b = teams[j]
            if team_a in exclude or team_b in exclude:
                continue
            if args.focus and args.focus not in (team_a, team_b):
                continue
            try:
                va, vb, fa, fb, hands = play_match(team_a, team_b, deals, args.seed)
            except Exception as error:  # noqa: BLE001
                print(f"  SKIP {team_a} vs {team_b}: {type(error).__name__}: {str(error)[:90]}")
                continue
            if va is None:
                print(f"  SKIP {team_a} vs {team_b}: model load failed")
                continue
            mean_a, se_a = mean_and_se(va)
            bb_a = 100 * mean_a
            ci_a = 100 * ci95(se_a, len(va))
            v = verdict(bb_a, ci_a)
            number += 1
            values_by_team[team_a].extend(va)
            values_by_team[team_b].extend(vb)
            matches.append({
                "id": f"live-{this_pair + 1}",
                "kind": "group",
                "a": team_a,
                "b": team_b,
                "deals": deals,
                "hands": hands,
                "mean_bb": bb_a,
                "ci95_bb": ci_a,
                "verdict": v,
                "faults_a": fa,
                "faults_b": fb,
                "status": "complete",
                "finished_at": int(time.time()),
            })
            print(
                f"  [{number}] {team_a} vs {team_b}: {bb_a:+.1f} ± {ci_a:.1f} ({v}) faults {fa}/{fb}",
                flush=True,
            )

    standings, matrix = summarize(matches, values_by_team)
    payload = {
        "seed": args.seed,
        "deals": deals,
        "shard": shard_text,
        "teams": standings,
        "matches": matches,
        "matrix": matrix,
        "values": values_by_team,
    }
    out = args.out
    if not out:
        out = (
            os.path.join(ROOT, "data", "tournament_results.json")
            if shard_count == 1
            else os.path.join(ROOT, "data", f"tournament_shard_{shard_index}.json")
        )
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    print(f"wrote {out} ({len(matches)} matches) in {time.time() - started:.1f}s")


if __name__ == "__main__":
    main()
