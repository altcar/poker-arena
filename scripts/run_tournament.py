"""Run a heads-up round robin between all available bots and write the
results to data/tournament_results.json.

Usage: .venv/Scripts/python.exe scripts/run_tournament.py [deals]
"""

import json
import os
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

DEALS = int(sys.argv[1]) if len(sys.argv) > 1 else 20
SEED = 20261111

# The 5 fork bots use the shared engine; the competition teams use their own.
FORK_BOTS = {
    "conservative": ("model_a.py", "Model"),
    "workhorse": ("model.py", "Model"),
    "adaptive": ("model_c.py", "Model"),
    "mixed": ("model_d.py", "Model"),
    "aggressive": ("model_e.py", "Model"),
}


def load_model(team):
    """Load a team's Model class. Returns (ModelClass, folder) or (None, error)."""
    folder = os.path.join(ROOT, "all-teams", team)
    if not os.path.isdir(folder):
        return None, "no folder"
    # Fork bots: use the python/ folder
    if team in FORK_BOTS:
        # Clear cached team modules, but keep the shared engine modules so the
        # PlayerAction class identity stays the same for the engine.
        for mod in list(sys.modules):
            if mod in ("model", "model_a", "model_b", "model_c", "model_d", "model_e",
                       "policy", "botcore"):
                try:
                    del sys.modules[mod]
                except KeyError:
                    pass
        sys.path.insert(0, os.path.join(ROOT, "python"))
        try:
            import model as m
            return m.Model, folder
        except Exception as e:
            return None, f"import error: {e}"
    # Competition teams: use their own folder + python/ for reserved modules
    for mod in list(sys.modules):
        if mod in ("model", "pbbrain", "pbcore", "pbpre", "pb_cards", "pb_equity",
                   "pb_opponents", "pb_preflop", "fasteval", "pf_table", "pf_tables",
                   "equity_engine", "postflop_policy", "preflop_policy", "sidepot_equity",
                   "all_in_equity", "features", "poker_eval", "pokey_strategy", "hand_eval",
                   "pokerlib"):
            try:
                del sys.modules[mod]
            except KeyError:
                pass
    sys.path.insert(0, os.path.join(ROOT, "python"))
    sys.path.insert(0, folder)
    try:
        import model as m
        return m.Model, folder
    except Exception as e:
        return None, f"import error: {e}"
    finally:
        sys.path = [p for p in sys.path if p not in (folder, os.path.join(ROOT, "python"))]


def play_match(team_a, team_b, deals, seed):
    """Duplicate heads-up match. Returns (values_a, values_b, faults_a, faults_b, hands)."""
    model_a_cls, _ = load_model(team_a)
    model_b_cls, _ = load_model(team_b)
    if model_a_cls is None or model_b_cls is None:
        return None, None, 0, 0, 0
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


def verdict(mean, half):
    if mean is None or half is None:
        return "tie"
    if mean - half > 0:
        return "a"
    if mean + half < 0:
        return "b"
    return "tie"


def main():
    started = time.time()
    teams = sorted(os.listdir(os.path.join(ROOT, "all-teams")))
    teams = [t for t in teams if os.path.isdir(os.path.join(ROOT, "all-teams", t))]

    matches = []
    number = 0
    for i in range(len(teams)):
        for j in range(i + 1, len(teams)):
            team_a = teams[i]
            team_b = teams[j]
            try:
                va, vb, fa, fb, hands = play_match(team_a, team_b, DEALS, SEED)
            except Exception as error:
                print(f"  SKIP {team_a} vs {team_b}: {type(error).__name__}: {str(error)[:90]}")
                continue
            if va is None:
                print(f"  SKIP {team_a} vs {team_b}: model load failed")
                continue
            mean_a, se_a = mean_and_se(va)
            mean_b, se_b = mean_and_se(vb)
            bb_a = 100 * mean_a
            bb_b = 100 * mean_b
            ci_a = 100 * ci95(se_a, len(va))
            ci_b = 100 * ci95(se_b, len(vb))
            v = verdict(bb_a, ci_a)
            number += 1
            matches.append({
                "id": f"live-{number}",
                "kind": "group",
                "a": team_a,
                "b": team_b,
                "deals": DEALS,
                "hands": hands,
                "mean_bb": bb_a,
                "ci95_bb": ci_a,
                "verdict": v,
                "faults_a": fa,
                "faults_b": fb,
                "status": "complete",
                "finished_at": int(time.time()),
            })
            print(f"  [{number}] {team_a} vs {team_b}: {bb_a:+.1f} ± {ci_a:.1f} ({v}) faults {fa}/{fb}", flush=True)

    out = os.path.join(ROOT, "data", "tournament_results.json")
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"seed": SEED, "deals": DEALS, "matches": matches}, f, indent=2)
    print(f"wrote {out} ({len(matches)} matches) in {time.time() - started:.1f}s")


if __name__ == "__main__":
    main()
