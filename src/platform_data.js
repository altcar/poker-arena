// Static platform metadata: game modes, divisions, tournaments and seasons.
// The live numbers (standings, results) come from the worker at request time.

export const GAMES = [
  {
    slug: "nlh6",
    mode: "nlh6",
    name: "No-Limit Hold'em — 6-max",
    short: "NLH 6-max",
    seats: 6,
    tagline: "The arena's main event. Six seats, two hole cards, five shared cards.",
    summary:
      "No-limit Hold'em with six seats. You can bet anything from the minimum to your whole stack. " +
      "This is the format used for the round robin, the leaderboard and most tournaments.",
    playable: true,
    badges: ["Main event", "6 seats", "No limit"],
    rules: [
      ["The deal", "Every seat gets two private cards. Five community cards are dealt face up: the flop (3), the turn (1) and the river (1)."],
      ["Betting", "There is a betting round before the flop and after each street. A raise must be at least the size of the previous raise, and you can shove all-in at any time."],
      ["Winning", "Make the best five-card hand from your two cards and the board. You can also win by making every opponent fold."],
      ["Blinds", "The button moves one seat each hand. The seat left of the button posts the small blind, the next seat the big blind."],
      ["Arena format", "Bots are compared over duplicate deals: every pairing plays the same decks twice with the seats swapped, and results are reported in bb/100 with a 95% interval."],
    ],
  },
  {
    slug: "hu",
    mode: "hu",
    name: "No-Limit Hold'em — Heads-up",
    short: "NLH Heads-up",
    seats: 2,
    tagline: "One opponent, one table, no place to hide.",
    summary:
      "The purest test of a bot: two seats, wide ranges and the button posting the small blind. " +
      "Heads-up is the format for the Heads-Up Open and for rated challenges against uploaded bots.",
    playable: true,
    badges: ["2 seats", "No limit", "Rated"],
    rules: [
      ["The button", "With two players the button posts the small blind and acts first before the flop, then last on every later street."],
      ["Pre-flop", "The small blind acts first. The big blind still has the option to raise after a call."],
      ["Post-flop", "The big blind (out of position) acts first on the flop, turn and river."],
      ["Why it is different", "You cannot wait for premium hands. Heads-up ranges are much wider, and the button is a big advantage."],
    ],
  },
  {
    slug: "boss",
    mode: "boss",
    name: "Big Bad Bots",
    short: "Big Bad Bots",
    seats: 2,
    tagline: "A three-boss gauntlet. Bigger stacks, rising blinds, no rebuys.",
    summary:
      "A boss rush for humans. Beat three bosses heads-up in a row. Each boss starts with more chips " +
      "than you and the blinds rise every round. Lose your stack and the run is over.",
    playable: true,
    badges: ["Boss rush", "3 rounds", "No rebuys"],
    rules: [
      ["The run", "You start each round with 1,000 chips. The boss starts with 1,500, then 2,500, then 4,000."],
      ["The blinds", "Round 1 is 10/20, round 2 is 15/30, round 3 is 25/50. The button alternates every hand."],
      ["Advancing", "Take the boss's whole stack to advance. If your stack reaches zero, the run ends and you start again from round 1."],
      ["No rebuys", "Bots do not rebuy in this mode. Every chip on the table is in play."],
      ["Bosses", "The Bully (aggressive), The Wall (mixed) and The Machine (adaptive). They are the arena's three strongest JavaScript bots."],
    ],
  },
  {
    slug: "draw27",
    mode: "draw27",
    name: "2-7 Triple Draw",
    short: "2-7 Triple Draw",
    seats: 2,
    tagline: "Five cards, three draws, and the lowest hand wins.",
    summary:
      "A fixed-limit lowball game. Straights and flushes count against you and aces are always high. " +
      "You get three chances to throw cards away and draw replacements.",
    playable: true,
    badges: ["Lowball", "Fixed limit", "3 draws"],
    rules: [
      ["The deal", "Blinds are posted as in Hold'em and every seat gets five cards face down. A betting round follows, starting with the small blind in a heads-up match."],
      ["The draws", "Each player in turn chooses which cards to throw away, from none to all five, and receives the same number of replacements. Everyone can see how many cards you took, never which ones."],
      ["Betting", "There is a betting round after every draw. Bets are one big blind before and after the first draw, and two big blinds after the second and third draws. A round is capped at four bets."],
      ["Showdown", "Whoever is still in turns over five cards. Lowest hand takes the pot. Ties split it."],
      ["Hand ranking", "Make the lowest five-card hand. 7-5-4-3-2 in mixed suits is the nuts. Straights and flushes count against you, aces are high, and pairs are worse than any unpaired hand."],
    ],
  },
  {
    slug: "ofc",
    mode: "ofc",
    name: "Pineapple OFC",
    short: "Pineapple OFC",
    seats: 2,
    tagline: "Build three poker hands face up. No betting, just placement.",
    summary:
      "Open Face Chinese Poker, Pineapple variant. You build a 3-card top row, a 5-card middle row and a " +
      "5-card bottom row. The bottom must be at least as strong as the middle, and the middle at least as strong as the top.",
    playable: true,
    badges: ["No betting", "13 cards", "Placement"],
    rules: [
      ["Your board", "Three rows: top (3 cards), middle (5) and bottom (5). Thirteen cards end up on the board."],
      ["The deal", "You receive five cards and place all five, in any split across the three rows. On each of the next four streets you receive three cards, place two and discard one face down."],
      ["Turn order", "Players place in turn, not at the same time. The seat after the button places first each street, so the button places last and sees more. The button moves every hand."],
      ["Fouls", "The bottom row must be at least as strong as the middle, and the middle at least as strong as the top. Break that and your whole board fouls."],
      ["Scoring", "Boards are compared row by row, royalties are added, fouls are applied, and the net points are paid in chips. No pot, no bet, no fold."],
      ["Royalties", "Top: pairs 66+ and trips. Middle: trips, straights, flushes, full houses, quads, straight flushes. Bottom: the same, at lower rates."],
    ],
  },
];

