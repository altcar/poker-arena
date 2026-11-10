"""Precompute the five-bot tournament with the real Python engine.

Plays a heads-up round robin (duplicate deals, seats swapped) between the
renamed A-E bots and writes the site's data files:
    public/data/teams.json
    public/data/matches.json
    public/data/tournament.json

Run: .venv/Scripts/python.exe scripts/make_tournament.py [deals]
"""

import json
import os
import random
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "python"))
sys.path.append(os.path.join(os.path.dirname(ROOT), "poker_bot"))

from card_utils import full_deck  # noqa: E402
from engine import play_hand  # noqa: E402
from model_a import Model as A  # noqa: E402
from model import Model as B  # noqa: E402
from model_c import Model as C  # noqa: E402
from model_d import Model as D  # noqa: E402
from model_e import Model as E  # noqa: E402
from evals.stats import ci95, mean_and_se, pool  # noqa: E402

DEALS = int(sys.argv[1]) if len(sys.argv) > 1 else 100
SEED = 20261110

BOTS = [
    {
        "key": "conservative",
        "name": "Conservative",
        "plan": "A",
        "tier": 1,
        "Model": A,
        "tagline": "Tight hand-strength heuristic. No Monte-Carlo, no opponent model.",
        "description": "The safest bot: a deterministic category heuristic with tight preflop ranges and no bluffs.",
    },
    {
        "key": "workhorse",
        "name": "Workhorse",
        "plan": "B",
        "tier": 2,
        "Model": B,
        "tagline": "The submitted model: preflop ranges plus Monte-Carlo equity.",
        "description": "The goldman-stanley submission: 25 bb ranges, equity versus random hands, pot-odds calls.",
    },
    {
        "key": "adaptive",
        "name": "Adaptive",
        "plan": "C",
        "tier": 3,
        "Model": C,
        "tagline": "Workhorse plus a live opponent model.",
        "description": "Per-team counters drive the equity range filter and switch the strategy archetype.",
    },
    {
        "key": "mixed",
        "name": "Mixed",
        "plan": "D",
        "tier": 4,
        "Model": D,
        "tagline": "Adaptive plus mixed strategies, board texture and MDF.",
        "description": "Blocker-aware bluffing, texture-driven sizing, small-bet defence and mixed frequencies.",
    },
    {
        "key": "aggressive",
        "name": "Aggressive",
        "plan": "E",
        "tier": 5,
        "Model": E,
        "tagline": "Maximum aggression: population exploits and overbet shoves.",
        "description": "Tighter value against stations, wide pressure against nits, traps against maniacs.",
    },
]

NAMES = [b["name"] for b in BOTS]


