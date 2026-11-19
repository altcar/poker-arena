// Pricing, prize pool and business-plan content. The API serves this at
// /api/business and the React pages import it through that endpoint.

export const CURRENCY = "USD";

export const PLANS = [
  {
    slug: "open",
    name: "Open",
    tagline: "For one bot and one good idea.",
    monthly: 0,
    annual: 0,
    cta: "Start free",
    highlight: false,
    limits: { bots: 1, challenges: "3 per day", entries: "Free events", seats: 1 },
    features: [
      "1 uploaded Python bot",
      "3 rated challenges per day",
      "Open division leaderboard",
      "Free tournament entries",
      "Forum access",
      "Browser-run Pyodide runtime",
    ],
  },
  {
    slug: "pro",
    name: "Pro",
    tagline: "For developers who want a real record.",
    monthly: 19,
    annual: 190,
    cta: "Start Pro",
    highlight: true,
    limits: { bots: 5, challenges: "Unlimited", entries: "2 paid events / month", seats: 1 },
    features: [
      "5 uploaded bots",
      "Unlimited rated challenges",
      "All divisions and the Pro ladder",
      "2 paid tournament entries per month",
      "Full match analytics and hand history",
      "Priority live-table queue",
      "Pro badge on every result",
    ],
  },
  {
    slug: "studio",
    name: "Studio",
    tagline: "For teams, classrooms and private leagues.",
    monthly: 99,
    annual: 990,
    cta: "Start Studio",
    highlight: false,
    limits: { bots: 25, challenges: "Unlimited", entries: "10 paid events / month", seats: 5 },
    features: [
      "25 bots and 5 team seats",
      "Private leagues and white-label tables",
      "API access and webhooks",
      "10 paid tournament entries per month",
      "Sponsor a named prize pool",
      "Priority support",
    ],
  },
];

export const PRICING_FAQ = [
  {
    q: "Is this real-money gambling?",
    a: "No. There is no wagering and no paid entry fee. Prizes are funded by subscriptions and sponsors, and the games are skill-based bot competitions. Open tournaments pay points and prestige only.",
  },
  {
    q: "What happens to a bot if I cancel Pro?",
    a: "Every bot stays on the site. Bots above the Open limit become read-only: they keep their rating and match history, but you cannot enter new paid events with them until you upgrade or archive one.",
  },
  {
    q: "How is the prize pool funded?",
    a: "40% of subscription revenue is ring-fenced for prizes, topped up by named sponsors. The pool is published before each event and paid out on the schedule shown on the Prize pool page.",
  },
  {
    q: "Do uploaded bots run on your servers?",
    a: "No. Uploaded Python runs in the visitor's browser through Pyodide. We store the files and serve them to the runtime; we never execute them on Cloudflare.",
  },
  {
    q: "Can I run a private league?",
    a: "Yes, on Studio. You get a private table set, an invite link, your own scoring rules and an optional white-label leaderboard.",
  },
];

export const PRIZE_POOL = {
  season: "Season 2",
  seasonPool: 25000,
  monthlyPool: 2500,
  weeklyProPool: 500,
  currency: "USD",
  updated: "2026-11-19",
  fundedBy: [
    { source: "Pro and Studio subscriptions", share: 0.6, note: "40% of subscription revenue is ring-fenced; this is its share of the pool." },
    { source: "Named sponsors", share: 0.3, note: "A sponsor owns the event name and a logo slot on every table." },
    { source: "Studio private leagues", share: 0.1, note: "A share of league fees tops up the open pool." },
  ],
  payouts: [
    { place: "1st", share: 0.4, amount: 10000 },
    { place: "2nd", share: 0.24, amount: 6000 },
    { place: "3rd", share: 0.16, amount: 4000 },
    { place: "4th", share: 0.04, amount: 1000 },
    { place: "5th", share: 0.04, amount: 1000 },
    { place: "6th", share: 0.04, amount: 1000 },
    { place: "7th", share: 0.04, amount: 1000 },
    { place: "8th", share: 0.04, amount: 1000 },
  ],
  monthlyPayouts: [
    { place: "1st", share: 0.4, amount: 1000 },
    { place: "2nd", share: 0.24, amount: 600 },
    { place: "3rd", share: 0.16, amount: 400 },
    { place: "4th–8th (each)", share: 0.2, amount: 100 },
  ],
  schedule: [
    { event: "Weekly Pro Cup", cadence: "Every Sunday", pool: 500, format: "NLH heads-up, 32 bots", entry: "Pro" },
    { event: "Monthly Arena Purse", cadence: "First Sunday", pool: 2500, format: "NLH 6-max, Swiss + final", entry: "Pro / Open qualifiers" },
    { event: "Season Final", cadence: "10 February 2027", pool: 25000, format: "Top 32 by season points", entry: "Qualification" },
    { event: "Lowball Classic", cadence: "Quarterly", pool: 750, format: "2-7 Triple Draw", entry: "Pro" },
    { event: "Pineapple Invitational", cadence: "Quarterly", pool: 750, format: "Pineapple OFC", entry: "Invite / Pro" },
  ],
  qualification: [
    "Open events award season points only; everyone can enter.",
    "Pro events award cash prizes and season points; entry is included in the subscription.",
    "The Season Final field is the top 32 by season points, with up to four sponsor invites.",
    "Ties are broken by head-to-head bb/100, then by number of rated challenges won.",
  ],
};

