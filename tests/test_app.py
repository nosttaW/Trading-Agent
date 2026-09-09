import os
import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest
from cryptography.fernet import Fernet
from fastapi.testclient import TestClient

TEST_DB = Path(__file__).parent / "test-trading.db"
os.environ["TRADING_DB_PATH"] = str(TEST_DB)
os.environ["APP_ENCRYPTION_KEY"] = Fernet.generate_key().decode()
os.environ["SESSION_SECRET"] = "test-session-secret-that-is-at-least-32-characters"
os.environ["COOKIE_SECURE"] = "false"
sys.path.insert(0, str(Path(__file__).parents[1] / "backend"))
import app as service

os.environ["ADMIN_PASSWORD_HASH"] = service.password_hash("correct horse battery staple")
client = TestClient(service.app)


@pytest.fixture(autouse=True)
def clean_database():
    if TEST_DB.exists():
        TEST_DB.unlink()
    service.init_db()
    service.LOGIN_FAILURES.clear()
    login = client.post("/api/auth/login", json={"password": "correct horse battery staple"})
    assert login.status_code == 200
    client.headers["x-csrf-token"] = login.json()["csrf_token"]
    yield
    if TEST_DB.exists():
        TEST_DB.unlink()


def session_config(**changes):
    value = {
        "name": "Controlled fifteen minute research",
        "instructions": "Explore simple long-only rules and preserve every generated attempt.",
        "maximum_candidates": 3,
        "generation_interval_minutes": 15,
        "generate_immediately": True,
    }
    value.update(changes)
    return value


def create_completed_session():
    created = client.post("/api/research-sessions", json=session_config()).json()
    started = client.post(f"/api/research-sessions/{created['id']}/start").json()
    assert started["state"] == "GENERATING"
    completed = client.post(f"/api/research-sessions/{created['id']}/control", json={"action": "stop"}).json()
    return completed


def test_authentication_and_csrf_enforced():
    anonymous = TestClient(service.app)
    assert anonymous.get("/api/providers").status_code == 401
    assert anonymous.post("/api/auth/login", json={"password": "wrong password"}).status_code == 401
    login = anonymous.post("/api/auth/login", json={"password": "correct horse battery staple"})
    assert login.status_code == 200
    assert login.cookies.get(service.SESSION_COOKIE)
    assert "HttpOnly" in login.headers["set-cookie"] and "SameSite=strict" in login.headers["set-cookie"]
    assert anonymous.get("/api/providers").status_code == 200
    assert anonymous.post("/api/research-sessions", json=session_config()).status_code == 403
    anonymous.headers["x-csrf-token"] = login.json()["csrf_token"]
    assert anonymous.post("/api/research-sessions", json=session_config()).status_code == 200
    assert anonymous.post("/api/auth/logout").status_code == 200
    assert anonymous.get("/api/providers").status_code == 401


def test_tampered_session_rejected():
    anonymous = TestClient(service.app)
    anonymous.cookies.set(service.SESSION_COOKIE, "tampered.token")
    assert anonymous.get("/api/providers").status_code == 401


def test_login_rate_limit():
    anonymous = TestClient(service.app)
    for _ in range(service.LOGIN_MAX_FAILURES):
        assert anonymous.post("/api/auth/login", json={"password": "wrong"}).status_code == 401
    assert anonymous.post("/api/auth/login", json={"password": "correct horse battery staple"}).status_code == 429


def test_status_fails_closed_for_execution():
    result = client.get("/api/status").json()
    assert result["mode"] == "DEMO"
    assert result["arbitrary_python_enabled"] is False
    assert result["broker_submission_enabled"] is False
    assert client.post("/api/orders", json={}).status_code == 403


def test_provider_secrets_encrypted_and_masked():
    payload = {"name": "Compatible API", "base_url": "https://api.openai.com/v1", "api_key": "top-secret", "model_id": "test-model", "profile": "chat_completions"}
    saved = client.post("/api/providers", json=payload)
    assert saved.status_code == 200, saved.text
    body = saved.json()
    assert body["api_key_masked"] == "••••••••"
    assert "top-secret" not in saved.text
    with service.connect() as connection:
        row = connection.execute("SELECT encrypted_api_key FROM providers").fetchone()
    assert "top-secret" not in row[0]
    assert service.decrypt_secret(row[0]) == "top-secret"


@pytest.mark.parametrize("url", [
    "http://169.254.169.254/latest/meta-data",
    "http://127.0.0.1:8000/v1",
    "https://localhost/v1",
    "ftp://example.com/v1",
    "https://user:pass@example.com/v1",
])
def test_provider_ssrf_destinations_blocked(url):
    result = client.post("/api/providers", json={"name": "Blocked", "base_url": url, "model_id": "model"})
    assert result.status_code == 422


def test_explicit_local_endpoint_allowlist(monkeypatch):
    monkeypatch.setenv("AI_LOCAL_ENDPOINT_ALLOWLIST", "127.0.0.1:11434")
    result = client.post("/api/providers", json={"name": "Local model", "base_url": "http://127.0.0.1:11434/v1", "model_id": "local", "api_key": None})
    assert result.status_code == 200, result.text


def test_unsafe_custom_headers_rejected():
    result = client.post("/api/providers", json={"name": "Unsafe", "base_url": "https://api.openai.com/v1", "model_id": "model", "custom_headers": {"Authorization": "secret"}})
    assert result.status_code == 422


