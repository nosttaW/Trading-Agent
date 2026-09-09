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
def clean_database(monkeypatch):
    if TEST_DB.exists():
        TEST_DB.unlink()
    service.init_db()
    service.LOGIN_FAILURES.clear()
    monkeypatch.setattr(service, "alpaca_data_connection", lambda: True)
    monkeypatch.setattr(service, "validate_alpaca_equity_symbol", lambda instrument: None)
    monkeypatch.setattr(service, "fetch_alpaca_bars", lambda *args: (service.demo_bars(args[1]), "iex"))
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


def create_completed_session(monkeypatch=None):
    if monkeypatch:
        monkeypatch.setattr(service, "alpaca_data_connection", lambda: True)
        monkeypatch.setattr(service, "validate_alpaca_equity_symbol", lambda instrument: None)
        monkeypatch.setattr(service, "fetch_alpaca_bars", lambda *args: (service.demo_bars(args[1]), "iex"))
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
    assert result["mode"] == "CONNECTED"
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


def test_provider_model_discovery_returns_sorted_ids_without_saving(monkeypatch):
    class Response:
        status_code = 200
        content = b'{}'
        def raise_for_status(self): pass
        def json(self): return {"data": [{"id": "model-z"}, {"id": "model-a"}, {"bad": True}]}
    class Client:
        def __init__(self, **_): pass
        def __enter__(self): return self
        def __exit__(self, *_): pass
        def get(self, *_, **__): return Response()
    monkeypatch.setattr(service.httpx, "Client", Client)
    monkeypatch.setattr(service, "validate_endpoint", lambda value: value.rstrip("/"))
    payload = {"name": "Compatible API", "base_url": "https://api.example.com/v1", "api_key": "secret-key", "model_id": "manual", "profile": "chat_completions"}
    result = client.post("/api/providers/discover-models", json=payload)
    assert result.status_code == 200 and result.json()["models"] == ["model-a", "model-z"]
    assert client.get("/api/providers").json() == []


def test_unsafe_custom_headers_rejected():
    result = client.post("/api/providers", json={"name": "Unsafe", "base_url": "https://api.openai.com/v1", "model_id": "model", "custom_headers": {"Authorization": "secret"}})
    assert result.status_code == 422


def test_alpaca_credentials_are_mode_separated_encrypted_and_masked():
    data = client.post("/api/alpaca-connections", json={"mode": "data", "label": "Data", "key_id": "DATAKEY1234", "secret_key": "DATASECRET1234", "feed": "iex"})
    paper = client.post("/api/alpaca-connections", json={"mode": "paper", "label": "Paper", "key_id": "PAPERKEY1234", "secret_key": "PAPERSECRET1234", "feed": None})
    assert data.status_code == paper.status_code == 200
    assert "DATAKEY1234" not in data.text and "DATASECRET1234" not in data.text
    assert data.json()["base_url"] == "https://data.alpaca.markets"
    assert paper.json()["base_url"] == "https://paper-api.alpaca.markets"
    with service.connect() as connection:
        rows = connection.execute("SELECT * FROM alpaca_connections ORDER BY mode").fetchall()
    assert len(rows) == 2
    assert all("SECRET" not in row["encrypted_secret_key"] for row in rows)
    assert service.decrypt_secret(rows[0]["encrypted_key_id"]) == "DATAKEY1234"


def test_live_alpaca_credentials_disabled():
    result = client.post("/api/alpaca-connections", json={"mode": "live", "label": "Live", "key_id": "LIVEKEY1234", "secret_key": "LIVESECRET1234"})
    assert result.status_code == 403


def test_alpaca_feed_validation():
    assert client.post("/api/alpaca-connections", json={"mode": "data", "label": "Data", "key_id": "DATAKEY1234", "secret_key": "DATASECRET1234"}).status_code == 422
    assert client.post("/api/alpaca-connections", json={"mode": "paper", "label": "Paper", "key_id": "PAPERKEY1234", "secret_key": "PAPERSECRET1234", "feed": "sip"}).status_code == 422


