# Strategy Lab

Responsive strategy-research software: persistent sequential research sessions, configurable instruments and 1m/5m/15m/1h/1d timeframes, configurable OpenAI-compatible providers, reviewed Python strategy templates, deterministic chronological batch backtests, leaderboard/comparison, immutable live-data-test setup, audit history, fail-closed trading screens.

> **No profit promise. Not investment advice.** Backtests and simulations do not predict future returns. Execution can differ materially. Risk thresholds are not guaranteed loss caps.

## Safety status

| Mode | Status |
|---|---|
| Connected research | Working after a tested Alpaca market-data connection. AI remains optional. |
| Historical backtest | Working for reviewed long-only US-equity templates on 1m/5m/15m/1h/1d Alpaca historical bars frozen with feed, retrieval timestamp, and content hash. |
| Live Data Test | Persistent immutable session/setup works; awaits configured current Alpaca data; never invents ticks/fills. |
| Broker Paper | Working through Alpaca's official paper endpoint after encrypted credentials, connection test, immutable approval, reconciliation, explicit resume, and independent risk checks. |
| Live Trading | Disabled. No broker-order submission implementation exists. |
| Arbitrary Python | Disabled. Source view/download works; trusted host executes reviewed templates only. |

`ENABLE_LIVE_TRADING=true` **does not enable orders**. `/api/orders` always returns `403`. Research, backtesting, and live-data-test creation cannot place broker orders.

Single-admin password authentication protects every non-health API. Passwords use PBKDF2-HMAC-SHA256 with 600,000 iterations; only the encoded hash is configured. Sessions are signed, 12-hour, HttpOnly, SameSite=Strict cookies. Mutations require a session-bound CSRF token. Five failed attempts per client trigger a 15-minute lockout. Current LAN HTTP needs `COOKIE_SECURE=false`; switch to `true` immediately when HTTPS terminates through Cloudflare Tunnel.

## Local startup

```bash
cp .env.example .env
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
# Paste output into APP_ENCRYPTION_KEY in .env
python -m backend.auth_cli hash-password
# Paste output into ADMIN_PASSWORD_HASH. Generate SESSION_SECRET per .env.example.
python -m pip install -r backend/requirements.txt pytest
python -m uvicorn --app-dir backend app:app --reload
```

Separate terminal:

```bash
cd frontend
npm ci
npm run dev
```

Open <http://localhost:5173>. Production build:

```bash
cd frontend && npm run build
rm -rf backend/static && cp -R dist backend/static
python -m uvicorn --app-dir backend app:app --host 0.0.0.0 --port 8000
```

Packaged Docker image:

```bash
cp .env.example .env
# Set APP_ENCRYPTION_KEY. Compose pulls the GHCR package.
docker compose pull
docker compose up -d
```

Default package: `ghcr.io/nosttaw/trading-agent:latest`. Pin an immutable workflow build with `TRADING_AGENT_TAG=<commit-sha>` in `.env`. The package workflow tests and builds every push, publishes every pushed commit under its full SHA, and updates `latest` only from `main`; pull requests build without publishing. Arcane deployment uses `docker-compose.arcane.yml`; editing repository Compose does not automatically redeploy an existing Arcane project.

Open <http://localhost:8000>. Health: <http://localhost:8000/api/health>. Readiness: <http://localhost:8000/api/readiness>.

## Beginner workflow

1. Open **Settings → Alpaca connections**, save Market Data credentials, select the entitled feed, then test it.
2. Optionally save server-side AI provider settings. Test connection. API keys return masked only.
3. Open **Research Sessions → New research session**. Choose a suggested prompt, use the guided prompt builder, or write complete Advanced instructions. Custom prompts cannot bypass reviewed-template, validation, security, or risk restrictions.
4. Keep default Continuous interval to start each candidate after the previous finishes, or select 1/5/10/15/30/60 minutes. Set candidate/time/token ceilings.
5. Start. SQLite persistence plus the backend scheduler continue after browser closure.
6. Pause/resume, or choose **Stop & backtest all**.
7. Review all attempts—including duplicates/invalid output—and backtest outcomes.
8. Search/sort/pin up to four compatible results. Inspect rules, code, trades, warnings, lineage.
9. Select **Test on Live Data**. Confirm a fresh immutable simulated account.
10. Current market-data ingestion remains waiting until configured. It cannot fall back to broker paper/live.

