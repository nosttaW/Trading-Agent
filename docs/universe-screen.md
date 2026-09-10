# Universe screen

Research-only ranking of long-only reviewed templates. No order path. Empty shortlist is valid.

## Protocol conflict and expected empty shortlist

The requested protocol contains two irreconcilable constraints:

1. C1/C2 require price and 63-day liquidity ending at the 2026-09-09 last completed bar **before ranking**.
2. E3 declares every 2026-01-01..2026-09-09 bar a sealed holdout that may only be touched after ranking freezes.

Using C1/C2 chooses the ranking universe with holdout information. The validator therefore labels every result `unverified out-of-sample` and declares no winner. It still emits funnel, frozen evidence, diagnostics, ASCII, and JSON. To permit a genuine winner, define C1/C2 using data ending 2025-12-31, or move the sealed holdout start after the hard-screen reference date.

G3 also conflicts with the reviewed catalog: nine candidates comprise three strategy families with bounded parameter variants. G3 says no two candidates may differ only by a threshold. Adding new strategy families would exceed this task's reviewed-template-only boundary. The limitation is explicit; no candidate is fabricated.

## Strict metadata

Alpaca `data.alpaca.markets` supplies bars. Alpaca's read-only asset directory supplies symbol, current active/tradable state, exchange, and name. It does not supply a separate point-in-time halt flag or establish ADR sponsorship/underlying/FX, closed-end NAV schedule, or ETF constituent concentration. C5 therefore uses Alpaca's current active/tradable status and records the halt-field limitation instead of misclassifying missing data as a halt. Strict mode still excludes instruments whose security type or other required metadata cannot be established. Frozen test/admin cache records can provide verified fields; production never infers them from performance data.

Accepted venues map to MICs: NYSE `XNYS`, Nasdaq `XNAS`, NYSE American `XASE`, NYSE Arca `ARCX`. Identifiers must be uppercase ASCII letters only. ETF concentration must be known and <=30%. ADR status must be verified. Share classes never merge.

IEX is recorded as minority-venue coverage. Volume-derived ranking under IEX is not represented as consolidated volume; strict shortlist remains empty. SIP/delayed SIP must match the configured feed exactly.

## Screen order

A1–A4/E1–E5 metadata, then:

1. Last completed price >= USD 5.00.
2. 63-bar median volume >=500,000 shares/day and median dollar volume >=USD 20,000,000/day.
3. Listing age >=750 completed daily bars.
4. Development history >=500 completed daily bars.
5. Active/tradable per latest frozen Alpaca asset metadata. A separate point-in-time halt flag is unavailable and disclosed.
6. Exact uppercase ASCII ticker resolution.

Funnel counts are cumulative. Weekends and cross-session gaps, including Labor Day 2026-09-07, are not intraday missing bars. No interpolation.

## Ranking

Development: 2023-01-01..2025-12-31, three calendar-year thirds. Nine reviewed variants across moving average, RSI, channel breakout. Scores use the exact 30/20/15/10/10/10/5 components. Equal scores use Sortino, drawdown, trades, turnover, dollar volume, symbol. Raw gross return never reranks.

Sensitivity uses each parameter +/-10% of its catalog range, maximum three parameters. Candidates below 15 development round trips reject. Forty trades saturates sample score. Concentration penalizes top-five winning trades >30% and best positive month >35%.

Multiplicity records instruments × 9 candidates and Bonferroni alpha. The implementation does not claim a deflated Sharpe/PBO estimate. Raw Sharpe is descriptive only.

## Costs

Commission USD 0/share. SEC fee: 2.78 bps sell notional. Spread per side: base 2 bps, adverse 5 bps, severe 10 bps. Slippage per side: base 2 bps, adverse 5 bps, severe 10 bps. Half-spread is represented by passing total spread as 4/10/20 bps to the shared engine. Costs charge once. USD0.01 tick assumption. Long-only, one position, no leverage/borrow. Signal at close; earliest fill next open. No-fill circuit-breaker auctions, partial fills, open liquidity, market impact, auctions are unsupported because daily bars cannot prove them; execution-realism evidence is conservative.

## CLI

```bash
python -m backend.screens.cli --universe us_equities,us_etfs --feed sip --days 750 \
  --cutoff 2025-12-31 --holdout-start 2026-01-01 --cost base,adverse,severe \
  --candidates 9 --seed 42 --top 3 --out ascii,json --offline
```

Options:

- `--offline`: reuse cached asset metadata/daily bars; never fetch.
- `--feed`: required configured feed entitlement (`sip`, `iex`, `delayed_sip`).
- `--seed`: deterministic calculations and artifact figures.
- `--dry-run`: validate/estimate only; does not initialize or write the database.
- `--symbols`: optional comma-separated strict subset.
- `--maximum-instruments`: bound; default 100, maximum 500.
- `--out`: `ascii`, `json`, or both.

The CLI imports `backend.app`; it does not create another strategy engine.

## Ready-to-paste configurations

No winner/alternates/configurations are emitted under the contradictory protocol. Producing three would fabricate shortlisted evidence. Once the reference/holdout conflict is corrected, configurations must be generated from passing frozen holdout rows and include exact name, provider, prompt, symbol, daily timeframe, 9 candidates, continuous interval, 180-minute duration, and token ceiling divided across candidate count.

## Reproducibility and limits

Frozen datasets record provider/feed/symbol/timeframe/first/last/retrieval/hash/adjustment. Same frozen inputs, seed, engine version produce byte-identical metric figures. Retrieval timestamp/run ID naturally differ between new runs. Offline reruns avoid fetches.

Corporate-action event details are unavailable from bar payloads; `adjustment=all` is recorded, never falsely expanded into event lists. Exact holiday calendar, opening liquidity, auction halts, ETF holdings, ADR sponsorship, and issuer share-class mapping require reviewed metadata sources not currently integrated.

Research and simulation only. Not investment advice. No profit promise.
Backtests and simulations do not predict future returns. Execution may differ materially.