class FakePaperBroker:
    def __init__(self): self.submissions = []; self.canceled = False
    def account(self): return {"id": "paper-account-1", "status": "ACTIVE", "equity": "10000", "cash": "9000", "buying_power": "9000"}
    def positions(self): return []
    def orders(self): return [{"id": "broker-1", "client_order_id": order["client_order_id"], "status": "accepted", "filled_qty": "0", "filled_avg_price": None} for order in self.submissions]
    def submit(self, symbol, side, quantity, client_order_id):
        order = {"id": "broker-1", "client_order_id": client_order_id, "symbol": symbol, "side": side, "qty": quantity, "status": "accepted", "filled_qty": "0", "filled_avg_price": None}
        self.submissions.append(order); return order
    def cancel_all(self): self.canceled = True


def paper_ready(monkeypatch):
    completed = create_completed_session(monkeypatch); backtest = completed["backtests"][0]; fake = FakePaperBroker()
    monkeypatch.setattr(service, "paper_broker", lambda: fake)
    detail = client.get(f"/api/backtests/{backtest['id']}").json()
    expected = f"APPROVE BROKER PAPER SPY {detail['source_hash'][:12]}"
    approved = client.post("/api/paper-sessions", json={"backtest_id": backtest["id"], "typed_approval": expected}).json()
    return approved, fake


def test_inactive_flat_paper_session_can_be_archived(monkeypatch):
    approved, _ = paper_ready(monkeypatch)
    result = client.delete(f"/api/paper-sessions/{approved['id']}")
    assert result.status_code == 200 and result.json()["history_preserved"] is True
    assert all(item["id"] != approved["id"] for item in client.get("/api/paper-sessions").json())
    with service.connect() as connection: assert connection.execute("SELECT archived_at FROM paper_sessions WHERE id=?", (approved["id"],)).fetchone()[0]


def test_active_paper_session_cannot_be_archived(monkeypatch):
    approved, _ = paper_ready(monkeypatch)
    client.post(f"/api/paper-sessions/{approved['id']}/control", json={"action": "resume", "confirmation": "RESUME BROKER PAPER"})
    assert client.delete(f"/api/paper-sessions/{approved['id']}").status_code == 409


def test_paper_approval_starts_halted_and_binds_hash(monkeypatch):
    approved, _ = paper_ready(monkeypatch)
    assert approved["mode"] == "BROKER_PAPER"
    assert approved["state"] == "HALTED" and approved["emergency_stop"] == 1
    assert approved["strategy_hash"] and approved["engine_hash"] == service.PAPER_ENGINE_HASH
    assert approved["broker_account_id"] == "paper-account-1"
    assert approved["limits"]["capital_allocation"] == "9000.00"
    assert approved["limits"]["max_order_notional"] == "900.00"
    assert approved["limits"]["max_position_notional"] == "2250.00"
    assert approved["limits"]["max_order_percent"] == "10.00"
    assert approved["limits"]["max_position_percent"] == "25.00"


def test_paper_resume_reconciles_and_order_is_idempotent(monkeypatch):
    approved, fake = paper_ready(monkeypatch)
    resumed = client.post(f"/api/paper-sessions/{approved['id']}/control", json={"action": "resume", "confirmation": "RESUME BROKER PAPER"})
    assert resumed.status_code == 200 and resumed.json()["state"] == "ACTIVE"
    payload = {"side": "buy", "quantity": "1", "reference_price": "100", "bar_at": "2026-01-02T15:00:00Z", "confirmation": "Submit Broker Paper Order"}
    first = client.post(f"/api/paper-sessions/{approved['id']}/orders", json=payload)
    second = client.post(f"/api/paper-sessions/{approved['id']}/orders", json=payload)
    assert first.status_code == second.status_code == 200
    assert first.json()["duplicate"] is False and second.json()["duplicate"] is True
    assert len(fake.submissions) == 1