## Architecture

One FastAPI app, one React/Vite UI, one SQLite deployment database. Minimal implementation; no microservices. The backend scheduler scans persistent due sessions every five seconds. Each generation updates `next_run_at`; downtime causes one recovery attempt, never a burst of missed ticks. `in_flight` prevents overlap in this single-instance deployment. Production scale-out requires DB-backed compare-and-swap leases or PostgreSQL row locks.

Schema: `backend/migrations/001_initial.sql`. File plan: `PLAN.md`.

AI output is schema-validated. It may select one of three reviewed templates and a bounded variant. No returned source is executed. Invalid provider responses remain visible. Provider/model/profile never changes silently.

Backtesting is a custom deterministic engine rather than a third-party library. Reason: required shared reviewed-template semantics, exact next-event timing, narrow initial scope, zero extra engine. Limit: not a general-purpose institutional simulator. Details: [`docs/strategy-interface.md`](docs/strategy-interface.md).

## Stop-on-positive research mode

Research sessions can use **Test each; stop when positive**. Each newly generated reviewed-template candidate is immediately backtested on the configured frozen historical period. Generation stops at the first `net_return_percent > 0` after configured costs, or at the existing candidate, duration, or token ceiling. Positive historical return is a search stopping condition only—not validation or evidence of future profit.

## Universe Screen

The research-only Universe Screen applies strict US listing/metadata/liquidity/history filters, evaluates nine bounded reviewed variants, freezes development ranking before validator holdout processing, records multiple-testing diagnostics, and exports ASCII/JSON evidence. The requested reference-date screen conflicts with its sealed-holdout rule, so current strict runs correctly return an empty shortlist labelled unverified rather than fabricate a winner. See [docs/universe-screen.md](docs/universe-screen.md).

## Strategy Validation

Dedicated validation runs evaluate frozen reviewed strategies on recent completed Alpaca data. Recent 30/90/180/365-day or custom periods, immutable dataset fingerprints, period-use/holdout access provenance, fixed-parameter rolling/expanding walk-forward windows, realistic cost/delay stress, nearby-parameter sensitivity, robust metrics, seeded moving-block bootstrap intervals, concentration/regimes, frozen criteria, progress/cancellation, saved comparisons, and JSON/CSV export are implemented. See [docs/validation.md](docs/validation.md) for setup, definitions, assumptions, examples, and limitations. Validation never proves future profitability.

## Autonomous Alpaca paper strategies

Broker-paper sessions remain manual by default. An ACTIVE, reconciled session can separately enable autonomous paper execution with a strategy-bound typed confirmation. The server evaluates the reviewed immutable template on completed Alpaca bars, sizes entries to the approved maximum-order notional, exits the broker-reported long position, and uses deterministic per-bar client order IDs. Open, unknown, foreign, or unsupported short orders/positions block submission. Data/broker failures pause the session, retry reconciliation, then resume after recovery. Approval expiry, version mismatch, Stop, and Emergency Stop fail closed. This route is hard-coded to Alpaca's paper endpoint; real-money execution remains absent.

## Watchlist and research sources

Overview includes a persistent watchlist for up to 20 validated US-equity symbols. The server polls configured Alpaca bars, stores the latest 120 closes, and shows price, bar timestamp, polling heartbeat, timeframe, feed state, and sparkline. One-minute items poll at most every 15 seconds; slower intervals at most once per minute.

Research Sessions optionally use constrained public web search for inspiration. Stored source records include URL, title, retrieval time, excerpt, and content hash. Search output is treated as untrusted data inside AI prompts; retrieved instructions cannot override platform rules. No downloaded code or serialized model executes. Search failure yields clearly model-generated hypotheses without invented citations. Search-result titles are discovery metadata—not verified claims or evidence of profitability.

