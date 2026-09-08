# Guardrail Trading MVP

**Not investment advice. Backtests, AI research, and paper trading do not predict future returns. Execution can differ materially. Loss of all allocated capital remains possible. No profit is promised.**

Small local demo. One FastAPI backend, React UI, SQLite. It deliberately does **not** transmit a broker order, fabricate fills, use an AI key, fetch market data, or enable live trading.

## Run

```sh
python -m pip install -r backend/requirements.txt pytest
python -m uvicorn --app-dir backend app:app --reload
cd frontend && npm install && npm run dev
python -m pytest -q
```

Or `docker compose up`. Open `http://localhost:5173`.

## Production image

```sh
docker build -t guardrail-trading:latest .
docker run --rm -p 8000:8000 -v guardrail-trading-data:/data guardrail-trading:latest
```

Open `http://localhost:8000`. The image serves the compiled UI and API. It persists SQLite only in `/data`, runs unprivileged/read-only, has no broker credentials, and pins `ENABLE_LIVE_TRADING=false` in `docker-compose.arcane.yml`.

GitHub Actions packages `ghcr.io/nosttaW/trading-agent` on `main` pushes. Arcane builds the same local image from `Dockerfile`; project manifest: `arcane-project.json`.

## Implemented boundary

`declarative candidate → strict schema → deterministic demo chronological backtest → typed paper approval → immutable strategy/engine hash check → independent risk gate → broker adapter boundary → audit log`

- Strategy JSON rejects unknown fields, executable code, non-finite/negative monetary values, unsafe allocations, unsupported symbols/timeframes, ambiguous crossover rules.
- Monetary calculations use `Decimal`; timestamps use UTC.
- Demo bars are explicitly labeled deterministic local-development data. No fabricated data is ever represented as real market data.
- Backtests model a conservative simplified 5 bps cost/slippage fill. `ponytail:` ceiling: daily close-only, one long-only MA crossover. Upgrade only after licensed historical data, corporate-action handling, calendars, walk-forward/holdout controls, paper-forward reconciliation.
- Paper approval requires an exact typed authorization. Strategy and engine hashes bind it. Live approvals/orders are server-blocked even if `ENABLE_LIVE_TRADING=true`; client requests cannot enable live operation.
- AI, web research, remote content ingestion, autonomous strategy creation, order streaming, market streaming, account storage, reconciliation, broker credentials, paper/live submission are intentionally absent. No incomplete integration receives an order.

## Alpaca verification record

Retrieved **2026-09-08 UTC**. Official docs were fetched with a standard browser user agent. Paths/endpoints, entitlements, rate limits, SDK status, order lifecycle/state mapping, fractional and extended-hours rules must be re-verified against these sources immediately before implementing authenticated calls:

- Trading API: <https://docs.alpaca.markets/us/docs/trading-api>
- Authentication: <https://docs.alpaca.markets/us/reference/authentication-2>
- API reference: <https://docs.alpaca.markets/docs/api-references/trading-api/>
- Market Data API: <https://docs.alpaca.markets/docs/api-references/market-data-api/>
- SDK documentation: <https://alpaca.markets/docs/api-documentation/client-sdk/>
- Rate limits: <https://docs.alpaca.markets/docs/broker-api-rate-limits>

The adapter pins distinct declared paper/live URLs: `https://paper-api.alpaca.markets` and `https://api.alpaca.markets`. It does not infer mode, fall back, send credentials, or substitute fills. Its required interface enumerates all requested broker operations but fails closed until the above review creates a tested authenticated implementation.

## Production gate

Do not use this demo to trade. Before any paper broker submission: implement tested authenticated official Alpaca calls; account/environment identity verification; asset tradability; order lifecycle; reconciliation/recovery; audit-event immutability; approved data feeds; calendar/halts; all listed validation stages; paper-forward requirements; secrets management; authenticated multi-user UI; PostgreSQL migration; monitoring/alerts; independent deployment of risk controls. Before live: every paper-forward requirement, fresh typed live approval, all hash checks, reconciliation, safety halt, persistent red `LIVE` UI, max 24-hour initial authorization.