def enable_automation(approved):
    expected = f"ENABLE AUTONOMOUS PAPER {approved['instrument']} {approved['strategy_hash'][:12]}"
    return client.post(f"/api/paper-sessions/{approved['id']}/automation", json={"enabled": True, "confirmation": expected})


def test_stale_engine_approval_is_exposed_and_can_be_retired(monkeypatch):
    approved, _ = paper_ready(monkeypatch)
    with service.connect() as connection: connection.execute("UPDATE paper_sessions SET engine_hash='old-engine' WHERE id=?", (approved["id"],))
    assert client.get(f"/api/paper-sessions/{approved['id']}").json()["approval_current"] is False
    stopped = client.post(f"/api/paper-sessions/{approved['id']}/control", json={"action": "stop", "confirmation": "STOP BROKER PAPER"})
    assert stopped.status_code == 200 and stopped.json()["state"] == "STOPPED"


def test_stale_engine_approval_cannot_enable_automation(monkeypatch):
    approved, fake = paper_ready(monkeypatch)
    client.post(f"/api/paper-sessions/{approved['id']}/control", json={"action": "resume", "confirmation": "RESUME BROKER PAPER"})
    with service.connect() as connection: connection.execute("UPDATE paper_sessions SET engine_hash='old-engine' WHERE id=?", (approved["id"],))
    result = enable_automation(approved)
    assert result.status_code == 409 and fake.submissions == []


def test_paper_automation_requires_separate_opt_in(monkeypatch):
    approved, fake = paper_ready(monkeypatch)
    assert approved["automation_enabled"] == 0 and approved["automation_state"] == "DISABLED"
    assert client.post(f"/api/paper-sessions/{approved['id']}/automation", json={"enabled": True, "confirmation": "wrong"}).status_code == 422
    client.post(f"/api/paper-sessions/{approved['id']}/control", json={"action": "resume", "confirmation": "RESUME BROKER PAPER"})
    enabled = enable_automation(approved)
    assert enabled.status_code == 200 and enabled.json()["automation_enabled"] == 1
    assert fake.submissions == []


def test_paper_automation_closed_bar_entry_is_sized_and_idempotent(monkeypatch):
    approved, fake = paper_ready(monkeypatch)
    client.post(f"/api/paper-sessions/{approved['id']}/control", json={"action": "resume", "confirmation": "RESUME BROKER PAPER"})
    assert enable_automation(approved).status_code == 200
    bars = service.demo_bars("15m")[-100:]
    for index, bar in enumerate(bars): bar["close"] = bar["open"] = bar["high"] = bar["low"] = str(100 + index)
    monkeypatch.setattr(service, "latest_alpaca_bars", lambda *args: bars)
    monkeypatch.setattr(service, "closed_bars", lambda incoming, _: incoming)
    service.process_paper_automation(); service.process_paper_automation()
    assert len(fake.submissions) == 1
    assert fake.submissions[0]["side"] == "buy" and fake.submissions[0]["qty"] == "4.522"
    assert float(fake.submissions[0]["qty"]) * 199 <= 900
    result = client.get(f"/api/paper-sessions/{approved['id']}").json()
    assert result["automation_state"] == "RUNNING" and result["automation_runtime"]["signal"] == "LONG"


def test_paper_automation_pauses_then_retries_data_failure(monkeypatch):
    approved, fake = paper_ready(monkeypatch)
    client.post(f"/api/paper-sessions/{approved['id']}/control", json={"action": "resume", "confirmation": "RESUME BROKER PAPER"})
    enable_automation(approved)
    monkeypatch.setattr(service, "latest_alpaca_bars", lambda *args: (_ for _ in ()).throw(service.HTTPException(504, "data unavailable")))
    service.process_paper_automation()
    result = client.get(f"/api/paper-sessions/{approved['id']}").json()
    assert result["state"] == "PAUSED" and result["automation_state"] == "RETRYING"
    assert result["automation_runtime"]["auto_paused"] is True and fake.submissions == []


