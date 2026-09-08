import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1] / "backend"))
from fastapi.testclient import TestClient
from app import app

client = TestClient(app)
STRATEGY = {"name":"SPY moving-average crossover","version":1,"hypothesis":"Medium-term trend persistence may exceed modeled transaction costs.","market":"US_EQUITIES","instrument":"SPY","timeframe":"1d","entry":{"type":"moving_average_cross","fast_period":20,"slow_period":100,"direction":"above"},"exit":{"type":"moving_average_cross","fast_period":20,"slow_period":100,"direction":"below"},"position":{"type":"fixed_fraction","fraction":"0.05"},"risk":{"max_order_notional":"100.00","max_position_notional":"500.00","max_daily_loss":"25.00","max_drawdown_percent":"5.00","stop_loss_percent":"2.00"},"schedule":{"regular_hours_only":True,"timezone":"America/New_York"}}

def test_strategy_rejects_unknown_field():
    assert client.post("/api/strategies", json={**STRATEGY, "code":"import os"}).status_code == 422

def test_backtest_is_deterministic():
    created = client.post("/api/strategies", json=STRATEGY).json()
    first = client.post(f"/api/strategies/{created['strategy_id']}/backtest").json()["result"]
    second = client.post(f"/api/strategies/{created['strategy_id']}/backtest").json()["result"]
    assert first == second and first["trade_count"] >= 0

def test_kill_switch_halts_excess_drawdown():
    from app import Risk, kill_switch
    risk = Risk(max_order_notional="100", max_position_notional="500", max_daily_loss="25", max_drawdown_percent="5", stop_loss_percent="2")
    assert kill_switch("5.01", risk)["state"] == "HALTED"

def test_monitoring_uses_immutable_baseline():
    created = client.post("/api/strategies", json=STRATEGY).json()
    backtest = client.post(f"/api/strategies/{created['strategy_id']}/backtest").json()
    monitor = client.get(f"/api/strategies/{created['strategy_id']}/monitoring").json()
    assert monitor["baseline"]["hash"] == backtest["checkpoint_hash"]
    assert monitor["lifecycle"]["execution"] == "hard-blocked"

def test_live_order_never_reaches_network():
    assert client.post("/api/orders", json={"strategy_id":"none","symbol":"SPY","side":"buy","quantity":"1","price":"100","mode":"live"}).status_code == 403
