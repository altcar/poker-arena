import os
import pickle
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE = os.path.join(ROOT, "all-teams")
sys.path.insert(0, os.path.join(ROOT, "python"))

for team in sorted(os.listdir(BASE)):
    folder = os.path.join(BASE, team)
    if not os.path.isdir(folder) or team in ("conservative", "workhorse", "adaptive", "mixed", "aggressive"):
        continue
    pkls = [name for name in os.listdir(folder) if name.endswith(".pkl")]
    if not pkls:
        print(f"{team}: NO PKL")
        continue
    # Prefer the pickle named after the team, else the first.
    preferred = [p for p in pkls if os.path.splitext(p)[0].lower().replace("_", "-") == team.lower().replace("_", "-")]
    pkl = preferred[0] if preferred else pkls[0]
    sys.path.insert(0, folder)
    sys.path.insert(0, os.path.join(ROOT, "python"))
    try:
        # Import every local module so unpickling can resolve the classes.
        for name in os.listdir(folder):
            if name.endswith(".py") and name != "model.py":
                try:
                    __import__(name[:-3])
                except Exception:
                    pass
        __import__("model")
        with open(os.path.join(folder, pkl), "rb") as handle:
            model = pickle.load(handle)
        print(f"{team}: OK ({pkl})")
    except Exception as error:  # noqa: BLE001
        print(f"{team}: FAIL ({pkl}) {type(error).__name__}: {str(error)[:140]}")
    for mod in list(sys.modules):
        if mod in {
            "model", "pbbrain", "pbcore", "pbpre", "pb_cards", "pb_equity",
            "pb_opponents", "pb_preflop", "fasteval", "pf_table", "pf_tables",
            "pf_data", "preflop_table", "preflop_tables", "preflop_data",
            "preflop_ranges", "equity_engine", "postflop_policy", "preflop_policy",
            "sidepot_equity", "all_in_equity", "features", "poker_eval",
            "pokey_strategy", "hand_eval", "handeval", "pokerlib", "policy",
            "botcore", "brain", "ranges", "equity", "cards", "decision",
            "opponents", "policy_net", "policy_weights", "strength", "tables",
            "evaluator", "strategy", "tracker", "bot_brain", "bot_decide",
            "bot_eval", "bot_opp", "bot_preflop", "bot_tables", "poker_math",
            "river_policy", "poker_strength", "pk_cards", "pk_equity",
            "pk_estimate", "pk_eval", "pk_opponent", "pk_postflop", "pk_preflop",
            "pk_ranges", "pk_tables", "core", "opp", "policy_v1", "blueprint",
            "translation", "hunter_chart", "hunter_eval", "hand_history",
            "bucket_data", "buckets", "arena_sdk", "arena_translate", "combo_bot",
            "core_allin", "core_equity", "core_interface", "main_allin",
            "main_bot", "main_fallback", "main_opponent", "main_postflop",
            "team_equity_table", "team_opponent", "team_preflop", "team_ranges",
        }:
            sys.modules.pop(mod, None)
    sys.path = [p for p in sys.path if p not in (folder, os.path.join(ROOT, "python"))]