def test_paper_automation_disable_and_emergency_stop_block_worker(monkeypatch):
    approved, fake = paper_ready(monkeypatch)
    client.post(f"/api/paper-sessions/{approved['id']}/control", json={"action": "resume", "confirmation": "RESUME BROKER PAPER"})
    enable_automation(approved)
    disabled = client.post(f"/api/paper-sessions/{approved['id']}/automation", json={"enabled": False, "confirmation": "DISABLE AUTONOMOUS PAPER"})
    assert disabled.json()["automation_enabled"] == 0
    service.process_paper_automation(); assert fake.submissions == []
    enable_automation(approved)
    stopped = client.post(f"/api/paper-sessions/{approved['id']}/control", json={"action": "emergency_stop", "confirmation": "EMERGENCY STOP PAPER"})
    service.process_paper_automation()
    assert stopped.json()["state"] == "HALTED" and fake.submissions == []


def test_paper_sell_cannot_exceed_broker_position(monkeypatch):
    approved, fake = paper_ready(monkeypatch)
    fake.positions = lambda: [{"symbol": "SPY", "qty": "1", "market_value": "100", "avg_entry_price": "100", "unrealized_pl": "0"}]
    client.post(f"/api/paper-sessions/{approved['id']}/control", json={"action": "resume", "confirmation": "RESUME BROKER PAPER"})
    payload = {"side": "sell", "quantity": "2", "reference_price": "100", "bar_at": "2026-01-02T15:00:00Z", "confirmation": "Submit Broker Paper Order"}
    result = client.post(f"/api/paper-sessions/{approved['id']}/orders", json=payload)
    assert result.status_code == 422 and fake.submissions == []


def test_paper_buy_checks_resulting_position_exposure(monkeypatch):
    approved, fake = paper_ready(monkeypatch)
    fake.positions = lambda: [{"symbol": "SPY", "qty": "22", "market_value": "2200", "avg_entry_price": "100", "unrealized_pl": "0"}]
    client.post(f"/api/paper-sessions/{approved['id']}/control", json={"action": "resume", "confirmation": "RESUME BROKER PAPER"})
    payload = {"side": "buy", "quantity": "1", "reference_price": "100", "bar_at": "2026-01-02T15:00:00Z", "confirmation": "Submit Broker Paper Order"}
    result = client.post(f"/api/paper-sessions/{approved['id']}/orders", json=payload)
    assert result.status_code == 422 and fake.submissions == []


def test_emergency_stop_disables_automation(monkeypatch):
    approved, fake = paper_ready(monkeypatch)
    client.post(f"/api/paper-sessions/{approved['id']}/control", json={"action": "resume", "confirmation": "RESUME BROKER PAPER"})
    enable_automation(approved)
    stopped = client.post(f"/api/paper-sessions/{approved['id']}/control", json={"action": "emergency_stop", "confirmation": "EMERGENCY STOP PAPER"}).json()
    assert stopped["automation_enabled"] == 0 and stopped["automation_state"] == "EMERGENCY_STOPPED"


def test_paper_risk_gate_and_emergency_stop(monkeypatch):
    approved, fake = paper_ready(monkeypatch)
    client.post(f"/api/paper-sessions/{approved['id']}/control", json={"action": "resume", "confirmation": "RESUME BROKER PAPER"})
    excessive = {"side": "buy", "quantity": "10", "reference_price": "100", "bar_at": "2026-01-02T15:00:00Z", "confirmation": "Submit Broker Paper Order"}
    assert client.post(f"/api/paper-sessions/{approved['id']}/orders", json=excessive).status_code == 422
    stopped = client.post(f"/api/paper-sessions/{approved['id']}/control", json={"action": "emergency_stop", "confirmation": "EMERGENCY STOP PAPER"})
    assert stopped.status_code == 200 and stopped.json()["state"] == "HALTED" and fake.canceled


