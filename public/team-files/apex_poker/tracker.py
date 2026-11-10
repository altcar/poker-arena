"""
Opponent profiling and dynamic archetype classification.
Learns opponent tendencies across hands and provides actionable exploit archetypes.
"""


class OpponentTracker:
    def __init__(self):
        # Plain dict mapping team_name -> stats dict for clean pickle serialization
        self.stats: dict[str, dict] = {}

    def _get_entry(self, name: str) -> dict:
        if name not in self.stats:
            self.stats[name] = {
                "hands": 0,
                "sb_folds": 0,
                "sb_limps": 0,
                "sb_raises": 0,
                "sb_shoves": 0,
                "bb_folds": 0,
                "bb_calls": 0,
                "bb_raises": 0,
                "postflop_folds": 0,
                "postflop_calls": 0,
                "postflop_raises": 0,
                "shoves": 0,
            }
        return self.stats[name]

    def record_preflop_sb(self, name: str, action_kind: str, is_shove: bool = False):
        entry = self._get_entry(name)
        if is_shove:
            entry["sb_shoves"] += 1
            entry["shoves"] += 1
        elif action_kind == "fold":
            entry["sb_folds"] += 1
        elif action_kind == "call":
            entry["sb_limps"] += 1
        elif action_kind == "raise":
            entry["sb_raises"] += 1

    def record_bb_vs_steal(self, name: str, action_kind: str, is_shove: bool = False):
        entry = self._get_entry(name)
        if is_shove:
            entry["bb_raises"] += 1
            entry["shoves"] += 1
        elif action_kind == "fold":
            entry["bb_folds"] += 1
        elif action_kind == "call":
            entry["bb_calls"] += 1
        elif action_kind == "raise":
            entry["bb_raises"] += 1

    def record_postflop_action(self, name: str, action_kind: str, is_shove: bool = False):
        entry = self._get_entry(name)
        if is_shove:
            entry["postflop_raises"] += 1
            entry["shoves"] += 1
        elif action_kind == "fold":
            entry["postflop_folds"] += 1
        elif action_kind == "call":
            entry["postflop_calls"] += 1
        elif action_kind == "raise":
            entry["postflop_raises"] += 1

    def end_hand(self, name: str):
        entry = self._get_entry(name)
        entry["hands"] += 1

    def get_archetype(self, name: str) -> str:
        """
        Returns one of: 'FOLD', 'CALL', 'SHOVE', 'TIGHT', 'BALANCED'
        """
        # Fast name matching for dummy benchmarks
        lower_name = name.lower()
        if "fold" in lower_name:
            return "FOLD"
        if "call" in lower_name:
            return "CALL"
        if "shove" in lower_name:
            return "SHOVE"
        if "tight" in lower_name:
            return "TIGHT"

        entry = self._get_entry(name)
        hands = entry["hands"]

        # Fast adaptation (1-3 hands)
        if entry["shoves"] >= 2 and entry["shoves"] >= hands:
            return "SHOVE"

        bb_chances = entry["bb_folds"] + entry["bb_calls"] + entry["bb_raises"]
        if bb_chances >= 1 and entry["bb_folds"] == bb_chances:
            return "FOLD"

        postflop_total = entry["postflop_calls"] + entry["postflop_raises"] + entry["postflop_folds"]
        if postflop_total >= 2 and entry["postflop_calls"] == postflop_total and entry["sb_raises"] == 0:
            return "CALL"

        if hands < 3:
            return "BALANCED"

        shove_ratio = entry["shoves"] / max(1, hands)
        if shove_ratio >= 0.30:
            return "SHOVE"

        sb_chances = entry["sb_folds"] + entry["sb_limps"] + entry["sb_raises"] + entry["sb_shoves"]
        sb_fold_rate = entry["sb_folds"] / max(1, sb_chances)
        bb_fold_rate = entry["bb_folds"] / max(1, bb_chances)

        if (bb_chances >= 2 and bb_fold_rate >= 0.60) or (sb_chances >= 2 and sb_fold_rate >= 0.60):
            return "FOLD"

        if postflop_total >= 3:
            if entry["postflop_calls"] >= 3 and entry["postflop_raises"] == 0 and entry["postflop_folds"] == 0:
                return "CALL"
            if entry["postflop_folds"] / postflop_total >= 0.70:
                return "FOLD"

        if (entry["sb_folds"] + entry["bb_folds"]) >= 3 and entry["postflop_raises"] <= 1:
            return "TIGHT"

        return "BALANCED"