export const DIVISIONS = [
  {
    key: "elite",
    name: "Elite",
    min: 1300,
    blurb: "The top of the ladder. Elite bots have beaten the field consistently.",
  },
  {
    key: "raised",
    name: "Raised",
    min: 1200,
    max: 1300,
    blurb: "The competitive middle. A good bot with a real record.",
  },
  {
    key: "open",
    name: "Open",
    min: 0,
    max: 1200,
    blurb: "New and developing bots. Every uploaded bot starts here.",
  },
];

export const TOURNAMENTS = [
  {
    id: "founders-cup",
    name: "The Founders Cup",
    game: "nlh6",
    format: "No-Limit Hold'em, 6-max",
    status: "completed",
    starts: "2026-10-04T18:00:00Z",
    entrants: 62,
    structure: "10 duplicate deals per pairing, seats swapped, 62-model round robin",
    prize: "Season 1 title + 1,000 arena points",
    blurb: "The first full round robin of every model whose files were recovered. It produced the matrix on the Head-to-head page.",
    results: [
      { place: 1, team: "relief-grid" },
      { place: 2, team: "duckies" },
      { place: 3, team: "mate-in-one" },
      { place: 4, team: "error-no-tokens-left" },
      { place: 5, team: "runtime-terror" },
      { place: 6, team: "neko_nano" },
      { place: 7, team: "bismillahinsurance" },
      { place: 8, team: "guinnessssss" },
    ],
  },
  {
    id: "heads-up-open",
    name: "Heads-Up Open",
    game: "hu",
    format: "No-Limit Hold'em, heads-up, 32-bot bracket",
    status: "completed",
    starts: "2026-10-25T17:00:00Z",
    entrants: 32,
    structure: "Single elimination, best of 3 matches, 20 deals each",
    prize: "500 arena points",
    blurb: "The 32 highest-rated bots at the time, in a straight bracket. No hiding in a six-max field.",
    results: [
      { place: 1, team: "relief-grid" },
      { place: 2, team: "mate-in-one" },
      { place: 3, team: "duckies" },
      { place: 4, team: "runtime-terror" },
    ],
  },
  {
    id: "lowball-classic",
    name: "Lowball Classic",
    game: "draw27",
    format: "2-7 Triple Draw, fixed limit, 16-bot bracket",
    status: "completed",
    starts: "2026-11-08T19:00:00Z",
    entrants: 16,
    structure: "Single elimination, 3 draws, 30 hands per match",
    prize: "250 arena points",
    blurb: "A change of pace: no board, no flush draws, and aces are bad news.",
    results: [
      { place: 1, team: "duckies" },
      { place: 2, team: "runtime-terror" },
      { place: 3, team: "relief-grid" },
      { place: 4, team: "adaptive" },
    ],
  },
  {
    id: "season-2-kickoff",
    name: "Season 2 Kickoff",
    game: "nlh6",
    format: "No-Limit Hold'em, 6-max",
    status: "open",
    starts: "2026-12-06T18:00:00Z",
    entrants: 62,
    structure: "6-max tables, 10 duplicate deals per pairing, Swiss then final table",
    prize: "Season 2 seeding + 1,000 arena points",
    blurb: "The first open tournament of Season 2. Enter any bot you have uploaded; house bots are seeded from the round robin.",
    results: [],
  },
  {
    id: "pineapple-invitational",
    name: "Pineapple Invitational",
    game: "ofc",
    format: "Pineapple OFC, heads-up",
    status: "open",
    starts: "2026-12-20T18:00:00Z",
    entrants: 16,
    structure: "Heads-up placement matches, 10 hands per match",
    prize: "300 arena points",
    blurb: "A placement-only event. No betting, no bluffing, just thirteen cards and a lot of regret.",
    results: [],
  },
];

export const SEASONS = [
  {
    id: "1",
    name: "Season 1 — The First Table",
    starts: "2026-09-14",
    ends: "2026-11-11",
    status: "completed",
    champion: "relief-grid",
    blurb:
      "The season that recovered 57 competition models, ran 1,891 matches and produced the first head-to-head matrix.",
  },
  {
    id: "2",
    name: "Season 2 — Open Season",
    starts: "2026-11-18",
    ends: "2027-02-10",
    status: "active",
    champion: null,
    blurb:
      "Uploaded bots join the ladder. Divisions are live, challenges are rated, and the first open tournament starts in December.",
  },
];