def test_live_order_route_stays_blocked_with_paper_features(monkeypatch):
    paper_ready(monkeypatch)
    assert client.post("/api/orders", json={"mode": "live"}).status_code == 403


def test_invalid_equity_symbol_is_rejected_before_session_creation(monkeypatch):
    def reject(_): raise service.HTTPException(422, "No US-equity data found for BTC")
    monkeypatch.setattr(service, "validate_alpaca_equity_symbol", reject)
    result = client.post("/api/research-sessions", json=session_config(instruments=["BTC"]))
    assert result.status_code == 422
    assert "No US-equity data" in result.json()["detail"]


def test_watchlist_persists_validated_live_bars(monkeypatch):
    bars = service.demo_bars("1m")[-20:]
    monkeypatch.setattr(service, "validate_alpaca_equity_symbol", lambda _: None)
    monkeypatch.setattr(service, "latest_alpaca_bars", lambda *args: bars)
    result = client.post("/api/watchlist", json={"symbol": "aapl", "timeframe": "1m"})
    assert result.status_code == 200
    item = result.json()[0]
    assert item["symbol"] == "AAPL" and item["status"] == "CONNECTED"
    assert len(item["bars"]) == 20 and item["last_poll_at"]
    assert client.delete("/api/watchlist/AAPL").status_code == 200


def test_web_research_sources_are_stored_with_provenance(monkeypatch):
    source = {"url": "https://example.com/research", "title": "Example study", "published_at": "", "retrieved_at": service.iso(), "excerpt": "Search result title: Example study"}
    monkeypatch.setattr(service, "web_search", lambda query, maximum: [source])
    created = client.post("/api/research-sessions", json=session_config(web_research_enabled=True, web_research_query="robust trend research", web_research_max_sources=1))
    assert created.status_code == 200
    result = created.json()
    assert result["research_sources"][0]["url"] == source["url"]
    assert result["research_sources"][0]["content_hash"]


def test_web_research_failure_never_fabricates_sources(monkeypatch):
    def fail(*_): raise service.HTTPException(502, "search unavailable")
    monkeypatch.setattr(service, "web_search", fail)
    created = client.post("/api/research-sessions", json=session_config(web_research_enabled=True, web_research_query="robust trend research"))
    assert created.status_code == 200
    assert created.json()["research_sources"] == []
    assert created.json()["last_error"] == "search unavailable"


def test_session_immediate_generation_and_frozen_config():
    created = client.post("/api/research-sessions", json=session_config()).json()
    original_hash = created["config_hash"]
    started = client.post(f"/api/research-sessions/{created['id']}/start").json()
    assert started["state"] == "GENERATING"
    assert started["generation_count"] == 1
    assert len(started["candidates"]) == 1
    assert started["config_hash"] == original_hash
    assert datetime.fromisoformat(started["next_run_at"]) > datetime.now(UTC)


def test_continuous_generation_runs_one_candidate_per_cycle():
    created = client.post("/api/research-sessions", json=session_config(generation_interval_minutes=0, maximum_candidates=3)).json()
    started = client.post(f"/api/research-sessions/{created['id']}/start").json()
    assert started["generation_count"] == 1
    service.process_due_sessions()
    second = client.get(f"/api/research-sessions/{created['id']}").json()
    assert second["generation_count"] == 2 and second["in_flight"] == 0
    service.process_due_sessions()
    third = client.get(f"/api/research-sessions/{created['id']}").json()
    assert third["generation_count"] == 3
    service.process_due_sessions()
    finished = client.get(f"/api/research-sessions/{created['id']}").json()
    assert finished["state"] == "COMPLETED"


def test_sub_fifteen_minute_intervals_are_valid():
    for interval in (1, 5, 10):
        result = client.post("/api/research-sessions", json=session_config(generation_interval_minutes=interval))
        assert result.status_code == 200


