import sys
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "python"))

base = os.path.join(ROOT, "all-teams")
teams = sorted(os.listdir(base))

for team in teams:
    folder = os.path.join(base, team)
    if not os.path.isdir(folder):
        continue
    # skip the 5 fork bots (they use the shared engine)
    if team in ("conservative", "workhorse", "adaptive", "mixed", "aggressive"):
        continue
    try:
        # clear module cache for team modules
        for mod in list(sys.modules):
            if mod in ("model", "pbbrain", "pbcore", "pbpre", "pb_cards", "pb_equity",
                       "pb_opponents", "pb_preflop", "fasteval", "pf_table", "pf_tables",
                       "equity_engine", "postflop_policy", "preflop_policy", "sidepot_equity",
                       "all_in_equity", "features", "poker_eval", "pokey_strategy", "hand_eval",
                       "model", "pokerlib"):
                del sys.modules[mod]
        sys.path.insert(0, folder)
        sys.path.insert(0, os.path.join(ROOT, "python"))
        import model
        m = model.Model()
        print(f"{team}: OK  {type(m).__name__}")
        sys.path = [p for p in sys.path if p not in (folder, os.path.join(ROOT, "python"))]
        sys.path.insert(0, os.path.join(ROOT, "python"))
        del sys.modules["model"]
    except Exception as e:
        print(f"{team}: FAIL  {type(e).__name__}: {str(e)[:90]}")
        sys.path = [p for p in sys.path if p not in (folder, os.path.join(ROOT, "python"))]
        sys.path.insert(0, os.path.join(ROOT, "python"))
        for mod in list(sys.modules):
            if mod in ("model", "pbbrain", "pbcore", "pbpre", "pb_cards", "pb_equity",
                       "pb_opponents", "pb_preflop", "fasteval", "pf_table", "pf_tables",
                       "equity_engine", "postflop_policy", "preflop_policy", "sidepot_equity",
                       "all_in_equity", "features", "poker_eval", "pokey_strategy", "hand_eval",
                       "model", "pokerlib"):
                try:
                    del sys.modules[mod]
                except KeyError:
                    pass