def test_session_immediate_generation_and_frozen_config():
    created = client.post("/api/research-sessions", json=session_config()).json()
    original_hash = created["config_hash"]
    started = client.post(f"/api/research-sessions/{created['id']}/start").json()
    assert started["state"] == "GENERATING"
    assert started["generation_count"] == 1
    assert len(started["candidates"]) == 1
    assert started["config_hash"] == original_hash
    assert datetime.fromisoformat(started["next_run_at"]) > datetime.now(UTC)


def test_pause_resume_and_no_missed_tick_burst():
    created = client.post("/api/research-sessions", json=session_config()).json()
    started = client.post(f"/api/research-sessions/{created['id']}/start").json()
    paused = client.post(f"/api/research-sessions/{created['id']}/control", json={"action": "pause"}).json()
    assert paused["state"] == "PAUSED" and paused["next_run_at"] is None
    resumed = client.post(f"/api/research-sessions/{created['id']}/control", json={"action": "resume"}).json()
    assert resumed["state"] == "GENERATING" and resumed["generation_count"] == 1
    assert datetime.fromisoformat(resumed["next_run_at"]) > datetime.now(UTC)


def test_stop_backtests_every_valid_candidate():
    completed = create_completed_session()
    valid = [item for item in completed["candidates"] if item["status"] == "VALID"]
    assert completed["state"] == "COMPLETED"
    assert len(completed["backtests"]) == len(valid) == 1
    result = completed["backtests"][0]
    assert result["status"] == "COMPLETED"
    assert result["metrics"]["trade_count"] >= 0
    assert len(result["equity_curve"]) == 620


def test_duplicate_attempt_is_retained():
    created = client.post("/api/research-sessions", json=session_config(allowed_families=["moving_average"], maximum_candidates=4)).json()
    client.post(f"/api/research-sessions/{created['id']}/start")
    service.create_candidate(created["id"])
    service.create_candidate(created["id"])
    service.create_candidate(created["id"])
    session = client.get(f"/api/research-sessions/{created['id']}").json()
    assert len(session["candidates"]) == 4
    assert session["candidates"][-1]["status"] == "DUPLICATE"


def test_backtest_fill_is_after_signal_bar():
    completed = create_completed_session()
    backtest = completed["backtests"][0]
    bars = service.demo_bars()
    timestamps = [bar["timestamp"] for bar in bars]
    for trade in backtest["trades"]:
        assert trade["entry_time"] in timestamps
        assert timestamps.index(trade["entry_time"]) >= 1


def test_live_data_test_creates_immutable_fresh_snapshot_without_broker_path():
    completed = create_completed_session()
    backtest = completed["backtests"][0]
    payload = {
        "backtest_id": backtest["id"], "data_provider": "alpaca", "entitlement": "delayed", "delay_minutes": 15,
        "starting_virtual_cash": "12000.00", "confirmation": "Start Live Data Test",
    }
    created = client.post("/api/live-tests", json=payload)
    assert created.status_code == 200, created.text
    result = created.json()
    assert result["mode"] == "LIVE_DATA_SIMULATED"
    assert result["backtest_id"] == backtest["id"]
    detail = client.get(f"/api/backtests/{backtest['id']}").json()
    assert result["strategy_hash"] == detail["source_hash"]
    assert result["virtual_cash"] == result["equity"] == "12000.00"
    assert result["positions"] == result["fills"] == []
    assert result["state"] == "WAITING_FOR_DATA"
    assert client.post("/api/orders", json={"mode": "live"}).status_code == 403


def test_live_data_delay_must_be_truthfully_labeled():
    completed = create_completed_session()
    result = client.post("/api/live-tests", json={"backtest_id": completed["backtests"][0]["id"], "entitlement": "delayed", "delay_minutes": 0, "confirmation": "Start Live Data Test"})
    assert result.status_code == 422


def test_cancel_preserves_candidates():
    created = client.post("/api/research-sessions", json=session_config()).json()
    started = client.post(f"/api/research-sessions/{created['id']}/start").json()
    canceled = client.post(f"/api/research-sessions/{created['id']}/control", json={"action": "cancel"}).json()
    assert canceled["state"] == "CANCELED"
    assert len(canceled["candidates"]) == len(started["candidates"]) == 1


def test_instrument_and_timeframe_are_configurable_and_frozen():
    created = client.post("/api/research-sessions", json=session_config(instruments=["aapl"], timeframe="15m")).json()
    assert created["config"]["instruments"] == ["AAPL"]
    assert created["config"]["timeframe"] == "15m"
    client.post(f"/api/research-sessions/{created['id']}/start")
    completed = client.post(f"/api/research-sessions/{created['id']}/control", json={"action": "stop"}).json()
    result = completed["backtests"][0]
    assert result["assumptions"]["instruments"] == ["AAPL"]
    assert result["assumptions"]["timeframe"] == "15m"
    assert result["dataset_id"].endswith("-15m")


@pytest.mark.parametrize("instruments", [[""], ["../SPY"], ["SPY;DROP"], ["TOO-LONG-SYMBOL"]])
def test_invalid_instrument_rejected(instruments):
    assert client.post("/api/research-sessions", json=session_config(instruments=instruments)).status_code == 422

def test_audit_chain_links_events():
    client.post("/api/research-sessions", json=session_config())
    client.post("/api/research-sessions", json=session_config(name="Second research session"))
    events = list(reversed(client.get("/api/activity").json()))
    assert events[1]["previous_hash"] == events[0]["event_hash"]