Historical and forward equity charts provide independent Buy/Sell marker filters. Markers represent recorded simulated fills only.

## Alpaca credentials

Open **Settings → Alpaca connections**. Credentials can be added separately for:

- Market data: `https://data.alpaca.markets`, with explicit IEX/SIP/delayed-SIP feed selection.
- Broker paper: `https://paper-api.alpaca.markets` only.
- Live: `https://api.alpaca.markets`; storage remains disabled unless the server live flag is enabled. Orders remain absent regardless.

Key IDs and secret keys are encrypted server-side with `APP_ENCRYPTION_KEY`; browser receives masked placeholders only. Connection tests call latest AAPL data or account status. Errors redact credentials. No mode fallback. Obtain keys from <https://app.alpaca.markets/>; select Paper Trading before creating paper keys.

Paper approval binds completed backtest, strategy/engine hashes, broker account, symbol, timeframe, allocation, order/position/loss/drawdown/frequency limits, and an expiry of at most seven days. Capital defaults to the maximum non-leveraged usable amount—minimum of broker cash, equity, and buying power. Maximum order defaults to 10% and maximum position to 25% of that frozen approval-time capital. New sessions start `HALTED`; reconciliation plus exact typed resume is required. Every intent persists before submission with deterministic client ID. Unknown outcomes block retries until reconciliation. Partial fill fields persist. Emergency Stop blocks entries, attempts cancellation of eligible pending paper orders, preserves positions, never liquidates automatically. The compact release exposes no automatic strategy-to-order scheduler; paper orders require the protected API and exact confirmation. This avoids unattended submission before market-data event ingestion and recovery are complete.

## Provider behavior

Supported explicit profiles:

- Chat Completions: `POST {base_url}/chat/completions`
- Responses: `POST {base_url}/responses`

Implemented: encrypted key/headers, connection test, non-streaming generation, local JSON validation, usage capture when reported, timeout, bounded repair attempts, actionable redacted errors.

Not assumed: model listing, streaming, tools, temperature support, JSON Schema support, Responses support. Remove `temperature` when an endpoint rejects it; the app never silently changes capabilities. Reliable dollar-metering is not assumed; hard candidate/time/token ceilings remain mandatory.

Endpoint protection resolves DNS before requests; blocks non-HTTPS remote endpoints, redirects, metadata/link-local/private/loopback/reserved/multicast addresses, embedded credentials, unsafe headers. Administrator exceptions use `AI_LOCAL_ENDPOINT_ALLOWLIST=host:port`. Arcane currently allows only `192.168.2.77:8000`; enter base URL `http://192.168.2.77:8000/v1`. This is defense in depth; production egress policy and a resolving proxy are still required against TOCTOU DNS rebinding.

## Testing

```bash
pytest -q
cd frontend && npm run build
```

Tests cover fail-closed orders, encrypted/masked secrets, SSRF blocks, local allowlist, unsafe headers, immediate sequential generation, configurable schedules, pause/resume, no missed-tick burst, stop/finalize, duplicates, chronological fill timing, immutable forward snapshot, fresh account, delayed-data validation, cancellation retention, audit chaining.

All broker tests are absence/fake-boundary tests. No real order is submitted.

## Deployment

One supported host: Docker Engine with Compose. See [`docs/operations.md`](docs/operations.md) for deployment, rollback, backup, restore test, updates, structured logs, limitations.

## Documentation

- [`docs/strategy-interface.md`](docs/strategy-interface.md) — Python API and execution semantics
- [`docs/security.md`](docs/security.md) — threat model and disabled capabilities
- [`docs/operations.md`](docs/operations.md) — deployment, backup, restore, update
- [`docs/references.md`](docs/references.md) — official provider/Alpaca sources and retrieval record

## Reference video disclosure

The YouTube page HTML was reachable on 2026-09-09 UTC, but browser automation/video playback was unavailable. The 5:08 segment was **not watched or claimed as matched**. Implementation follows the supplied results specification: session summary, sortable leaderboard, equity curves, metrics, detail tabs, trades, warnings, and up-to-four comparison.
