"""
Real-time river subgame solver: range-vs-range CFR+ over hand-strength
buckets (the re-solving step of Libratus / DeepStack, for the last street).

Players are 0 = us (to act at the root) and 1 = them. Ranges are bucket
weight vectors over the same strength scale (bucket b beats every bucket
below it, ties within a bucket split). Utilities are our chip change from
the root. Pure Python, no globals with state; safe to pickle around.
"""

import time

BET_FRACS = (0.5, 1.0)


def build_tree(pot, owed, stack_us, stack_them):
    """Game tree from the root, where we act and owe `owed` (their bet is
    already in `pot`). Node: ["d", actor, [(name, add, child)]] or
    ["t", kind, pot_final, our_added]; kind "sd", "we_fold", "they_fold".
    """
    def node(actor, pot, owed, added, stacks, checked, raises):
        acts = []
        other = 1 - actor
        if owed == 0:
            nxt = ["t", "sd", pot, added[0]] if checked else \
                node(other, pot, 0, added, stacks, True, raises)
            acts.append(("check", 0, nxt))
            room = min(stacks)
            seen = set()
            for f in BET_FRACS:
                amt = int(pot * f)
                if amt >= 0.7 * room:
                    continue
                if amt > 0 and amt not in seen:
                    seen.add(amt)
                    acts.append(("bet", amt, _after_bet(
                        actor, pot, amt, added, stacks, raises)))
            if room > 0:
                acts.append(("allin", room, _after_bet(
                    actor, pot, room, added, stacks, raises)))
        else:
            if actor == 0:
                acts.append(("fold", 0, ["t", "we_fold", pot, added[0]]))
            else:
                acts.append(("fold", 0, ["t", "they_fold", pot, added[0]]))
            pay = min(owed, stacks[actor])
            a2 = list(added)
            a2[actor] += pay
            s2 = list(stacks)
            s2[actor] -= pay
            acts.append(("call", pay, ["t", "sd", pot + pay, a2[0]]))
            room = s2[actor]
            if raises < 2 and room > 0 and s2[other] > 0:
                total = pay + min(room, s2[other])
                acts.append(("allin", total, _after_bet(
                    actor, pot, total, added, stacks, raises, owed)))
        return ["d", actor, acts]

    def _after_bet(actor, pot, amt, added, stacks, raises, owed=0):
        a2 = list(added)
        a2[actor] += amt
        s2 = list(stacks)
        s2[actor] -= amt
        return node(1 - actor, pot + amt, amt - owed, a2, s2, False,
                    raises + 1)

    return node(0, pot, owed, [0, 0], [stack_us, stack_them], False,
                1 if owed else 0)


def _terminal(term, player, opp_reach, prefix, total):
    """Values per bucket of `player` at a terminal, opponent reach given.
    prefix[b] = sum of opp_reach below bucket b."""
    kind, pot, ours = term[1], term[2], term[3]
    theirs = pot - ours  # chips the other side put in, from the root pot
    n = len(opp_reach)
    if kind == "sd":
        # We win: pot - ours (they lose theirs = pot - ours). The pot
        # before the root is dead money, so the game is constant-sum.
        win_u, lose_u = (pot - ours, -ours) if player == 0 else \
            (ours, ours - pot)
        out = [0.0] * n
        for b in range(n):
            below = prefix[b]
            tie = opp_reach[b]
            above = total - below - tie
            out[b] = below * win_u + above * lose_u + \
                tie * 0.5 * (win_u + lose_u)
        return out
    if kind == "we_fold":
        u = -ours if player == 0 else ours
    else:
        u = pot - ours if player == 0 else -(pot - ours)
    return [total * u] * n


