"""Merge the sharded tournament runs into data/tournament_results.json.

Usage:
  .venv/Scripts/python.exe scripts/merge_tournament.py
"""

import glob
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from run_tournament import summarize  # noqa: E402


def main():
    paths = sorted(glob.glob(os.path.join(ROOT, "data", "tournament_shard_*.json")))
    paths += sorted(glob.glob(os.path.join(ROOT, "data", "tournament_extra_*.json")))
    if not paths:
        raise SystemExit("no tournament shard/extra files to merge")

    matches = []
    seen_pairs = set()
    values_by_team = {}
    deals = None
    seed = None
    for path in paths:
        with open(path, encoding="utf-8") as handle:
            shard = json.load(handle)
        for match in shard.get("matches", []):
            key = (match["a"], match["b"])
            if key in seen_pairs:
                continue
            seen_pairs.add(key)
            matches.append(match)
        # Values are per match, so skip the values of a duplicate pair too.
        for team, values in (shard.get("values") or {}).items():
            values_by_team.setdefault(team, []).extend(values)
        deals = shard.get("deals", deals)
        seed = shard.get("seed", seed)
        print(f"  {os.path.basename(path)}: {len(shard.get('matches', []))} matches")

    matches.sort(key=lambda match: (match["a"], match["b"]))
    standings, matrix = summarize(matches, values_by_team)
    payload = {
        "seed": seed,
        "deals": deals,
        "teams": standings,
        "matches": matches,
        "matrix": matrix,
        "values": values_by_team,
    }
    out = os.path.join(ROOT, "data", "tournament_results.json")
    with open(out, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)
    print(f"wrote {out} ({len(matches)} matches, {len(standings)} teams)")


if __name__ == "__main__":
    main()
