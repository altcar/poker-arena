"""Play one fixed 6-player hand with the A-E models and dump the result.

Used to cross-check the JavaScript port in src/ against the real Python
engine. Run from the poker-arena folder:
    .venv/Scripts/python.exe scripts/python_play.py
"""

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "python"))

from card_utils import full_deck  # noqa: E402
from engine import play_hand  # noqa: E402
from model_a import Model as A  # noqa: E402
from model import Model as B  # noqa: E402  (the submitted goldman-stanley model)
from model_c import Model as C  # noqa: E402
from model_d import Model as D  # noqa: E402
from model_e import Model as E  # noqa: E402


def main():
    deck = full_deck()
    models = [A(), B(), C(), D(), E(), A()]
    teams = ["A", "B", "C", "D", "E", "A2"]
    result = play_hand(models, deck, [500] * 6, 0, 10, 20, teams)
    payload = {
        "net": result.net,
        "hands": [[str(c) for c in h] for h in result.hands],
        "board": [str(c) for c in result.board],
        "faults": {str(k): v for k, v in result.faults.items()},
        "actions": [
            {
                "player_id": a.player_id,
                "kind": a.kind,
                "amount": a.amount,
                "card": str(a.card) if a.card is not None else None,
            }
            for a in result.actions
        ],
    }
    out = os.path.join(ROOT, "data", "python_play.json")
    with open(out, "w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
