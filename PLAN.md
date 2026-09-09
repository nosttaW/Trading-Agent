# File-level implementation plan

1. `backend/app.py` — FastAPI routes, persistent SQLite jobs, reviewed strategy templates, deterministic chronological backtests, provider adapter/security, immutable live-data simulations, fail-closed broker/live boundaries, audit and health APIs.
2. `backend/migrations/001_initial.sql` — persistent schema for settings, sessions, candidates, results, forward tests, jobs, approvals, and append-only audit records.
3. `frontend/src.tsx` + `frontend/style.css` — responsive accessible research workspace, onboarding/settings, sessions, leaderboard, strategy detail, compare, live-data-test setup/dashboard, trading safety screens.
4. `tests/test_app.py` — provider SSRF/redaction, scheduler state transitions, duplicate retention, backtest timing, immutable snapshots, and no-broker-path safety tests.
5. `README.md`, `.env.example`, `docs/*`, Compose/Docker files — startup, architecture, strategy API, deployment, backups, limitations, verified official references.

Safety default: arbitrary generated Python execution disabled. AI may only select reviewed templates plus validated parameters. Broker paper/live submission remains fail-closed until authenticated adapter, isolation, reconciliation, and operational controls receive separate implementation and review.
