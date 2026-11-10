import random
import time
import itertools
from collections import Counter

from card_utils import Card
from game_state import GameStateView
from player_action import PlayerAction
# cards: ints 0..51, rank=c//4 (0=2..12=A), suit=c%4
def score5(r):
    c=Counter(r); g=sorted(c.items(),key=lambda x:(-x[1],-x[0])); s=[x[0] for x in g]; k=[x[1] for x in g]
    return k,s
def best7(cards):
    best=0
    for comb in itertools.combinations(cards,5):
        v=h5(comb)
        if v>best:best=v
    return best
def h5(comb):
    rs=sorted((c//4 for c in comb),reverse=True); fl=len({c%4 for c in comb})==1
    k,s=score5(rs); st=0
    if len(s)==5:
        if rs[0]-rs[4]==4: st=rs[0]+1
        elif rs==[12,3,2,1,0]: st=4
    if st and fl:cat=8
    elif k[0]==4:cat=7
    elif k[:2]==[3,2]:cat=6
    elif fl:cat=5
    elif st:cat=4;s=[st]
    elif k[0]==3:cat=3
    elif k[:2]==[2,2]:cat=2
    elif k[0]==2:cat=1
    else:cat=0
    if st and fl:s=[st]
    v=cat
    for x in s+[0]*(5-len(s)):v=v*16+x
    return v
def equity(hole,board,n=300,tl=1.5):
    t=time.time(); dead=set(hole)|set(board); deck=[c for c in range(52) if c not in dead]
    w=0;cnt=0
    for i in range(n):
        if i%20==0 and time.time()-t>tl:break
        d=random.sample(deck,2+5-len(board))
        opp=d[:2];full=list(board)+d[2:]
        a=best7(list(hole)+full);b=best7(opp+full)
        w+=1 if a>b else .5 if a==b else 0;cnt+=1
    return w/max(cnt,1)

EQ_N = 200
TIME_CAP = 0.8  # seconds of equity simulation per decision (limit is 10)
RANKS = "23456789TJQKA"
SUITS = "CDHS"


def to_int(card):
    return RANKS.index(card.rank) * 4 + SUITS.index(card.suit)


class Model:
    """Equity-based heads-up bot with a simple opponent-aggression model."""

    def __init__(self):
        self.hand = []
        self.player_id = None
        self.bets = 2.0
        self.seen = 6.0
        self.raised = False

    def seed(self, hand, player_id):
        self.hand = hand
        self.player_id = player_id
        self.raised = False

    def run(self, actions, game_state):
        me = self.player_id
        try:
            act, amt = self._decide(game_state)
            if act == "raise" and game_state.can_raise:
                lo, hi = game_state.raise_range
                amt = int(max(lo, min(hi, amt)))
                self.raised = True
                return PlayerAction.raise_to(me, amt)
            if act == "fold" and game_state.call_amount > 0:
                return PlayerAction.fold(me)
            return PlayerAction.call(me)
        except Exception:
            if game_state.call_amount == 0:
                return PlayerAction.call(me)
            return PlayerAction.fold(me)

    def end_game(self, actions, game_state):
        pass

    def _decide(self, g):
        me = self.player_id
        hole = [to_int(c) for c in self.hand]
        board = [to_int(c) for c in g.board]
        tc = g.call_amount
        stack = g.stacks[me]
        bb = g.big_blind
        pot = g.pot
        can = g.can_raise
        mn, mx = (g.raise_range if can else (0, 0))
        eq = equity(hole, board, n=EQ_N, tl=TIME_CAP)

        def rt(f):
            return int(max(mn, min(mx, mn + pot * f)))

        pre = len(board) == 0
        if not pre:
            self.seen += 1
            if tc > 0:
                self.bets += 1
        agg = self.bets / self.seen
        if tc == 0:
            if can and eq > .65:
                return ("raise", rt(.7))
            if can and pre and eq > .57:
                return ("raise", rt(.3))
            if can and eq > .5 and random.random() < .4:
                return ("raise", rt(.5))
            if can and not pre and agg < .5 and random.random() < .08:
                return ("raise", rt(.6))
            return ("call", 0)
        odds = tc / (pot + tc)
        frac = tc / max(pot - tc, 1)
        margin = .03 + min(.14, .07 * frac)
        if agg > .55:
            margin -= .04
        elif agg < .25:
            margin += .03
        if tc >= 0.4 * (stack + tc):
            margin = max(margin, .16 if agg <= .55 else .1)
        big = tc >= 0.25 * (stack + tc)
        if pre and tc <= bb / 2 and can and eq > .5 and random.random() < .8:
            return ("raise", rt(.5))
        thr = .92 if (big or len(board) == 5) else .82
        if eq > thr and can:
            return ("raise", rt(1.0))
        if eq > .68 and can and not big and random.random() < .4:
            return ("raise", rt(.7))
        if frac < .4:
            margin -= .03
        if self.raised and not pre and big and eq < .8:
            return ("fold", 0)
        if eq > odds + margin:
            return ("call", 0)
        if can and frac < .5 and random.random() < .08:
            return ("raise", rt(.7))
        return ("fold", 0)