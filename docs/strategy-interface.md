# Reviewed Python strategy interface

Arbitrary generated/uploaded Python is **not executed**. The downloadable source documents the interface. Trusted code executes a reviewed template ID and bounded JSON parameters.

```python
from strategy_lab import Observation, Portfolio, StrategyResult

TEMPLATE_ID = "moving_average"
PARAMETERS = {"fast": 20, "slow": 80}

def initialize(config):
    return {"bars_seen": 0}

def on_closed_bar(config, observation: Observation, portfolio: Portfolio, state):
    state = {"bars_seen": state["bars_seen"] + 1}
    target = observation.reviewed_signal(TEMPLATE_ID, PARAMETERS)
    return StrategyResult(
        target_fraction=target,
        state=state,
        diagnostics={"signal": target},
    )
```

## Inputs

- `config`: immutable, JSON-compatible configuration.
- `observation`: bars available through the deterministic event timestamp. Future bars unavailable.
- `portfolio`: normalized cash, positions, pending exposure; immutable to strategy code.
- `state`: explicit bounded JSON object.

No broker client, API key, DB client, filesystem handle, network tool, future bar, arbitrary import, or order-submission function exists.

## Output

- `target_fraction`: reviewed templates produce `0` or configured long-only allocation.
- `state`: bounded JSON-compatible data.
- `diagnostics`: optional bounded JSON values.

Trusted host validates every result. NaN/Infinity, unknown fields, oversized output, unsupported targets, shorts, and unsafe quantities fail.

## Current templates

1. Moving-average crossover: simple arithmetic means. Long when fast MA exceeds slow MA.
2. RSI recovery: simple average gains/losses over the configured period. Long below entry threshold; exit above exit threshold. Zero losses yields RSI 100.
3. Price-channel breakout: enter above the maximum prior close; exit below the minimum prior close. Current close is excluded from channel construction.

## Deterministic semantics

- **Initialization:** fresh state, virtual cash, zero positions.
- **Warm-up:** missing lookback returns no position change.
- **Evaluation:** completed daily bar only.
- **Fill:** resulting intent fills no earlier than next eligible bar open.
- **Missing data:** no forward fill. Dataset validation must reject unordered, duplicate, missing, nonpositive, or stale events before non-demo use.
- **State:** safe JSON only; never pickle.
- **Sessions:** US-equity regular-session daily bars. UTC internally.
- **Corporate actions:** absent from deterministic demo fixture. Real historical use blocked until licensed adjusted/raw provenance and event-time semantics are implemented.
- **Stops/targets:** unsupported; no same-bar ambiguity is hidden.
- **Reversal:** long-only; exit before any later re-entry.
- **Fractional quantities:** round down to 0.001 share.
- **Orders:** simulated next-open market intent only. Limit, stop, partial-fill, and unfilled-order models unavailable.
- **Costs:** fee + half-spread + slippage applied explicitly. No queue-position claim.

## Backtest conventions

Daily Sharpe uses arithmetic daily returns, zero risk-free rate, sample standard deviation, annualization `sqrt(252)`. Sortino uses root-mean-square nonpositive daily returns and `sqrt(252)`. Undefined values render as unavailable. Benchmark: buy-and-hold first close to final close without benchmark costs. These conventions are descriptive only.

The UI describes 60/20/20 development/validation/final-holdout configuration. The compact demo provides full-period descriptive metrics but does not claim completed walk-forward, parameter-sensitivity, or untouched final-holdout analysis. Ranking excludes holdout claims and never labels maximum return “best.”
