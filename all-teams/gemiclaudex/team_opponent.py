"""Per-half opponent tracker and bounded exploit adjustments.

What can be observed (checked against arena/ and game/):
* The bot only learns anything inside ``act``. There is no hand-end callback.
* As BB, ``act`` is never called when the SB folds preflop, so SB folds and
  true VPIP are invisible. SB rates use "hands we saw" as the denominator.
* A fold to our raise is never shown, but it can be inferred: if our non-all-in
  preflop raise got no further ``act`` in that hand, the opponent folded. A
  fold to our all-in can't be told apart from a call, so it isn't counted.
* The bot restarts each half, so this object lives for about 100 hands, seeing
  the opponent in one role only.

Only frequency leaks are used, because hand composition is never revealed.
"""

from __future__ import annotations

from dataclasses import dataclass

EXPLOITS_ENABLED = True

MIN_OBS = 15
PRIOR_WEIGHT = 20.0

BASE_FOLD_TO_OPEN = 0.33
BASE_THREE_BET = 0.22
BASE_JAM_OVER_OPEN = 0.01
BASE_FOLD_TO_3BET = 0.45
BASE_FOUR_BET = 0.08
BASE_LIMP_SHARE = 0.15
BASE_FOLD_TO_ISO = 0.40
BASE_OPEN_JAM = 0.02
BASE_POST_BET = 0.15

# Assumed strength of a jam (top share of hands) before there is evidence.
PRIOR_JAM_RANGE = {"open": 0.08, "vs_open": 0.06, "deep": 0.05}
MIN_JAM_RANGE = 0.03

BIG_RAISE_SHARE = 1 / 3
BLINDS = ("post_small_blind", "post_big_blind")
RAISES = ("raise_to", "bet")


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def shrunk(count: int, seen: int, base: float) -> float:
    """Rate shrunk toward the baseline; exactly the baseline until MIN_OBS."""
    if seen < MIN_OBS:
        return base
    return (count + PRIOR_WEIGHT * base) / (seen + PRIOR_WEIGHT)


@dataclass(frozen=True)
class SbKnobs:
    """Adjustments when we are the SB. All values are bounded."""

    open_shift: float = 0.0  # -1 tight table .. +1 wide table
    open_size: int = 50
    value_size: int = 50
    limp_junk: bool = False
    fold_non_callers: bool = False
    bluff_mult: float = 1.0
    wide_value: float = 0.0
    call_wider: float = 0.0


@dataclass(frozen=True)
class BbKnobs:
    """Adjustments when we are the BB. All values are bounded."""

    bluff_mult: float = 1.0
    three_bet_mult: float = 4.0
    iso_shift: float = 0.0  # 0 base table .. 1 wide table
    iso_bluff_mult: float = 1.0
    iso_size: int = 70
    jam_lighter: float = 0.0