def solve(our_range, their_range, pot, owed, stack_us, stack_them,
          iters=400, deadline=None):
    """CFR+ on the river subgame. Returns (root action list, average root
    strategy per our bucket: list[bucket][action])."""
    tree = build_tree(pot, owed, stack_us, stack_them)
    n = len(our_range)
    regrets = {}
    avg = {}

    def walk(node, key):
        if node[0] == "t":
            return
        regrets[key] = [[0.0] * n for _ in node[2]]
        avg[key] = [[0.0] * n for _ in node[2]]
        for k, (_, _, child) in enumerate(node[2]):
            walk(child, key + (k,))

    walk(tree, ())

    def strat(key):
        R = regrets[key]
        m = len(R)
        out = [[0.0] * n for _ in range(m)]
        for b in range(n):
            s = 0.0
            for k in range(m):
                s += R[k][b]
            for k in range(m):
                out[k][b] = R[k][b] / s if s > 0 else 1.0 / m
        return out

    def cfr(node, key, player, opp_reach, my_reach, t):
        if node[0] == "t":
            prefix = [0.0] * len(opp_reach)
            acc = 0.0
            for b in range(len(opp_reach)):
                prefix[b] = acc
                acc += opp_reach[b]
            return _terminal(node, player, opp_reach, prefix, acc)
        actor, acts = node[1], node[2]
        sigma = strat(key)
        if actor == player:
            vals = []
            for k, (_, _, child) in enumerate(acts):
                nr = [m * s for m, s in zip(my_reach, sigma[k])]
                vals.append(cfr(child, key + (k,), player, opp_reach, nr, t))
            v = [0.0] * n
            for k in range(len(acts)):
                sk, vk = sigma[k], vals[k]
                for b in range(n):
                    v[b] += sk[b] * vk[b]
            R, A = regrets[key], avg[key]
            for k in range(len(acts)):
                rk, vk, ak, sk = R[k], vals[k], A[k], sigma[k]
                for b in range(n):
                    x = rk[b] + vk[b] - v[b]
                    rk[b] = x if x > 0 else 0.0
                    ak[b] += t * my_reach[b] * sk[b]
            return v
        v = [0.0] * n
        for k, (_, _, child) in enumerate(acts):
            nr = [o * s for o, s in zip(opp_reach, sigma[k])]
            vk = cfr(child, key + (k,), player, nr, my_reach, t)
            for b in range(n):
                v[b] += vk[b]
        return v

    for t in range(1, iters + 1):
        cfr(tree, (), 0, list(their_range), list(our_range), t)
        cfr(tree, (), 1, list(our_range), list(their_range), t)
        if deadline is not None and t % 10 == 0 and \
                time.perf_counter() > deadline:
            break
    A = avg[()]
    m = len(A)
    root = []
    for b in range(n):
        s = sum(A[k][b] for k in range(m))
        root.append([A[k][b] / s if s > 0 else 1.0 / m for k in range(m)])
    return [(name, add) for name, add, _ in tree[2]], root


def buckets(scores_us, w_us, scores_them, w_them, nb=30):
    """Maps both ranges onto nb shared strength buckets (equal combined
    mass). Returns (our bucket weights, their bucket weights, edges) where
    edges[b] is the lowest score in bucket b."""
    tu = sum(w_us) or 1.0
    tt = sum(w_them) or 1.0
    pts = [(s, w / tu) for s, w in zip(scores_us, w_us)] + \
        [(s, w / tt) for s, w in zip(scores_them, w_them)]
    pts.sort()
    total = sum(w for _, w in pts) or 1.0
    edges = []
    acc = 0.0
    step = total / nb
    i = 0
    last = None
    while i < len(pts):
        s = pts[i][0]
        if not edges or (acc >= step * len(edges) and s != last):
            edges.append(s)
        while i < len(pts) and pts[i][0] == s:
            acc += pts[i][1]
            i += 1
        last = s
    return edges


def bucket_of(score, edges):
    lo, hi = 0, len(edges) - 1
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if edges[mid] <= score:
            lo = mid
        else:
            hi = mid - 1
    return lo


def to_buckets(scores, weights, edges):
    out = [0.0] * len(edges)
    for s, w in zip(scores, weights):
        out[bucket_of(s, edges)] += w
    return out
