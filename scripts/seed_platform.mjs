// Seed the public arena with a demo account, three uploaded bots and the
// opening forum threads. Idempotent: existing bots/threads are skipped.
//
//   node scripts/seed_platform.mjs [base-url]

const BASE = process.argv[2] || "https://poker-arena.hheylau.workers.dev";
const EMAIL = "demo@poker-arena.local";
const HANDLE = "arena";
const PASSWORD = "arena-demo-2026";

let cookie = "";

async function call(path, options = {}) {
  const response = await fetch(`${BASE}${path}`, {
    ...options,
    headers: {
      "content-type": "application/json",
      ...(cookie ? { cookie } : {}),
      ...(options.headers || {}),
    },
  });
  const setCookie = response.headers.get("set-cookie");
  if (setCookie) cookie = setCookie.split(";")[0];
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(`${path}: ${data.error || response.status}`);
  return data;
}

async function login() {
  try {
    await call("/api/auth/login", { method: "POST", body: JSON.stringify({ email: EMAIL, password: PASSWORD }) });
    return;
  } catch {
    await call("/api/auth/signup", { method: "POST", body: JSON.stringify({ email: EMAIL, handle: HANDLE, password: PASSWORD, terms: true }) });
  }
}

const BOTS = [
  {
    name: "PocketRockets",
    description: "Raises pairs and big aces, checks everything else. A gentle introduction to the ladder.",
    source: `from card_utils import Card
from player_action import PlayerAction

class Model:
    def seed(self, hand, player_id):
        self.hand = hand
        self.player_id = player_id
        self.values = sorted((c.value for c in hand), reverse=True)

    def run(self, actions, state):
        values = self.values
        pair = values[0] == values[1]
        big = values[0] >= 12
        if state.call_amount == 0:
            if pair or big:
                return PlayerAction.raise_to(self.player_id, min(state.max_raise_to, max(state.min_raise_to, state.big_blind * 3)))
            return PlayerAction.call(self.player_id)
        if pair and state.call_amount <= state.big_blind * 8:
            if state.can_raise:
                return PlayerAction.raise_to(self.player_id, min(state.max_raise_to, state.min_raise_to))
            return PlayerAction.call(self.player_id)
        if big and state.call_amount <= state.big_blind * 3:
            return PlayerAction.call(self.player_id)
        return PlayerAction.fold(self.player_id)

    def end_game(self, actions, state):
        pass
`,
  },
  {
    name: "SevenDeuce",
    description: "Loose and aggressive: attacks limps, barrels the flop, folds to real resistance.",
    source: `import random
from card_utils import Card
from player_action import PlayerAction

class Model:
    def seed(self, hand, player_id):
        self.hand = hand
        self.player_id = player_id
        self.rng = random.Random(1000 + player_id + hand[0].value * 13 + hand[1].value)

    def run(self, actions, state):
        pressure = self.rng.random()
        if state.call_amount == 0:
            if pressure < 0.45 and state.can_raise:
                size = max(state.min_raise_to, min(state.max_raise_to, state.pot // 2 or state.big_blind * 2))
                return PlayerAction.raise_to(self.player_id, size)
            return PlayerAction.call(self.player_id)
        if state.call_amount <= state.big_blind * 4 and pressure < 0.55:
            return PlayerAction.call(self.player_id)
        if pressure > 0.92 and state.can_raise:
            return PlayerAction.raise_to(self.player_id, state.min_raise_to)
        return PlayerAction.fold(self.player_id)

    def end_game(self, actions, state):
        pass
`,
  },
  {
    name: "TheStation",
    description: "Calls too much on purpose. A useful baseline for testing whether your bot can value bet.",
    source: `from card_utils import Card
from player_action import PlayerAction

class Model:
    def seed(self, hand, player_id):
        self.hand = hand
        self.player_id = player_id

    def run(self, actions, state):
        if state.call_amount <= state.big_blind * 10 or state.call_amount == 0:
            return PlayerAction.call(self.player_id)
        return PlayerAction.fold(self.player_id)

    def end_game(self, actions, state):
        pass
`,
  },
];

const THREADS = [
  {
    title: "Welcome to the arena forum",
    category: "general",
    body: `## Start here

This is the place for bot development, tournament talk and bug reports. Be civil and post what you tried.

- **Development** — model interface, strategy, performance.
- **Tournaments** — entries, results, format questions.
- **Bugs** — what you expected, what happened, and the URL.

The [platform rules](#/rules) apply everywhere.`,
    replies: [
      "The 2-7 Triple Draw table is live. Go and foul up your first board.",
      "Pineapple OFC is the most humbling game on the site. I placed a pair of threes on top and spent four streets paying for it.",
    ],
  },
  {
    title: "How do I test a bot without publishing it?",
    category: "development",
    body: `I have a model.py that I want to try before anyone else sees it. Is there a private test mode?

Right now, uploading publishes the bot. A rated test runs 20 hands in your browser and posts the result. If you want a private check first, run the model locally against the shim in \`public/py/shim.py\` — it implements the same \`seed\`/\`run\`/\`end_game\` surface.`,
    replies: [
      "Also worth reading [Your first bot](#/stack/your-first-bot). The three rules there (never fault, cache your work, keep state in plain data) saved me a lot of debugging.",
    ],
  },
  {
    title: "Season 2 Kickoff — who is entering?",
    category: "tournaments",
    body: `The Kickoff is open for entries. Six-max, duplicate deals, Swiss then a final table.

I am entering **PocketRockets** even though it probably folds too much postflop. What is everyone else bringing?`,
    replies: [
      "SevenDeuce is in. It will either top the group or bust in the first hour, there is no middle ground.",
      "TheStation is entering as a control. If your bot cannot beat TheStation, it is not ready.",
    ],
  },
  {
    title: "Bug: the OFC board does not show my last discard",
    category: "bugs",
    body: `When I place two cards on street 3, the third card disappears but the board does not show which one was discarded. The discard counter goes up, so I assume it is face down by design.

Is that intended? If so, maybe the log should say "discarded face down" rather than showing the card.`,
    replies: [
      "Intended: discards stay face down. The log line is a fair point, I will make it less specific.",
    ],
  },
];

async function main() {
  await login();
  const me = await call("/api/auth/me");
  console.log(`signed in as @${me.user.handle}`);

  const existing = new Set((me.bots || []).map((b) => b.name));
  for (const bot of BOTS) {
    if (existing.has(bot.name)) {
      console.log(`bot ${bot.name}: exists`);
      continue;
    }
    await call("/api/bots", { method: "POST", body: JSON.stringify({ ...bot, mode: "nlh" }) });
    console.log(`bot ${bot.name}: uploaded`);
  }

  const forum = await call("/api/forum/threads");
  const titles = new Set((forum.threads || []).map((t) => t.title));
  for (const thread of THREADS) {
    if (titles.has(thread.title)) {
      console.log(`thread ${thread.title}: exists`);
      continue;
    }
    const created = await call("/api/forum/threads", {
      method: "POST",
      body: JSON.stringify({ title: thread.title, category: thread.category, body: thread.body }),
    });
    for (const reply of thread.replies || []) {
      await call(`/api/forum/threads/${created.id}/posts`, {
        method: "POST",
        body: JSON.stringify({ body: reply }),
      });
    }
    console.log(`thread ${thread.title}: created`);
  }

  try {
    await call("/api/newsletter", {
      method: "POST",
      body: JSON.stringify({ email: EMAIL, source: "seed" }),
    });
    console.log("newsletter: subscribed");
  } catch (error) {
    console.log(`newsletter: ${error.message}`);
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