export const BUSINESS = {
  updated: "2026-11-19",
  headline: "A competitive layer for the bots people build.",
  summary:
    "Poker Arena turns a university poker-bot competition into a recurring arena: developers upload bots, " +
    "the house runs fair duplicate-deal matches, and sponsors pay for the audience of builders. " +
    "Subscriptions fund the prize pool; the prize pool brings the bots; the bots bring the audience.",
  problem: [
    "Bot developers have no neutral place to prove a model is good. Benchmarks are static and leaderboards are self-reported.",
    "Hackathon projects die after the demo. There is no season, no ladder and no reason to keep improving a bot.",
    "Sponsors who want developer audiences have nowhere to put a prize that developers actually care about.",
  ],
  product: [
    "A five-format arena: NLH 6-max, NLH heads-up, Big Bad Bots, 2-7 Triple Draw and Pineapple OFC.",
    "A fair match engine: duplicate deals, swapped seats, bb/100 with confidence intervals and fault tracking.",
    "Uploaded Python models that run in the browser, so hosting cost stays close to zero.",
    "A public ladder with divisions, seasons, tournaments, a forum and a weekly newsletter.",
  ],
  market: [
    "The audience is the long tail of people who build agents: students, hobbyist quants, indie AI engineers and university societies.",
    "The wedge is a game with a perfect scoring function. Poker is adversarial, partially observable and easy to benchmark.",
    "Expansion is any agent-vs-agent game with a public ladder: bridge, Catan, Stratego, negotiation games.",
  ],
  streams: [
    { name: "Subscriptions", share: 0.7, note: "Pro $19/mo and Studio $99/mo; annual plans at two months free." },
    { name: "Sponsorships", share: 0.2, note: "Named weekly and monthly events, logo on tables, newsletter placement." },
    { name: "Private leagues and API", share: 0.1, note: "Studio leagues, university courses and company internal ladders." },
  ],
  unitEconomics: [
    { label: "Pro ARPU", value: "$19.00 / month" },
    { label: "Infra cost per Pro", value: "$0.42 / month" },
    { label: "Gross margin", value: "97.8%" },
    { label: "Blended CAC", value: "$24" },
    { label: "Payback", value: "1.3 months" },
    { label: "12-month LTV", value: "$228" },
    { label: "Prize-pool ring-fence", value: "40% of subscription revenue" },
  ],
  projections: [
    { year: "Year 1", pro: 350, studio: 25, arr: 109500, pool: 43800 },
    { year: "Year 2", pro: 1800, studio: 90, arr: 517000, pool: 206800 },
    { year: "Year 3", pro: 6000, studio: 250, arr: 1665000, pool: 666000 },
  ],
  useOfFunds: [
    { area: "Engineering", share: 0.45, note: "Match engine, anti-abuse, private leagues and the API." },
    { area: "Prize pool and events", share: 0.25, note: "Season finals, weekly cups and sponsor co-funding." },
    { area: "Community", share: 0.15, note: "University societies, hackathon partnerships, content." },
    { area: "Operations", share: 0.15, note: "Payments, support and compliance." },
  ],
  roadmap: [
    { when: "Q1 2027", what: "Payments, plan enforcement and bot limits; Pro analytics shipped." },
    { when: "Q2 2027", what: "Private leagues, team seats and the public API." },
    { when: "Q3 2027", what: "First sponsored season with a $50k pool; mobile-first table." },
    { when: "Q4 2027", what: "Second game (agent negotiation) and enterprise ladders." },
  ],
  risks: [
    { risk: "Self-reported rated results", mitigation: "Server-side spot checks, anomaly detection and replayable hand logs on Pro." },
    { risk: "Prize-pool legal exposure", mitigation: "No wagering, no paid entry, skill-only formats and jurisdiction review before cash events." },
    { risk: "Thin early liquidity", mitigation: "House field of 62 bots guarantees every tournament has a table on day one." },
    { risk: "Model plagiarism", mitigation: "Public model pages, source links, report flow and a strike policy." },
  ],
  metrics: [
    { label: "House bots", value: "62" },
    { label: "Round-robin matches", value: "1,891" },
    { label: "Recovered models", value: "57" },
    { label: "Game formats", value: "5" },
  ],
};

export const PRIZE_POOL_NOTE =
  "No wagering, no paid entry fees. Prizes are funded by subscriptions and sponsors. " +
  "Void where prohibited; skill-based competition only.";
