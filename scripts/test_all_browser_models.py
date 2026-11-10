import csv
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "public", "py"))

import shim  # noqa: E402

sys.modules["card_utils"] = shim
sys.modules["player_action"] = shim
sys.modules["game_state"] = shim

STATE = {
    "button": 0,
    "small_blind": 10,
    "big_blind": 20,
    "stacks": [490, 480],
    "bets": [10, 20],
    "folded": [False, False],
    "acted": [False, False],
    "board": [],
    "street": "preflop",
    "pot": 30,
    "last_raise": 20,
    "to_act": 0,
    "teams": ["hero", "villain"],
}

rows = list(csv.DictReader(open(os.path.join(ROOT, "data", "team_files.csv"), encoding="utf-8")))
failures = []
ok = 0
for row in rows:
    team = row["team"]
    if row["status"] != "ok":
        continue
    folder = os.path.join(ROOT, "all-teams", team)
    if not os.path.isdir(folder):
        continue
    result = json.loads(shim.load_team(team, folder, row.get("pkl") or ""))
    if not result.get("ok"):
        failures.append((team, "load", result.get("error")))
        print(f"{team}: LOAD FAIL {result.get('error')}")
        continue
    try:
        shim.seed(team, json.dumps(["AS", "KD"]), 0)
        action = json.loads(shim.run(team, json.dumps(STATE), "[]"))
        if action.get("kind") not in ("fold", "call", "raise"):
            failures.append((team, "action", action))
            print(f"{team}: BAD ACTION {action}")
        else:
            ok += 1
    except Exception as error:
        failures.append((team, "run", f"{type(error).__name__}: {error}"))
        print(f"{team}: RUN FAIL {type(error).__name__}: {error}")

print(f"\nok: {ok}, failures: {len(failures)}")
for failure in failures:
    print("  ", failure)
