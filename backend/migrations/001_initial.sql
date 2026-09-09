PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS app_metadata (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

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

CREATE TABLE IF NOT EXISTS watchlist (
  symbol TEXT PRIMARY KEY,
  timeframe TEXT NOT NULL,
  bars TEXT NOT NULL DEFAULT '[]',
  last_event_at TEXT,
  last_poll_at TEXT,
  status TEXT NOT NULL DEFAULT 'PENDING',
  error TEXT,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS market_datasets (
  id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  instrument TEXT NOT NULL,
  timeframe TEXT NOT NULL,
  start_at TEXT NOT NULL,
  end_at TEXT NOT NULL,
  feed TEXT NOT NULL,
  bars TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS research_sources (
  id TEXT PRIMARY KEY,
  session_id TEXT NOT NULL REFERENCES research_sessions(id),
  url TEXT NOT NULL,
  title TEXT NOT NULL,
  published_at TEXT,
  retrieved_at TEXT NOT NULL,
  excerpt TEXT NOT NULL,
  content_hash TEXT NOT NULL,
  status TEXT NOT NULL
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
  archived_at TEXT,
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
  invalidated_at TEXT,
  invalidation_reason TEXT,
  UNIQUE(candidate_id, dataset_hash, engine_hash)
);

CREATE TABLE IF NOT EXISTS strategy_reviews (
  id TEXT PRIMARY KEY,
  backtest_id TEXT NOT NULL REFERENCES backtests(id),
  provider_id TEXT NOT NULL REFERENCES providers(id),
  model_id TEXT NOT NULL,
  verdict TEXT NOT NULL,
  summary TEXT NOT NULL,
  strengths TEXT NOT NULL,
  weaknesses TEXT NOT NULL,
  recommendations TEXT NOT NULL,
  evidence_snapshot_hash TEXT NOT NULL,
  token_usage INTEGER,
  created_at TEXT NOT NULL
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
  warnings TEXT NOT NULL,
  runtime_state TEXT NOT NULL DEFAULT '{}',
  logs TEXT NOT NULL DEFAULT '[]'
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

CREATE TABLE IF NOT EXISTS paper_sessions (
  id TEXT PRIMARY KEY,
  backtest_id TEXT NOT NULL REFERENCES backtests(id),
  candidate_id TEXT NOT NULL REFERENCES candidates(id),
  strategy_hash TEXT NOT NULL,
  engine_hash TEXT NOT NULL,
  broker_account_id TEXT NOT NULL,
  instrument TEXT NOT NULL,
  timeframe TEXT NOT NULL,
  state TEXT NOT NULL,
  approval_expires_at TEXT NOT NULL,
  limits TEXT NOT NULL,
  strategy_state TEXT NOT NULL,
  last_bar_at TEXT,
  last_reconciled_at TEXT,
  peak_equity TEXT,
  start_of_day_equity TEXT,
  emergency_stop INTEGER NOT NULL DEFAULT 0,
  lease_owner TEXT,
  lease_until TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  automation_enabled INTEGER NOT NULL DEFAULT 0,
  automation_state TEXT NOT NULL DEFAULT 'DISABLED',
  automation_runtime TEXT NOT NULL DEFAULT '{}',
  automation_logs TEXT NOT NULL DEFAULT '[]',
  archived_at TEXT
);

CREATE TABLE IF NOT EXISTS paper_orders (
  id TEXT PRIMARY KEY,
  paper_session_id TEXT NOT NULL REFERENCES paper_sessions(id),
  client_order_id TEXT NOT NULL UNIQUE,
  broker_order_id TEXT UNIQUE,
  bar_at TEXT NOT NULL,
  side TEXT NOT NULL CHECK(side IN ('buy','sell')),
  quantity TEXT NOT NULL,
  reference_price TEXT NOT NULL,
  status TEXT NOT NULL,
  filled_quantity TEXT NOT NULL DEFAULT '0',
  average_fill_price TEXT,
  raw_status TEXT,
  error TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS executor_control (
  id INTEGER PRIMARY KEY CHECK(id=1),
  emergency_stop INTEGER NOT NULL DEFAULT 1,
  reason TEXT NOT NULL,
  updated_at TEXT NOT NULL
);
INSERT OR IGNORE INTO executor_control(id,emergency_stop,reason,updated_at) VALUES(1,1,'Restart requires reconciliation',CURRENT_TIMESTAMP);

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
CREATE INDEX IF NOT EXISTS idx_paper_sessions_state ON paper_sessions(state,approval_expires_at);
CREATE INDEX IF NOT EXISTS idx_paper_orders_session ON paper_orders(paper_session_id,created_at);
