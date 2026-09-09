# Strategy Validation

Strategy Validation asks whether a frozen reviewed strategy retains useful performance on recent data not known to have been used for development. It does **not** prove future profitability.

## Run

1. Complete a current backtest.
2. Open **Strategy Validation** → **New validation**.
3. Select the immutable strategy/version, one to five US-equity symbols, timeframe, recent 30/90/180/365-day preset or custom dates.
4. Freeze walk-forward mode, costs, fill delay, criteria, bootstrap seed.
5. Queue. The existing server scheduler executes one validation job at a time. Closing the browser does not stop it.
6. Reopen the saved run. Export JSON for full provenance or CSV for summary metrics.

Cancellation removes pending work or sets a cooperative cancellation flag checked between symbols. A process interruption leaves `RUNNING`, never `COMPLETED`; current single-host recovery requires cancellation and a new run.

## Periods and holdouts

The originating backtest end date is recorded as the development/tuning cutoff. Prior backtest periods are seeded into `strategy_period_uses`; overlap produces a warning. Unknown history is labeled **unverified out-of-sample**, not genuine unseen evidence.

The strategy source/hash and parameters freeze before evaluation. Viewing a completed holdout through the UI increments an access ledger. A second view marks independence compromised. Repeated analysis or tuning requires fresh unseen data for another independent claim.

Rolling and expanding modes use fixed parameters. Training windows provide chronology/provenance only; optimization is intentionally not implemented. Test windows start after purge and embargo bars. Step must be at least test length, preventing overlapping scored windows and double-counted combined returns. Indicator warm-up may precede score start. Positions flatten at each scored-window end.

## Data

Alpaca is the only source. Requests use `adjustment=all`, configured feed entitlement, UTC storage, New York regular-session filtering, completed bars only. Exact bar JSON is stored in `market_datasets`; SHA-256 fingerprints make runs reproducible. Exact-range cached data is reused.

Data quality reports provider, feed, entitlement caveat, requested/actual coverage, last completed bar, freshness, weekday expected bars, same-session gaps, timezone, adjustment, cache status. Weekday expectations do not encode holidays/early closes. No synthetic fallback exists. Provider history, entitlement, rate limits, sparse IEX coverage, corporate-action methodology, and late corrections can constrain evidence.

## Execution

A close-derived signal fills no earlier than a later bar open. Delay is 1–5 bars. Fees are charged explicitly once per side. Half-spread and slippage adjust fill price once. Intraday tests use regular hours and daily flattening. Base, adverse, severe cost multipliers are reported. Nearby parameters and delays measure fragility; they never auto-select a winner from holdout results.

Unsupported: liquidity capacity, partial fills, queue position, market impact, intrabar path, exact auction behavior, exchange holidays/early closes without an exchange-calendar provider.

## Metrics

- Net return: compounded portfolio return after modeled costs.
- CAGR: annualized geometric return only for at least 30 calendar days.
- Volatility: sample standard deviation of scored-bar returns, annualized by bars/year.
- Sharpe: arithmetic mean return divided by sample standard deviation, zero risk-free rate.
- Sortino: arithmetic mean divided by RMS nonpositive returns, zero risk-free rate.
- Drawdown: peak-to-trough percentage; duration measured in scored bars.
- Exposure: fraction of scored bars holding a position.
- Turnover: gross traded notional divided by starting capital.
- Expectancy: mean closed-trade net P/L.
- Holding period: mean closed-trade duration in bars.
- Profit factor: gross winning P/L divided by absolute gross losing P/L.
- Buy-and-hold: matching scored dates/capital with the same per-side cost assumptions.
- Cash: 0%, explicitly excluding interest.

Undefined metrics remain `null`/unavailable. They are never displayed as zero.

Confidence intervals use a seeded moving-block bootstrap over contiguous scored-return blocks. The default 500 samples and cube-root block length preserve short-range temporal dependence better than IID sampling. Results remain sensitive to block choice, regime shifts, short samples, and nonstationarity.

Concentration reports the share of gross winning P/L from the top 10% of winning trades and the share of positive return from the top 10% of positive scored bars. Regimes use trailing information only: close versus trailing 20-bar mean. This is descriptive, not causal.

## Assessment

Criteria freeze in the run specification: minimum trades, maximum drawdown, minimum net return. Output is exactly one of:

- `meets configured criteria`
- `does not meet configured criteria`
- `insufficient evidence`

Candidate/variant counts and previous period use support multiple-testing warnings. No output says “proven” or guarantees profitability.

## Limits

Fixed-parameter walk-forward works. Training-window optimization is not implemented. Multi-symbol runs evaluate the same frozen strategy independently per symbol; they are not a portfolio simulation. Combined OOS equity joins non-overlapping windows; cross-symbol equity is not aggregated. Runtime is bounded to five symbols, five years, 500,000 fetched bars per symbol, six sensitivity variants, three delays, 50 windows, and 2,000 bootstrap samples. Single-host SQLite supports one validation job at a time.
