PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS providers (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  base_url TEXT NOT NULL,
  profile TEXT NOT NULL CHECK(profile IN ('chat_completions','responses')),
  model_id TEXT NOT NULL,
  encrypted_api_key TEXT,
  encrypted_headers TEXT,
  timeout_seconds INTEGER NOT NULL,
  max_output_tokens INTEGER NOT NULL,
  temperature REAL,
  concurrency_limit INTEGER NOT NULL,
  max_retries INTEGER NOT NULL,
  input_price_per_million TEXT,
  output_price_per_million TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS alpaca_connections (
  mode TEXT PRIMARY KEY CHECK(mode IN ('data','paper','live')),
  label TEXT NOT NULL,
  encrypted_key_id TEXT NOT NULL,
  encrypted_secret_key TEXT NOT NULL,
  feed TEXT,
  base_url TEXT NOT NULL,
  last_test_status TEXT,
  last_test_at TEXT,
  account_id_masked TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS research_sessions (
  id TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  state TEXT NOT NULL,
  config TEXT NOT NULL,
  config_hash TEXT NOT NULL,
  next_run_at TEXT,
  started_at TEXT,
  stopped_at TEXT,
  created_at TEXT NOT NULL,
  generation_count INTEGER NOT NULL DEFAULT 0,
  token_count INTEGER NOT NULL DEFAULT 0,
  in_flight INTEGER NOT NULL DEFAULT 0,
  cancel_epoch INTEGER NOT NULL DEFAULT 0,
  last_error TEXT
);

CREATE TABLE IF NOT EXISTS candidates (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES research_sessions(id),
  ordinal INTEGER NOT NULL,
  status TEXT NOT NULL,
  family TEXT,
  name TEXT NOT NULL,
  hypothesis TEXT NOT NULL,
  parameters TEXT NOT NULL,
  source TEXT NOT NULL,
  source_hash TEXT NOT NULL,
  normalized_hash TEXT NOT NULL,
  dependency_manifest TEXT NOT NULL,
  provider_id TEXT,
  model_id TEXT,
  prompt_version TEXT NOT NULL,
  token_usage INTEGER,
  estimated_cost TEXT,
  warnings TEXT NOT NULL,
  error TEXT,
  created_at TEXT NOT NULL,
  UNIQUE(session_id, ordinal)
);

CREATE TABLE IF NOT EXISTS backtests (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES research_sessions(id),
  candidate_id TEXT NOT NULL REFERENCES candidates(id),
  status TEXT NOT NULL,
  dataset_id TEXT NOT NULL,
  dataset_hash TEXT NOT NULL,
  engine_version TEXT NOT NULL,
  engine_hash TEXT NOT NULL,
  assumptions TEXT NOT NULL,
  metrics TEXT,
  equity_curve TEXT,
  drawdown_curve TEXT,
  trades TEXT,
  warnings TEXT NOT NULL,
  error TEXT,
  started_at TEXT NOT NULL,
  completed_at TEXT,
  UNIQUE(candidate_id, dataset_hash, engine_hash)
);

CREATE TABLE IF NOT EXISTS live_tests (
  id TEXT PRIMARY KEY,
  backtest_id TEXT NOT NULL REFERENCES backtests(id),
  candidate_id TEXT NOT NULL REFERENCES candidates(id),
  strategy_hash TEXT NOT NULL,
  source_snapshot TEXT NOT NULL,
  parameters_snapshot TEXT NOT NULL,
  dependency_snapshot TEXT NOT NULL,
  engine_version TEXT NOT NULL,
  state TEXT NOT NULL,
  mode TEXT NOT NULL CHECK(mode='LIVE_DATA_SIMULATED'),
  config TEXT NOT NULL,
  virtual_cash TEXT NOT NULL,
  equity TEXT NOT NULL,
  positions TEXT NOT NULL,
  pending_orders TEXT NOT NULL,
  fills TEXT NOT NULL,
  last_event_at TEXT,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  paused_entries INTEGER NOT NULL DEFAULT 0,
  warnings TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  resource_id TEXT NOT NULL,
  unique_key TEXT NOT NULL UNIQUE,
  state TEXT NOT NULL,
  due_at TEXT NOT NULL,
  lease_owner TEXT,
  lease_until TEXT,
  attempts INTEGER NOT NULL DEFAULT 0,
  payload TEXT NOT NULL,
  last_error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS approvals (
  id TEXT PRIMARY KEY,
  candidate_id TEXT NOT NULL REFERENCES candidates(id),
  strategy_hash TEXT NOT NULL,
  engine_hash TEXT NOT NULL,
  broker_account TEXT NOT NULL,
  mode TEXT NOT NULL,
  limits TEXT NOT NULL,
  starts_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  revoked INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at TEXT NOT NULL,
  actor TEXT NOT NULL,
  kind TEXT NOT NULL,
  resource_type TEXT NOT NULL,
  resource_id TEXT NOT NULL,
  payload TEXT NOT NULL,
  previous_hash TEXT,
  event_hash TEXT NOT NULL UNIQUE
);

CREATE INDEX IF NOT EXISTS idx_sessions_state_next ON research_sessions(state,next_run_at);
CREATE INDEX IF NOT EXISTS idx_candidates_session ON candidates(session_id,ordinal);
CREATE INDEX IF NOT EXISTS idx_backtests_session ON backtests(session_id,status);
CREATE INDEX IF NOT EXISTS idx_jobs_due ON jobs(state,due_at);
CREATE INDEX IF NOT EXISTS idx_audit_at ON audit_events(at);
