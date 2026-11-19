-- Poker Arena platform schema. Apply with:
--   npx wrangler d1 execute poker-arena --remote --file=src/schema.sql
-- Safe to run more than once.

CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  handle TEXT NOT NULL UNIQUE,
  password_hash TEXT NOT NULL,
  salt TEXT NOT NULL,
  role TEXT NOT NULL DEFAULT 'member',
  created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS auth_sessions (
  token TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  created_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS auth_sessions_user ON auth_sessions (user_id);

CREATE TABLE IF NOT EXISTS user_bots (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  name TEXT NOT NULL,
  slug TEXT NOT NULL,
  description TEXT NOT NULL DEFAULT '',
  mode TEXT NOT NULL DEFAULT 'nlh',
  source TEXT NOT NULL,
  extras TEXT NOT NULL DEFAULT '[]',
  pickle TEXT,
  pkl_name TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'active',
  rating REAL NOT NULL DEFAULT 1200,
  matches INTEGER NOT NULL DEFAULT 0,
  wins INTEGER NOT NULL DEFAULT 0,
  losses INTEGER NOT NULL DEFAULT 0,
  draws INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS user_bots_user ON user_bots (user_id);
CREATE INDEX IF NOT EXISTS user_bots_rating ON user_bots (rating DESC);

CREATE TABLE IF NOT EXISTS bot_results (
  id TEXT PRIMARY KEY,
  bot_id TEXT NOT NULL,
  opponent TEXT NOT NULL,
  mode TEXT NOT NULL DEFAULT 'nlh',
  hands INTEGER NOT NULL DEFAULT 0,
  result TEXT NOT NULL,
  chips INTEGER NOT NULL DEFAULT 0,
  rating_before REAL,
  rating_after REAL,
  created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS bot_results_bot ON bot_results (bot_id, created_at DESC);

CREATE TABLE IF NOT EXISTS threads (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  category TEXT NOT NULL DEFAULT 'general',
  title TEXT NOT NULL,
  body TEXT NOT NULL,
  pinned INTEGER NOT NULL DEFAULT 0,
  locked INTEGER NOT NULL DEFAULT 0,
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS threads_category ON threads (category, updated_at DESC);

CREATE TABLE IF NOT EXISTS posts (
  id TEXT PRIMARY KEY,
  thread_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  body TEXT NOT NULL,
  created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS posts_thread ON posts (thread_id, created_at);

CREATE TABLE IF NOT EXISTS newsletter (
  id TEXT PRIMARY KEY,
  email TEXT NOT NULL UNIQUE,
  source TEXT NOT NULL DEFAULT 'site',
  created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS tournament_entries (
  id TEXT PRIMARY KEY,
  tournament_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  bot_id TEXT,
  created_at INTEGER NOT NULL,
  UNIQUE (tournament_id, user_id)
);

CREATE INDEX IF NOT EXISTS tournament_entries_tid ON tournament_entries (tournament_id);

-- Virtual bankroll created for every account. Not cash, not withdrawable.
CREATE TABLE IF NOT EXISTS paper_accounts (
  user_id TEXT PRIMARY KEY,
  balance INTEGER NOT NULL,
  starting_balance INTEGER NOT NULL,
  currency TEXT NOT NULL DEFAULT 'PAPER',
  created_at INTEGER NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS paper_transactions (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  amount INTEGER NOT NULL,
  balance_after INTEGER NOT NULL,
  note TEXT NOT NULL DEFAULT '',
  created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS paper_transactions_user ON paper_transactions (user_id, created_at DESC);

-- One row per account. plan stays "open" until a Stripe subscription is active.
CREATE TABLE IF NOT EXISTS subscriptions (
  user_id TEXT PRIMARY KEY,
  plan TEXT NOT NULL DEFAULT 'open',
  status TEXT NOT NULL DEFAULT 'active',
  interval TEXT NOT NULL DEFAULT 'monthly',
  stripe_customer_id TEXT,
  stripe_subscription_id TEXT,
  current_period_end INTEGER,
  cancel_at_period_end INTEGER NOT NULL DEFAULT 0,
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS stripe_events (
  id TEXT PRIMARY KEY,
  type TEXT NOT NULL,
  created_at INTEGER NOT NULL
);
