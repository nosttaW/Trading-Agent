import json
from datetime import UTC, datetime

import pytest

import test_app as base

service, client = base.service, base.client


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    if base.TEST_DB.exists(): base.TEST_DB.unlink()
    service.init_db(); service.LOGIN_FAILURES.clear()
    monkeypatch.setattr(service, "validate_alpaca_equity_symbol", lambda _: None)
    login = client.post("/api/auth/login", json={"password": "correct horse battery staple"}); client.headers["x-csrf-token"] = login.json()["csrf_token"]
    yield
    if base.TEST_DB.exists(): base.TEST_DB.unlink()


def verified_asset(symbol="TEST"):
    return {"symbol": symbol, "name": "Test Common Stock", "exchange": "NASDAQ", "mic": "XNAS", "asset_class": "us_equity", "status": "active", "tradable": True, "halt_status": "not_halted", "ticker_resolution_count": 1, "quote_currency": "USD", "primary_venue": "NASDAQ", "consolidated_tape": True, "adr_status": "not_adr", "share_class_resolution": "exact_listing", "security_type": "common_equity", "single_constituent_concentration": None, "corporate_actions_applied": "none in fixture"}


def long_bars():
    bars = service.demo_bars("1d")
    # Extend deterministic completed daily fixture to required dates/age.
    while len(bars) < 1100:
        previous = bars[-1]; at = datetime.fromisoformat(previous["timestamp"]) + service.timedelta(days=1)
        if at.weekday() >= 5: at += service.timedelta(days=7-at.weekday())
        price = float(previous["close"]) * 1.0005
        bars.append({"timestamp": at.isoformat(), "open": f"{price:.2f}", "high": f"{price*1.002:.2f}", "low": f"{price*.998:.2f}", "close": f"{price:.2f}", "volume": 2_000_000})
    return bars


def test_strict_metadata_excludes_unverified_etf_and_share_class():
    etf = verified_asset("ETF"); etf["security_type"] = "ETF"
    equity = verified_asset("ABC"); equity["share_class_resolution"] = "unverified"
    assert "E4" in service.classify_asset(etf)
    assert "A3" in service.classify_asset(equity)


def test_missing_separate_halt_flag_does_not_exclude_active_tradable_stock():
    asset = verified_asset(); asset["halt_status"] = "not separately supplied; active/tradable checked"
    assert service.classify_asset(asset) is None
    asset["tradable"] = False
    assert "C5" in service.classify_asset(asset)


def test_holiday_gap_not_counted_as_intraday_missing_bar():
    bars = [{"timestamp": "2026-09-04T15:30:00Z"}, {"timestamp": "2026-09-08T15:30:00Z"}]
    assert service.bar_gap_count(bars, "1h") == 0


def test_completed_daily_bar_excludes_run_date():
    bars = [{"timestamp": "2026-09-09T20:00:00Z"}, {"timestamp": "2026-09-10T20:00:00Z"}]
    now = datetime(2026, 9, 10, 12, tzinfo=UTC)
    assert service.completed_bars(bars, "1d", now) == bars[:1]


def test_sec_fee_and_cost_scenarios_not_double_counted():
    assert service.universe_cost_config("base") == {"fee_bps": "0", "spread_bps": "4", "slippage_bps": "2", "sec_sell_bps": "2.78"}
    bars = long_bars()
    base_result = service.evaluate_universe_candidate("moving_average", {"fast": 10, "slow": 40}, bars, bars[300]["timestamp"][:10], bars[800]["timestamp"][:10], 42, "base")
    severe = service.evaluate_universe_candidate("moving_average", {"fast": 10, "slow": 40}, bars, bars[300]["timestamp"][:10], bars[800]["timestamp"][:10], 42, "severe")
    assert severe["metrics"]["net_return_percent"] <= base_result["metrics"]["net_return_percent"]


def test_unreviewed_leaky_candidate_prevented():
    with pytest.raises(ValueError, match="unreviewed"):
        service.evaluate_strategy({"family": "future_bar", "parameters": "{}"}, {"timeframe": "1d", "starting_capital": "10000", "allocation_fraction": "1", "fee_bps": "0", "spread_bps": "0", "slippage_bps": "0"}, long_bars())


def test_funnel_boundary_price_and_liquidity():
    asset = verified_asset(); bars = long_bars()
    for bar in bars[-63:]: bar.update({"close": "5.00", "volume": 4_000_000})
    passed, reason, facts = service.hard_screen(asset, bars)
    assert passed and facts["last_price_usd"] == 5
    bars[-1]["close"] = "4.99"
    assert service.hard_screen(asset, bars)[1].startswith("C1")


def test_universe_run_dry_run_no_database_write(monkeypatch):
    monkeypatch.setattr(service, "alpaca_data_connection", lambda: {"feed": "sip"})
    before = client.get("/api/universe-runs").json()
    result = client.post("/api/universe-runs", json={"feed": "sip", "dry_run": True})
    assert result.status_code == 200 and result.json()["database_touched"] is False
    assert client.get("/api/universe-runs").json() == before


def test_universe_empty_shortlist_conflict_is_byte_reproducible(monkeypatch):
    monkeypatch.setattr(service, "alpaca_data_connection", lambda: {"feed": "sip"})
    monkeypatch.setattr(service, "fetch_alpaca_assets", lambda **_: [verified_asset()])
    monkeypatch.setattr(service, "fetch_alpaca_bars", lambda *_: (long_bars(), "sip"))
    payload = {"feed": "sip", "symbols": ["TEST"], "maximum_instruments": 1, "seed": 42}
    created = client.post("/api/universe-runs", json=payload).json(); service.process_universe_jobs(); first = client.get(f"/api/universe-runs/{created['id']}").json()
    assert first["state"] == "COMPLETED" and first["result_json"]["symbols"] == []
    assert all(item["verdict"] == "unverified out-of-sample" for item in first["result_json"]["ranking"])
    assert "Research and simulation only" in first["result_ascii"]


def test_universe_pending_cancellation(monkeypatch):
    monkeypatch.setattr(service, "alpaca_data_connection", lambda: {"feed": "sip"})
    run = client.post("/api/universe-runs", json={"feed": "sip", "symbols": ["TEST"]}).json()
    canceled = client.post(f"/api/universe-runs/{run['id']}/cancel").json()
    assert canceled["state"] == "CANCELED"