def test_pause_resume_and_no_missed_tick_burst():
    created = client.post("/api/research-sessions", json=session_config()).json()
    started = client.post(f"/api/research-sessions/{created['id']}/start").json()
    paused = client.post(f"/api/research-sessions/{created['id']}/control", json={"action": "pause"}).json()
    assert paused["state"] == "PAUSED" and paused["next_run_at"] is None
    resumed = client.post(f"/api/research-sessions/{created['id']}/control", json={"action": "resume"}).json()
    assert resumed["state"] == "GENERATING" and resumed["generation_count"] == 1
    assert datetime.fromisoformat(resumed["next_run_at"]) > datetime.now(UTC)


def test_zero_valid_candidates_fail_session(monkeypatch):
    monkeypatch.setattr(service, "provider_generate", lambda *_: (_ for _ in ()).throw(service.HTTPException(504, "provider timeout")))
    with service.connect() as connection:
        connection.execute("INSERT INTO providers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("provider", "Provider", "https://api.example.com/v1", "chat_completions", "model", service.encrypt_secret("secret"), service.encrypt_secret({}), 2, 1000, 0.2, 1, 0, None, None, service.iso(), service.iso()))
    created = client.post("/api/research-sessions", json=session_config(provider_id="provider", maximum_candidates=1)).json()
    client.post(f"/api/research-sessions/{created['id']}/start")
    result = client.post(f"/api/research-sessions/{created['id']}/control", json={"action": "stop"}).json()
    assert result["state"] == "FAILED" and result["backtests"] == []


def test_intraday_backtest_uses_regular_hours_and_flattens_daily():
    candidate = {"family": "moving_average", "parameters": '{"fast":10,"slow":40}'}
    bars = service.demo_bars("15m")[:200]
    result = service.backtest_candidate(candidate, session_config(timeframe="15m") | {"minimum_trade_count": 0, "starting_capital": "10000", "allocation_fraction": "0.25", "fee_bps": "1", "spread_bps": "2", "slippage_bps": "3"}, bars)
    eastern = service.ZoneInfo("America/New_York")
    assert all(9 <= datetime.fromisoformat(trade["entry_time"]).astimezone(eastern).hour < 16 for trade in result["trades"])
    assert all(not trade["exit_time"] or datetime.fromisoformat(trade["entry_time"]).astimezone(eastern).date() == datetime.fromisoformat(trade["exit_time"]).astimezone(eastern).date() for trade in result["trades"])


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


def test_strategy_delete_archives_but_preserves_history():
    completed = create_completed_session()
    backtest = completed["backtests"][0]
    archived = client.delete(f"/api/strategies/{backtest['candidate_id']}")
    assert archived.status_code == 200
    assert archived.json()["history_preserved"] is True
    assert client.get("/api/backtests").json() == []
    assert client.get(f"/api/backtests/{backtest['id']}").status_code == 200
    with service.connect() as connection:
        assert connection.execute("SELECT archived_at FROM candidates WHERE id=?", (backtest["candidate_id"],)).fetchone()[0]


def test_strategy_delete_blocked_by_active_forward_test():
    completed = create_completed_session(); backtest = completed["backtests"][0]
    created = client.post("/api/live-tests", json={"backtest_id": backtest["id"], "entitlement": "delayed", "delay_minutes": 15, "confirmation": "Start Live Data Test"})
    assert created.status_code == 200
    result = client.delete(f"/api/strategies/{backtest['candidate_id']}")
    assert result.status_code == 409


def test_backtest_fill_is_after_signal_bar():
    completed = create_completed_session()
    backtest = completed["backtests"][0]
    bars = service.demo_bars()
    timestamps = [bar["timestamp"] for bar in bars]
    for trade in backtest["trades"]:
        assert trade["entry_time"] in timestamps
        assert timestamps.index(trade["entry_time"]) >= 1


def test_live_worker_warms_without_orders_then_processes_new_bar(monkeypatch):
    completed = create_completed_session(); backtest = completed["backtests"][0]
    created = client.post("/api/live-tests", json={"backtest_id": backtest["id"], "entitlement": "delayed", "delay_minutes": 15, "confirmation": "Start Live Data Test"}).json()
    bars = service.demo_bars("1d")
    monkeypatch.setattr(service, "latest_alpaca_bars", lambda *args: bars)
    assert service.process_live_tests() == 1
    running = client.get(f"/api/live-tests/{created['id']}").json()
    assert running["state"] == "RUNNING"
    assert running["runtime_state"]["warmup_complete"] is True
    assert running["fills"] == []
    assert running["last_event_at"] == bars[-1]["timestamp"]
    assert len(running["runtime_state"]["equity_curve"]) == 2
    assert running["runtime_state"]["equity_curve"][-1]["at"] == bars[-1]["timestamp"]
    assert running["runtime_state"]["price_bars"][-1]["timestamp"] == bars[-1]["timestamp"]
    assert running["runtime_state"]["bars_available"] == len(bars)
    assert running["runtime_state"]["last_poll_at"]
    # Same event is deduplicated across repeated server cycles.
    service.process_live_tests()
    repeated = client.get(f"/api/live-tests/{created['id']}").json()
    assert repeated["runtime_state"]["events_processed"] == 1


def test_live_worker_connection_failure_is_visible(monkeypatch):
    completed = create_completed_session(); backtest = completed["backtests"][0]
    created = client.post("/api/live-tests", json={"backtest_id": backtest["id"], "entitlement": "delayed", "delay_minutes": 15, "confirmation": "Start Live Data Test"}).json()
    def fail(*_): raise service.HTTPException(504, "feed unavailable")
    monkeypatch.setattr(service, "latest_alpaca_bars", fail)
    service.process_live_tests()
    result = client.get(f"/api/live-tests/{created['id']}").json()
    assert result["state"] == "CONNECTION_ERROR"
    assert result["logs"][-1]["message"] == "feed unavailable"


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
    assert result["state"] == "WARMING_UP"
    assert client.post("/api/orders", json={"mode": "live"}).status_code == 403


def test_ai_strategy_review_is_stored_without_mutating_backtest(monkeypatch):
    completed = create_completed_session(monkeypatch); backtest = completed["backtests"][0]
    with service.connect() as connection:
        connection.execute("INSERT INTO providers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("reviewer", "Reviewer", "https://api.example.com/v1", "chat_completions", "model", service.encrypt_secret("secret"), service.encrypt_secret({}), 2, 1000, 0.2, 1, 0, None, None, service.iso(), service.iso()))
    monkeypatch.setattr(service, "provider_json", lambda *_: ({"verdict": "PAPER_CANDIDATE", "summary": "Evidence remains weak and requires independent validation.", "strengths": ["Chronological fills"], "weaknesses": ["Benchmark underperformance"], "recommendations": ["Retest"], "follow_up": None}, 10))
    result = client.post(f"/api/backtests/{backtest['id']}/reviews", json={"provider_id": "reviewer", "create_follow_up": False})
    assert result.status_code == 200 and result.json()["review"]["verdict"] == "REJECT"
    assert len(client.get(f"/api/backtests/{backtest['id']}/reviews").json()) == 1
    assert client.get(f"/api/backtests/{backtest['id']}").json()["metrics"] == backtest["metrics"]


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
    assert "-AAPL-15m-" in result["dataset_id"]


@pytest.mark.parametrize("instruments", [[""], ["../SPY"], ["SPY;DROP"], ["TOO-LONG-SYMBOL"]])
def test_invalid_instrument_rejected(instruments):
    assert client.post("/api/research-sessions", json=session_config(instruments=instruments)).status_code == 422

def test_audit_chain_links_events():
    client.post("/api/research-sessions", json=session_config())
    client.post("/api/research-sessions", json=session_config(name="Second research session"))
    events = list(reversed(client.get("/api/activity").json()))
    assert events[1]["previous_hash"] == events[0]["event_hash"]
