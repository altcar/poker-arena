// The Stack: articles about the arena, the formats and building bots.

export const STACK = [
  {
    slug: "how-a-match-is-run",
    title: "How a match is actually run",
    author: "Arena",
    date: "2026-11-16",
    tag: "Format",
    excerpt:
      "Duplicate deals, swapped seats and bb/100 with a confidence interval: why one lucky table is never enough.",
    body: `## One table is not a result

A poker bot can win a table by being lucky. It cannot win 122 tables in a row by being lucky. That is the whole reason the arena uses **duplicate deals**.

Two bots play the same decks twice, once in each seat. If bot A wins 300 chips on a deck and bot B wins 280 on the same deck, the deck is removed from the comparison and the difference is what the bots actually did with it.

## bb/100

Every result is reported in big blinds per 100 hands. A result of +12.4 bb/100 means the bot won 12.4 big blinds per 100 hands on average. The unit matters: a bot that wins 100 chips at 10/20 and a bot that wins 100 chips at 50/100 are not the same.

## The interval

The second number is a 95% confidence interval. If a bot's score is +8.1 with a ±6.0 interval, the true result is probably between +2.1 and +14.1. If the interval crosses zero, the match is a tie for ranking purposes.

This is why the round robin runs ten deals per pairing rather than one. Ten deals still leaves a wide interval; more deals make it narrower, slowly. The interval shrinks with the square root of the sample size, so four times the hands buys you half the uncertainty.

## Seats and buttons

Seat position is an advantage. The button acts last after the flop and gets to see what everyone does first. Duplicate deals swap seats so that advantage cancels out across the pairing.

## What counts as a fault

Each bot gets one call per decision. If it throws, returns an illegal action or times out, the arena records a fault and falls back to a fold (or a check when checking is free). Faults are shown next to every result: a bot with a good score and 40 faults is not a good bot, it is a bot that got lucky with the fallback.`,
  },
  {
    slug: "your-first-bot",
    title: "Your first bot: the Model interface",
    author: "Arena",
    date: "2026-11-14",
    tag: "Development",
    excerpt:
      "Six methods, one file, no framework. Here is the smallest bot that can sit at an arena table.",
    body: `## The interface

An uploaded bot is a Python file with a \`Model\` class. The arena creates one instance, then calls it once per hand and once per decision.

\`\`\`python
from card_utils import Card
from player_action import PlayerAction

class Model:
    def seed(self, hand, player_id):
        # hand: list of two Card objects for this hand
        # player_id: your seat at the table
        self.hand = hand
        self.player_id = player_id

    def run(self, actions, state):
        # actions: every action since your last decision
        # state: read-only view of the table
        if state.call_amount == 0:
            return PlayerAction.call(self.player_id)
        return PlayerAction.fold(self.player_id)

    def end_game(self, actions, state):
        pass
\`\`\`

That bot checks when it can and folds when it cannot. It will not win, but it will never fault, and that is the first thing to get right.

## Reading the state

The state view exposes the table exactly as the engine sees it:

- \`state.street\` — preflop, flop, turn or river
- \`state.board\` — the community cards
- \`state.stacks\`, \`state.bets\`, \`state.folded\`
- \`state.to_act\`, \`state.call_amount\`, \`state.pot\`
- \`state.min_raise_to\`, \`state.max_raise_to\`
- \`state.teams\` — the names at the table

Card objects have \`.rank\` and \`.suit\`, and \`str(card)\` gives you something like \`AS\`.

## Raising

\`\`\`python
PlayerAction.raise_to(self.player_id, amount)
\`\`\`

The amount is the total you are raising **to**, not the amount you are adding. The engine will reject a raise below \`state.min_raise_to\` or above \`state.max_raise_to\`. Clamp it yourself before returning.

## The three rules

1. Never return an illegal action. A fault is worse than a bad fold.
2. Do not do heavy work on every decision. Monte-Carlo is fine, but cache what you can.
3. Keep state between hands in plain data. Your instance survives the whole session; the engine does not reset it.

Upload the file on the **Upload** page, then run a rated challenge from your account. The arena runs the hands in your browser and posts the result to your bot's record.`,
  },
  {
    slug: "ofc-fouls",
    title: "Pineapple OFC: fouls are the whole game",
    author: "Arena",
    date: "2026-11-12",
    tag: "Strategy",
    excerpt:
      "A bot that never fouls beats a greedy bot that scores one extra royalty and then loses the board.",
    body: `## The constraint

Your bottom row must be at least as strong as your middle, and your middle at least as strong as your top. Everything else is secondary. A fouled board scores nothing, and in this arena a foul hands the opponent all three rows.

That single rule changes the game. In Hold'em you can chase a draw and miss. In OFC, a missed draw can invalidate eleven correctly placed cards.

## Placement order

Place your strongest cards in the bottom first. The bottom has five slots, the most room, and the highest royalty ceiling. The middle takes the next tier. The top is the last priority: a pair of sixes or better is a bonus, not a plan.

## The classic mistake

Putting a big pair on top early because it looks strong. Now the middle and bottom both need to beat that pair, and you have ten cards left to do it. It is possible, but you have turned a flexible board into a puzzle.

A safer top is high-card junk: K, Q, J in mixed suits. It leaves the middle and bottom free to be strong.

## Royalties

The top pays for pairs from 66 upwards and for trips. The middle and bottom pay for trips, straights, flushes, full houses, quads and straight flushes. Bottom royalties are smaller than middle royalties because bottom hands are easier to make.

That is why a bot should push strength downward: it is easier to make a flush in the bottom without violating the row order, and the middle can then hold a straight or trips.

## What the house bot does

The arena's OFC bot scores every legal placement with a simple heuristic: estimated row strength, royalty potential, and a foul penalty that grows as the board fills up. It is not a solver, but it never intentionally fouls. Against a greedy opponent that is usually enough.`,
  },
  {
    slug: "draw27-unlearning",
    title: "2-7 Triple Draw: unlearning Hold'em",
    author: "Arena",
    date: "2026-11-10",
    tag: "Strategy",
    excerpt:
      "Flushes are traps, aces are liabilities, and 7-5-4-3-2 is the best hand in the game. Welcome to lowball.",
    body: `## The target

The best hand in 2-7 Triple Draw is **7-5-4-3-2** in mixed suits. Not a straight, not a flush, not a pair: five different ranks, no ace, no six, no straight, no flush.

The ranking is the inverse of normal poker. High card is the best category. A pair is worse than any unpaired hand. A straight is worse than trips. A flush is worse than a full house. An ace always plays high, so A-2-3-4-5 is an ace-high straight and one of the worst straights you can make.

## Drawing

You get three draws. After each draw there is a betting round. You can throw away any number of cards from zero to five. Your opponent sees how many you took, never which ones.

That information matters. Taking three cards says you were holding junk. Standing pat says you already have a hand, or that you want your opponent to think so.

## Betting

This is fixed limit, not no limit. One bet size per round: one big blind before the first draw and after it, two big blinds after the second and third draws. A round is capped at four bets.

Fixed limit changes the maths. You cannot price out a draw with an all-in. You call because the pot is laying you a price, and the pot is usually laying you a good one.

## A simple bot plan

1. Keep 2, 3, 4, 5, 6 and 7. Discard 8, 9, T, J, Q, K, A unless you have a made hand.
2. Break straights and flushes. They look strong and they are the wrong direction.
3. Draw to a seven or an eight. A rough eight is still a favourite against a three-card draw.
4. Stand pat with any seven. Do not get greedy trying to improve 7-6-4-3-2.
5. Bet your made sevens. Call with eights. Fold the rest when the price is bad.`,
  },
  {
    slug: "reading-the-ladder",
    title: "Reading the ladder",
    author: "Arena",
    date: "2026-11-08",
    tag: "Arena",
    excerpt:
      "Open, Raised and Elite are not cosmetic. Here is how a bot moves between divisions.",
    body: `## Three divisions

Every bot sits in one of three divisions:

- **Open** — below 1200 rating. New uploads start here.
- **Raised** — 1200 to 1299. A bot with a winning record.
- **Elite** — 1300 and above. The bots that beat the field consistently.

A bot's division is derived from its rating, not from a fixed list. Beat enough opponents and you move up. Lose and you move down. There is no protected status.

## Where ratings come from

House bots get a rating from their round-robin score: a bot at +100 bb/100 lands around 1320, a bot at +80 lands around 1296. Uploaded bots start at 1200 and move on an Elo curve after every rated challenge.

The exact numbers are less important than the ordering. Ratings are a coarse filter; the head-to-head matrix is the detailed picture.

## Rated challenges

A rated challenge runs 20 hands in your browser against a house table. The arena records the result, updates your rating and writes a line in your bot's match history.

The hands are run client-side because the models are Python and the arena has no Python server. The result is trusted the same way a submitted tournament result is trusted: this is a free, no-stakes arena, and the point is to compare bots, not to police them.

## What gets you promoted

Consistency. A bot that wins 55% of its challenges against Raised opponents will climb. A bot that wins one big match and loses the next three will not. The ladder is deliberately boring: it rewards the same thing the round robin rewards.`,
  },
];
