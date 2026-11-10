"""Play a batch of fixed 6-player hands with the A-E models and dump them.

Mirrors scripts/js_batch.mjs so the JavaScript port can be compared
hand-for-hand against the real Python engine.
"""

import json
import os
import random
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "python"))

from card_utils import full_deck  # noqa: E402
from engine import play_hand  # noqa: E402
from model_a import Model as A  # noqa: E402
from model import Model as B  # noqa: E402
from model_c import Model as C  # noqa: E402
from model_d import Model as D  # noqa: E402
from model_e import Model as E  # noqa: E402

HANDS = int(sys.argv[1]) if len(sys.argv) > 1 else 20
SEED = 1


def action_json(a):
    return {
        "player_id": a.player_id,
        "kind": a.kind,
        "amount": a.amount,
        "card": str(a.card) if a.card is not None else None,
    }


def main():
    hands = []
    for h in range(HANDS):
        deck = full_deck()
        random.Random(SEED * 1000003 + h).shuffle(deck)
        models = [A(), B(), C(), D(), E(), A()]
        teams = ["A", "B", "C", "D", "E", "A2"]
        result = play_hand(models, deck, [500] * 6, h % 6, 10, 20, teams)
        hands.append(
            {
                "hand": h,
                "button": h % 6,
                "net": result.net,
                "hands": [[str(c) for c in hand] for hand in result.hands],
                "board": [str(c) for c in result.board],
                "faults": {str(k): v for k, v in result.faults.items()},
                "actions": [action_json(a) for a in result.actions],
            }
        )
    out = os.path.join(ROOT, "data", "python_batch.json")
    with open(out, "w", encoding="utf-8") as file:
        json.dump({"seed": SEED, "hands": hands}, file)
    print(f"wrote {out} ({HANDS} hands)")


if __name__ == "__main__":
    main()