class OpponentModel:
    def __init__(self) -> None:
        self.c = {
            # Opponent as BB, after our open.
            "open_seen": 0, "open_fold": 0, "open_call": 0,
            "open_3bet": 0, "open_jam": 0,
            # Opponent as BB, after our 4-bet.
            "fourbet_seen": 0, "fourbet_fold": 0,
            # Opponent as SB: what it did first, on hands we saw.
            "sb_seen": 0, "sb_limp": 0, "sb_open": 0, "sb_jam": 0,
            "sb_open_chips": 0,
            # Opponent as SB, after our 3-bet.
            "tb_seen": 0, "tb_fold": 0, "tb_call": 0, "tb_4bet": 0,
            "tb_jam": 0,
            # Opponent as SB, after our iso-raise.
            "iso_seen": 0, "iso_fold": 0, "iso_call": 0, "iso_raise": 0,
            # Opponent's postflop actions, for the placeholder's read.
            "post_seen": 0, "post_bet": 0,
        }
        self._last_len = 0
        self._counted = 0
        self._sequence: list[str] = []
        self._our_last: tuple[str, bool] | None = None
        self._was_sb = False

    # ---- feeding ---------------------------------------------------------

    def observe(self, state) -> None:
        """Call once at the start of every ``act``."""
        history = state.action_history
        if len(history) <= self._last_len:
            self._finish_hand()
        self._was_sb = state.my_index == state.dealer_index
        me = state.my_player_id
        for record in history[self._counted:]:
            if record.action in BLINDS:
                continue
            mine = record.player_id == me
            if record.street != "preflop":
                if not mine:
                    self.c["post_seen"] += 1
                    self.c["post_bet"] += record.action in RAISES
                continue
            # "Big" matches the FACING_BIG node: a raise of 1/3 of the stack or
            # more is decided by equity, so it feeds the same range estimate.
            is_big = record.action in RAISES and (record.amount or 0) >= (
                self._full_stack(state) * BIG_RAISE_SHARE
            )
            self._count(record, mine, is_big)
        self._counted = len(history)
        self._last_len = len(history)

    def record_own(self, kind: str, all_in: bool, preflop: bool) -> None:
        """Remember our own last preflop action, to infer folds next hand."""
        self._our_last = (kind, all_in) if preflop else None

    @staticmethod
    def _full_stack(state) -> int:
        return max(
            stack + bet
            for stack, bet in zip(state.stack_sizes, state.current_bets)
        )

    def _count(self, record, mine: bool, is_big: bool) -> None:
        position = len(self._sequence)
        raised = record.action in RAISES
        self._sequence.append("r" if raised else record.action[0])
        if mine:
            return
        c = self.c
        if self._was_sb:
            # We are SB: opponent acts at even positions after our move.
            if position == 1 and self._sequence[0] == "r":
                c["open_seen"] += 1
                if is_big:
                    c["open_jam"] += 1
                elif raised:
                    c["open_3bet"] += 1
                else:
                    c["open_call"] += 1
            elif position == 3 and self._sequence[2] == "r":
                c["fourbet_seen"] += 1
            return
        # We are BB: the opponent acts first.
        if position == 0:
            c["sb_seen"] += 1
            if is_big:
                c["sb_jam"] += 1
            elif raised:
                c["sb_open"] += 1
                c["sb_open_chips"] += record.amount or 0
            else:
                c["sb_limp"] += 1
        elif position == 2 and self._sequence[1] == "r":
            if self._sequence[0] == "r":
                c["tb_seen"] += 1
                if is_big:
                    c["tb_jam"] += 1
                elif raised:
                    c["tb_4bet"] += 1
                else:
                    c["tb_call"] += 1
            else:
                c["iso_seen"] += 1
                if raised:
                    c["iso_raise"] += 1
                else:
                    c["iso_call"] += 1

    def _finish_hand(self) -> None:
        """Infer a fold when our last preflop raise was never answered."""
        sequence = self._sequence
        last = self._our_last
        if last is not None and last[0] == "raise" and not last[1]:
            c = self.c
            # ``sequence`` holds what we had seen before our raise, so the
            # raise itself sits at position len(sequence).
            position = len(sequence)
            if self._was_sb and position == 0:
                c["open_seen"] += 1
                c["open_fold"] += 1
            elif self._was_sb and position == 2 and sequence[1] == "r":
                c["fourbet_seen"] += 1
                c["fourbet_fold"] += 1
            elif not self._was_sb and position == 1:
                if sequence[0] == "r":
                    c["tb_seen"] += 1
                    c["tb_fold"] += 1
                else:
                    c["iso_seen"] += 1
                    c["iso_fold"] += 1
        self._sequence = []
        self._our_last = None
        self._counted = 0

    # ---- stats -----------------------------------------------------------

    def fold_to_open(self) -> float:
        c = self.c
        return shrunk(c["open_fold"], c["open_seen"], BASE_FOLD_TO_OPEN)

    def three_bet_rate(self) -> float:
        c = self.c
        return shrunk(c["open_3bet"] + c["open_jam"], c["open_seen"],
                      BASE_THREE_BET)

    def jam_over_open_rate(self) -> float:
        c = self.c
        return shrunk(c["open_jam"], c["open_seen"], BASE_JAM_OVER_OPEN)

    def fold_to_3bet(self) -> float:
        c = self.c
        return shrunk(c["tb_fold"], c["tb_seen"], BASE_FOLD_TO_3BET)

    def four_bet_rate(self) -> float:
        c = self.c
        return shrunk(c["tb_4bet"] + c["tb_jam"], c["tb_seen"], BASE_FOUR_BET)

    def limp_share(self) -> float:
        c = self.c
        return shrunk(c["sb_limp"], c["sb_seen"], BASE_LIMP_SHARE)

    def fold_to_iso(self) -> float:
        c = self.c
        return shrunk(c["iso_fold"], c["iso_seen"], BASE_FOLD_TO_ISO)

    def open_jam_rate(self) -> float:
        c = self.c
        return shrunk(c["sb_jam"], c["sb_seen"], BASE_OPEN_JAM)

    def postflop_bet_rate(self) -> float:
        c = self.c
        return shrunk(c["post_bet"], c["post_seen"], BASE_POST_BET)

    # ---- exploits --------------------------------------------------------

    def postflop_loosen(self) -> float:
        """0 when their postflop bets look like value, 1 when they spew.

        Calibrated on measured bet rates: @starter (value only) bets 13.6% of
        its postflop actions and @random 34.9%. The rate is shrunk toward
        0.15, so a real 35% reads about 0.27 after 30 actions.
        """
        if not EXPLOITS_ENABLED:
            return 0.0
        return _clamp((self.postflop_bet_rate() - 0.18) / 0.08)

    def jam_range(self, context: str) -> float:
        """Estimated share of hands (top-down) behind an all-in.

        Observed big-raise rates only widen the range past the prior, and the
        result is bounded, so a misread can't produce an absurd call.
        """
        prior = PRIOR_JAM_RANGE[context]
        if not EXPLOITS_ENABLED:
            return prior
        if context == "open":
            observed = self.open_jam_rate()
        elif context == "vs_open":
            observed = self.jam_over_open_rate()
        else:
            c = self.c
            observed = shrunk(c["tb_jam"], c["tb_seen"], prior)
        return _clamp(max(prior, observed), MIN_JAM_RANGE, 1.0)

    def sb_knobs(self) -> SbKnobs:
        if not EXPLOITS_ENABLED:
            return SbKnobs()
        fold = self.fold_to_open()
        three = self.three_bet_rate()
        over = _clamp((fold - BASE_FOLD_TO_OPEN - 0.12) / 0.35)
        station = _clamp((0.20 - fold) / 0.15) * _clamp((0.35 - three) / 0.15)
        maniac = _clamp((three - BASE_THREE_BET - 0.10) / 0.30)
        open_shift = _clamp(over - 0.7 * maniac, -1.0, 1.0)
        size = round(50 - 10 * over)
        fold_4bet = self.c["fourbet_fold"], self.c["fourbet_seen"]
        fold_rate = shrunk(fold_4bet[0], fold_4bet[1], 0.45)
        return SbKnobs(
            open_shift=open_shift,
            open_size=size,
            value_size=round(50 + 50 * station),
            limp_junk=station > 0.5,
            fold_non_callers=self.jam_over_open_rate() > 0.40,
            bluff_mult=_clamp(1.0 - station + (fold_rate - 0.45), 0.0, 1.6),
            wide_value=maniac,
            call_wider=0.5 * maniac,
        )

    def bb_knobs(self) -> BbKnobs:
        if not EXPLOITS_ENABLED:
            return BbKnobs()
        fold3 = self.fold_to_3bet()
        over = _clamp((fold3 - BASE_FOLD_TO_3BET - 0.10) / 0.30)
        sticky = _clamp((0.25 - fold3) / 0.15)
        fold_iso = self.fold_to_iso()
        iso_sticky = _clamp((0.25 - fold_iso) / 0.15)
        iso_over = _clamp((fold_iso - BASE_FOLD_TO_ISO - 0.10) / 0.30)
        limp_heavy = _clamp((self.limp_share() - 0.35) / 0.40)
        return BbKnobs(
            bluff_mult=_clamp(1.0 - sticky + 0.6 * over, 0.0, 1.6),
            three_bet_mult=4.0 + sticky - 0.3 * over,
            iso_shift=max(limp_heavy, iso_over),
            iso_bluff_mult=_clamp(1.0 - iso_sticky + 0.5 * iso_over, 0.0, 1.5),
            iso_size=round(70 + 30 * iso_sticky - 10 * iso_over),
            jam_lighter=_clamp((self.four_bet_rate() - 0.20) / 0.40),
        )
