import json
from datetime import UTC, datetime, timedelta

import pytest

import test_app as base

service, client = base.service, base.client


@pytest.fixture(autouse=True)
def clean_validation_database(monkeypatch):
    if base.TEST_DB.exists(): base.TEST_DB.unlink()
    service.init_db(); service.LOGIN_FAILURES.clear()
    monkeypatch.setattr(service, "alpaca_data_connection", lambda: True)
    monkeypatch.setattr(service, "validate_alpaca_equity_symbol", lambda _: None)
    monkeypatch.setattr(service, "fetch_alpaca_bars", lambda *args: (service.demo_bars(args[1]), "iex"))
    login = client.post("/api/auth/login", json={"password": "correct horse battery staple"}); client.headers["x-csrf-token"] = login.json()["csrf_token"]
    yield
    if base.TEST_DB.exists(): base.TEST_DB.unlink()


def config(timeframe="1d"):
    return {"timeframe": timeframe, "starting_capital": "10000", "allocation_fraction": "0.25", "fee_bps": "1", "spread_bps": "2", "slippage_bps": "3", "minimum_trade_count": 0}


def candidate(family="moving_average", parameters=None):
    return {"family": family, "parameters": json.dumps(parameters or {"fast": 10, "slow": 40})}


def test_completed_bar_filter_excludes_open_bar():
    now = datetime(2026, 1, 5, 15, 2, tzinfo=UTC)
    bars = [{"timestamp": "2026-01-05T15:00:00Z"}, {"timestamp": "2026-01-05T15:02:00Z"}]
    assert service.completed_bars(bars, "1m", now) == bars[:1]


def test_warmup_excluded_and_fill_after_signal():
    bars = service.demo_bars("1d")
    result = service.evaluate_strategy(candidate(), config(), bars, score_start=100, seed=9)
    assert result["equity_curve"][0]["at"] == bars[100]["timestamp"]
    timestamps = [bar["timestamp"] for bar in bars]
    assert all(timestamps.index(trade["entry_time"]) > 100 for trade in result["trades"])


def test_costs_reduce_returns_without_double_counting():
    bars = service.demo_bars("1d")
    free = service.evaluate_strategy(candidate(), {**config(), "fee_bps": "0", "spread_bps": "0", "slippage_bps": "0"}, bars)
    costly = service.evaluate_strategy(candidate(), config(), bars)
    assert costly["metrics"]["net_return_percent"] <= free["metrics"]["net_return_percent"]
    assert float(costly["metrics"]["costs"]) >= 0


def test_benchmark_uses_matching_scored_dates_and_cash_is_explicit():
    bars = service.demo_bars("1d")
    result = service.evaluate_strategy(candidate(), config(), bars, score_start=200, score_end=400)
    assert result["equity_curve"][0]["at"] == bars[200]["timestamp"]
    assert result["metrics"]["benchmark_return_percent"] is not None
    assert result["metrics"]["cash_return_percent"] == 0


def test_missing_observations_report_coverage_gap():
    bars = service.demo_bars("15m")[:8]; missing = bars[:3] + bars[4:]
    assert service.bar_gap_count(missing, "15m") == 1
    assert service.expected_bar_count("2026-01-05", "2026-01-09", "1d") == 5
    assert service.expected_bar_count("2026-01-05", "2026-01-09", "1m") == 1950


def test_undefined_metrics_remain_none():
    bars = service.demo_bars("1d")[:3]
    result = service.evaluate_strategy(candidate("channel_breakout", {"lookback": 100, "exit": 40}), config(), bars)
    assert result["metrics"]["win_rate_percent"] is None
    assert result["metrics"]["profit_factor"] is None
    assert result["metrics"]["cagr_percent"] is None


def test_seeded_block_bootstrap_reproducible():
    returns = [((i % 7) - 3) / 1000 for i in range(100)]
    assert service.block_bootstrap(returns, 42, 200) == service.block_bootstrap(returns, 42, 200)
    assert service.block_bootstrap(returns, 42, 200) != service.block_bootstrap(returns, 43, 200)


def test_overlapping_walk_forward_windows_rejected():
    result = client.post("/api/validations", json={"backtest_id": "none", "symbols": ["SPY"], "timeframe": "1d", "testing_days": 30, "step_days": 5})
    assert result.status_code == 422


def test_unreviewed_leaky_strategy_is_prevented():
    bars = service.demo_bars("1d")
    with pytest.raises(ValueError, match="unreviewed"):
        service.evaluate_strategy({"family": "future_close_leak", "parameters": "{}"}, config(), bars)


def test_validation_api_create_process_reopen_export_and_cancel(monkeypatch):
    completed = base.create_completed_session(monkeypatch); backtest = completed["backtests"][0]
    recent = service.demo_bars("1d")[-120:]
    monkeypatch.setattr(service, "fetch_alpaca_bars", lambda *_: (recent, "iex"))
    created = client.post("/api/validations", json={"backtest_id": backtest["id"], "symbols": ["SPY"], "timeframe": "1d", "period_preset": "custom", "start_date": recent[-90]["timestamp"][:10], "end_date": recent[-1]["timestamp"][:10], "minimum_trades": 0})
    assert created.status_code == 200 and created.json()["state"] == "PENDING"
    run_id = created.json()["id"]
    assert service.process_validation_jobs() == 1
    first = client.get(f"/api/validations/{run_id}").json()
    reopened = client.get(f"/api/validations/{run_id}").json()
    assert first["state"] == "COMPLETED" and first["result"] == reopened["result"]
    assert first["dataset_refs"][0]["fingerprint"]
    assert client.get(f"/api/validations/{run_id}/export?format=json").status_code == 200
    assert client.get(f"/api/validations/{run_id}/export?format=csv").status_code == 200
    pending = client.post("/api/validations", json={"backtest_id": backtest["id"], "symbols": ["SPY"], "timeframe": "1d", "period_preset": "30d"}).json()
    canceled = client.post(f"/api/validations/{pending['id']}/cancel")
    assert canceled.status_code == 200 and canceled.json()["state"] == "CANCELED"


def test_same_frozen_inputs_are_numerically_identical():
    bars = service.demo_bars("15m")
    kwargs = {"score_start": 100, "score_end": 500, "seed": 123, "execution_delay_bars": 2}
    first = service.evaluate_strategy(candidate(), config("15m"), bars, **kwargs)
    second = service.evaluate_strategy(candidate(), config("15m"), bars, **kwargs)
    assert first == second