def play_match(bot_a, bot_b, deals, seed):
    """Duplicate heads-up match; returns per-deal values and faults."""
    model_a = bot_a["Model"]()
    model_b = bot_b["Model"]()
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
                teams = [bot_a["name"], bot_b["name"]]
            else:
                models = [model_b, model_a]
                teams = [bot_b["name"], bot_a["name"]]
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
    out_dir = os.path.join(ROOT, "public", "data")
    os.makedirs(out_dir, exist_ok=True)

    matches = []
    matrix = {name: {} for name in NAMES}
    runs = {name: [] for name in NAMES}
    faults = {name: 0 for name in NAMES}
    wins = {name: 0 for name in NAMES}
    losses = {name: 0 for name in NAMES}
    draws = {name: 0 for name in NAMES}
    opponents = {name: set() for name in NAMES}
    base_time = int(started)

    number = 0
    for i in range(len(BOTS)):
        for j in range(i + 1, len(BOTS)):
            bot_a = BOTS[i]
            bot_b = BOTS[j]
            values_a, values_b, faults_a, faults_b, hands = play_match(
                bot_a, bot_b, DEALS, SEED
            )
            mean_a, se_a = mean_and_se(values_a)
            mean_b, se_b = mean_and_se(values_b)
            bb_a = 100 * mean_a
            bb_b = 100 * mean_b
            ci_a = 100 * ci95(se_a, len(values_a))
            ci_b = 100 * ci95(se_b, len(values_b))
            v = verdict(bb_a, ci_a)

            number += 1
            matches.append(
                {
                    "id": f"group-{number}",
                    "job_id": number,
                    "kind": "group",
                    "a": bot_a["name"],
                    "b": bot_b["name"],
                    "group": "A",
                    "deals": DEALS,
                    "deals_completed": DEALS,
                    "rounds": 2,
                    "hands": hands,
                    "mean_bb": bb_a,
                    "ci95_bb": ci_a,
                    "verdict": v,
                    "faults_a": faults_a,
                    "faults_b": faults_b,
                    "disabled_a": None,
                    "disabled_b": None,
                    "status": "complete",
                    "finished_at": base_time + number,
                    "replay": None,
                }
            )
            matrix[bot_a["name"]][bot_b["name"]] = {"mean_bb": bb_a, "ci95_bb": ci_a}
            matrix[bot_b["name"]][bot_a["name"]] = {"mean_bb": bb_b, "ci95_bb": ci_b}
            runs[bot_a["name"]].append((len(values_a), mean_a, se_a))
            runs[bot_b["name"]].append((len(values_b), mean_b, se_b))
            faults[bot_a["name"]] += faults_a
            faults[bot_b["name"]] += faults_b
            opponents[bot_a["name"]].add(bot_b["name"])
            opponents[bot_b["name"]].add(bot_a["name"])
            if v == "a":
                wins[bot_a["name"]] += 1
                losses[bot_b["name"]] += 1
            elif v == "b":
                wins[bot_b["name"]] += 1
                losses[bot_a["name"]] += 1
            else:
                draws[bot_a["name"]] += 1
                draws[bot_b["name"]] += 1
            print(
                f"{bot_a['name']} vs {bot_b['name']}: {bb_a:+.1f} ± {ci_a:.1f} bb/100 ({v})",
                flush=True,
            )

    teams = []
    for bot in BOTS:
        name = bot["name"]
        count, mean, se = pool(runs[name])
        score = 100 * mean
        ci = 100 * ci95(se, count)
        teams.append(
            {
                "team": name,
                "plan": bot["plan"],
                "tier": bot["tier"],
                "tagline": bot["tagline"],
                "description": bot["description"],
                "score": score,
                "ci95": ci,
                "wins": wins[name],
                "draws": draws[name],
                "losses": losses[name],
                "opponents_played": len(opponents[name]),
                "opponents_expected": len(BOTS) - 1,
                "faults": faults[name],
                "hands": sum(DEALS * 2 for _ in opponents[name]),
            }
        )
    teams.sort(key=lambda t: (-t["score"], t["team"]))
    for rank, team in enumerate(teams, 1):
        team["rank"] = rank

    teams_payload = {
        "teams": [
            {
                "team": bot["name"],
                "status": "submitted",
                "group": "A",
                "finalist": False,
                "plan": bot["plan"],
                "tier": bot["tier"],
                "tagline": bot["tagline"],
                "description": bot["description"],
            }
            for bot in BOTS
        ] + [
            {
                "team": "You",
                "status": "you",
                "group": None,
                "finalist": False,
                "plan": None,
                "tier": None,
                "tagline": "The human player.",
                "description": "You are playing live at the 6-player table.",
            }
        ],
        "registrations": [],
    }

    tournament = {
        "phase": "done",
        "rounds": 1,
        "advance": 0,
        "groups": [
            {
                "name": "A",
                "teams": NAMES,
                "board": {
                    "kind": "group",
                    "provisional": False,
                    "pairs_total": len(matches),
                    "pairs_done": len(matches),
                    "teams": teams,
                    "matrix": {
                        me: {other: matrix[me].get(other) for other in NAMES if other != me}
                        for me in NAMES
                    },
                },
            }
        ],
        "finals": None,
        "qualified": [],
        "champion": teams[0]["team"],
    }

    with open(os.path.join(out_dir, "teams.json"), "w", encoding="utf-8") as f:
        json.dump(teams_payload, f, indent=2)
    with open(os.path.join(out_dir, "matches.json"), "w", encoding="utf-8") as f:
        json.dump(matches, f, indent=2)
    with open(os.path.join(out_dir, "tournament.json"), "w", encoding="utf-8") as f:
        json.dump(tournament, f, indent=2)
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(
            {
                "schema": 1,
                "title": "Poker Arena",
                "submissions": {"status": "closed", "why": "over"},
                "mode": "tournament",
                "phase": "done",
                "teams": len(teams_payload["teams"]),
                "generated_at": base_time,
            },
            f,
            indent=2,
        )

    # The worker imports the same data as a module.
    data_js = os.path.join(ROOT, "src", "tournament_data.js")
    with open(data_js, "w", encoding="utf-8") as f:
        f.write("// Generated by scripts/make_tournament.py - do not edit by hand.\n")
        f.write("export const tournament = ")
        f.write(json.dumps(tournament, separators=(",", ":")))
        f.write(";\n")
        f.write("export const matches = ")
        f.write(json.dumps(matches, separators=(",", ":")))
        f.write(";\n")
        f.write("export const teams = ")
        f.write(json.dumps(teams_payload, separators=(",", ":")))
        f.write(";\n")
    print(f"wrote {data_js}")
    print(f"wrote {out_dir} in {time.time() - started:.1f}s")


if __name__ == "__main__":
    main()
