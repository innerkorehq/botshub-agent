CREATE TABLE IF NOT EXISTS candidates (
  id INTEGER PRIMARY KEY,
  key TEXT UNIQUE NOT NULL,          -- canonical key, e.g. gh:owner/repo or web:example.com
  name TEXT NOT NULL,
  website TEXT, repo_url TEXT,
  description TEXT DEFAULT '',
  category TEXT, is_agent INTEGER, class_confidence REAL,
  score REAL DEFAULT 0, score_detail TEXT DEFAULT '{}',
  status TEXT NOT NULL DEFAULT 'new',
  first_seen TEXT NOT NULL, last_seen TEXT NOT NULL,
  last_enriched TEXT
);
CREATE TABLE IF NOT EXISTS signals (   -- dated, append-only: velocity needs history
  id INTEGER PRIMARY KEY,
  candidate_id INTEGER NOT NULL REFERENCES candidates(id),
  source TEXT NOT NULL, metric TEXT NOT NULL, value REAL NOT NULL,
  observed_at TEXT NOT NULL, url TEXT
);
CREATE INDEX IF NOT EXISTS ix_signals ON signals(candidate_id, metric, observed_at);
CREATE TABLE IF NOT EXISTS merges (alias_key TEXT PRIMARY KEY, candidate_id INTEGER, reason TEXT, at TEXT);
CREATE TABLE IF NOT EXISTS pages (     -- fetched sources; quotes must be found here
  id INTEGER PRIMARY KEY,
  candidate_id INTEGER NOT NULL REFERENCES candidates(id),
  url TEXT NOT NULL, kind TEXT, status INTEGER,
  text TEXT NOT NULL, html TEXT, content_hash TEXT NOT NULL, fetched_at TEXT NOT NULL,
  UNIQUE(candidate_id, url)
);
CREATE TABLE IF NOT EXISTS agents (    -- published listings
  id INTEGER PRIMARY KEY,
  candidate_id INTEGER UNIQUE REFERENCES candidates(id),
  slug TEXT UNIQUE NOT NULL, name TEXT NOT NULL, website TEXT, repo_url TEXT,
  category TEXT, tagline TEXT, overview TEXT, listing TEXT NOT NULL,   -- listing = full JSON
  quality REAL, status TEXT NOT NULL, published_at TEXT, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS facts (
  id INTEGER PRIMARY KEY,
  agent_id INTEGER NOT NULL REFERENCES agents(id),
  field TEXT NOT NULL, value TEXT NOT NULL, source_url TEXT NOT NULL, quote TEXT NOT NULL,
  stale INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS media (
  id INTEGER PRIMARY KEY,
  agent_id INTEGER NOT NULL REFERENCES agents(id),
  kind TEXT NOT NULL, url TEXT NOT NULL, source_url TEXT NOT NULL, caption TEXT DEFAULT '',
  UNIQUE(agent_id, url)
);
CREATE TABLE IF NOT EXISTS listing_versions (
  id INTEGER PRIMARY KEY, agent_id INTEGER NOT NULL, listing TEXT NOT NULL, at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS publish_log (
  id INTEGER PRIMARY KEY, candidate_id INTEGER, decision TEXT NOT NULL, reasons TEXT, quality REAL, at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS schema_version (v INTEGER);
INSERT INTO schema_version SELECT 1 WHERE NOT EXISTS (SELECT 1 FROM schema_version);
CREATE TABLE IF NOT EXISTS embedded (candidate_id INTEGER PRIMARY KEY, text_hash TEXT NOT NULL);
