"""Deterministic MVP. No AI or remote research tool can reach execution functions."""
from __future__ import annotations

import hashlib, json, os, sqlite3, uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ROOT = Path(__file__).parent
DB = Path(os.getenv("TRADING_DB_PATH", ROOT / "trading.db"))
ENGINE_VERSION = "mvp-1"
ENGINE_HASH = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
PLATFORM_CAPS = {"max_order_notional": Decimal("1000"), "max_position_notional": Decimal("5000"), "max_daily_loss": Decimal("250"), "max_drawdown_percent": Decimal("20")}
DISCLAIMER = "Backtests, AI research, and paper trading do not predict future returns. Execution can differ materially. Loss of all allocated capital remains possible."


def now() -> datetime: return datetime.now(UTC)
def canonical(value: object) -> str: return json.dumps(value, sort_keys=True, separators=(",", ":"))
def digest(value: object) -> str: return hashlib.sha256(canonical(value).encode()).hexdigest()
def money(value: str) -> Decimal:
    try:
        d = Decimal(value)
        if not d.is_finite() or d < 0: raise ValueError
        return d
    except (InvalidOperation, ValueError): raise ValueError("must be a non-negative finite decimal string")
def db():
    DB.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    return con
def audit(kind: str, payload: dict):
    with db() as con: con.execute("INSERT INTO audit_events(at,kind,payload) VALUES(?,?,?)", (now().isoformat(), kind, canonical(payload)))

def demo_bars() -> list[dict]:
    # Explicit deterministic local-development data. Never broker data.
    closes = [Decimal("100")]
    for i in range(1, 420):
        drift = Decimal("0.0012") if (i // 60) % 2 == 0 else Decimal("-0.0007")
        wave = Decimal((i * 17 % 11) - 5) / Decimal("10000")
        closes.append((closes[-1] * (1 + drift + wave)).quantize(Decimal("0.01")))
    start = datetime(2023, 1, 3, tzinfo=UTC)
    return [{"timestamp": (start + timedelta(days=i)).isoformat(), "close": str(p)} for i, p in enumerate(closes) if (start + timedelta(days=i)).weekday() < 5]

class Cross(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["moving_average_cross"]
    fast_period: int = Field(ge=2, le=100)
    slow_period: int = Field(ge=3, le=300)
    direction: Literal["above", "below"]
    @model_validator(mode="after")
    def periods(self):
        if self.fast_period >= self.slow_period: raise ValueError("fast_period must be lower than slow_period")
        return self
class Position(BaseModel):
    model_config = ConfigDict(extra="forbid")
    type: Literal["fixed_fraction"]
    fraction: str
    @field_validator("fraction")
    @classmethod
    def allocation(cls, v):
        d = money(v)
        if not Decimal("0") < d <= Decimal("0.10"): raise ValueError("fraction must be > 0 and <= 0.10")
        return v
class Risk(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_order_notional: str
    max_position_notional: str
    max_daily_loss: str
    max_drawdown_percent: str
    stop_loss_percent: str
    @field_validator("max_order_notional", "max_position_notional", "max_daily_loss", "max_drawdown_percent", "stop_loss_percent")
    @classmethod
    def decimal(cls, v): return str(money(v))
    @model_validator(mode="after")
    def caps(self):
        for name, cap in PLATFORM_CAPS.items():
            if money(getattr(self, name)) > cap: raise ValueError(f"{name} exceeds platform cap")
        if not Decimal("0") < money(self.stop_loss_percent) <= Decimal("10"): raise ValueError("stop_loss_percent must be > 0 and <= 10")
        return self
class Schedule(BaseModel):
    model_config = ConfigDict(extra="forbid")
    regular_hours_only: Literal[True]
    timezone: Literal["America/New_York"]
class Strategy(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=3, max_length=120)
    version: int = Field(ge=1)
    hypothesis: str = Field(min_length=10, max_length=1000)
    market: Literal["US_EQUITIES"]
    instrument: Literal["SPY"]
    timeframe: Literal["1d"]
    entry: Cross
    exit: Cross
    position: Position
    risk: Risk
    schedule: Schedule
    @model_validator(mode="after")
    def opposite_crosses(self):
        if self.entry.direction == self.exit.direction: raise ValueError("entry and exit directions must differ")
        return self
class ApprovalRequest(BaseModel):
    strategy_id: str
    account_id: str = Field(min_length=1)
    mode: Literal["paper", "live"]
    expires_at: datetime
    typed_approval: str
class OrderIntent(BaseModel):
    strategy_id: str
    symbol: Literal["SPY"]
    side: Literal["buy", "sell"]
    quantity: str
    price: str
    mode: Literal["paper", "live"]
    @field_validator("quantity", "price")
    @classmethod
    def positive(cls, v):
        if money(v) <= 0: raise ValueError("must be positive")
        return v

class BrokerAdapter:
    """Minimal Alpaca boundary. Network broker calls intentionally unavailable in demo mode."""
    required_operations = ("get_account", "get_clock", "get_calendar", "get_asset", "get_positions", "get_position", "get_open_orders", "get_order", "get_order_by_client_order_id", "submit_order", "cancel_order", "cancel_open_orders", "stream_order_updates", "stream_market_data", "get_recent_bars")
    def __init__(self, mode: str):
        self.mode = mode
        self.base_url = "https://paper-api.alpaca.markets" if mode == "paper" else "https://api.alpaca.markets"
    def unavailable(self): raise HTTPException(503, "Broker unavailable: configure the reviewed Alpaca adapter; simulated fills are prohibited.")
    get_account = get_clock = get_calendar = get_asset = get_positions = get_position = get_open_orders = get_order = get_order_by_client_order_id = submit_order = cancel_order = cancel_open_orders = stream_order_updates = stream_market_data = get_recent_bars = unavailable

def kill_switch(maximum_drawdown_percent: str, risk: Risk) -> dict:
    observed, limit = Decimal(maximum_drawdown_percent), money(risk.max_drawdown_percent)
    return {"state":"HALTED" if observed > limit else "ARMED", "reason":"drawdown limit exceeded" if observed > limit else "drawdown within approved limit", "limit_percent":f"{limit:.2f}", "observed_percent":f"{observed:.2f}"}

def metrics(strategy: Strategy, bars: list[dict]) -> dict:
    closes = [Decimal(b["close"]) for b in bars]
    fast, slow = strategy.entry.fast_period, strategy.entry.slow_period
    equity, cash, shares, peak, trades, costs = Decimal("10000"), Decimal("10000"), Decimal("0"), Decimal("10000"), 0, Decimal("0")
    curve, position = [], False
    for i, price in enumerate(closes):
        value = cash + shares * price
        peak = max(peak, value); curve.append(value)
        if i < slow: continue
        prev_fast = sum(closes[i-fast:i]) / fast; prev_slow = sum(closes[i-slow:i]) / slow
        curr_fast = sum(closes[i-fast+1:i+1]) / fast; curr_slow = sum(closes[i-slow+1:i+1]) / slow
        buy, sell = prev_fast <= prev_slow and curr_fast > curr_slow, prev_fast >= prev_slow and curr_fast < curr_slow
        # Fill next-bar-equivalent close plus conservative 5 bps slippage; no same-bar fill.
        if buy and not position:
            notional = min(cash * money(strategy.position.fraction), money(strategy.risk.max_order_notional))
            qty = (notional / (price * Decimal("1.0005"))).quantize(Decimal("0.001"), rounding=ROUND_DOWN)
            if qty > 0:
                fee = (qty * price * Decimal("0.0005")).quantize(Decimal("0.01")); cash -= qty * price + fee; shares += qty; costs += fee; position = True; trades += 1
        elif sell and position:
            fee = (shares * price * Decimal("0.0005")).quantize(Decimal("0.01")); cash += shares * price - fee; costs += fee; shares = Decimal("0"); position = False; trades += 1
    ending = cash + shares * closes[-1]
    max_dd = max((peak - v) / peak * 100 for v in curve) if curve else Decimal("0")
    result = {"starting_equity":"10000.00", "ending_equity":f"{ending:.2f}", "net_return_percent":f"{(ending / Decimal('10000') - 1) * 100:.2f}", "benchmark_return_percent":f"{(closes[-1] / closes[0] - 1) * 100:.2f}", "maximum_drawdown_percent":f"{max_dd:.2f}", "trade_count":trades, "costs":f"{costs:.2f}", "equity_curve":[f"{v:.2f}" for v in curve], "warnings":["Deterministic demo data only; not market data.", "Simplified close-price fill model; broker paper/live fills may differ.", "Trade count is insufficient for investment conclusions."]}
    result["kill_switch"] = kill_switch(result["maximum_drawdown_percent"], strategy.risk)
    return result

def init_db():
    with db() as con:
        con.executescript("""CREATE TABLE IF NOT EXISTS strategies(id TEXT PRIMARY KEY,body TEXT NOT NULL,hash TEXT NOT NULL,created_at TEXT NOT NULL); CREATE TABLE IF NOT EXISTS approvals(id TEXT PRIMARY KEY,strategy_id TEXT NOT NULL,mode TEXT NOT NULL,account_id TEXT NOT NULL,expires_at TEXT NOT NULL,strategy_hash TEXT NOT NULL,engine_hash TEXT NOT NULL,revoked INTEGER NOT NULL DEFAULT 0); CREATE TABLE IF NOT EXISTS checkpoints(id TEXT PRIMARY KEY,strategy_id TEXT NOT NULL,kind TEXT NOT NULL,data_hash TEXT NOT NULL,engine_hash TEXT NOT NULL,payload TEXT NOT NULL,created_at TEXT NOT NULL); CREATE TABLE IF NOT EXISTS audit_events(id INTEGER PRIMARY KEY AUTOINCREMENT,at TEXT NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL);""")

@asynccontextmanager
async def lifespan(_: FastAPI): init_db(); yield
init_db()
app = FastAPI(title="Guardrail Trading", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=["http://localhost:5173"], allow_methods=["*"], allow_headers=["*"])

@app.get("/api/status")
def status(): return {"mode":"demo", "live_enabled":False, "disclaimer":DISCLAIMER, "engine_version":ENGINE_VERSION, "engine_hash":ENGINE_HASH}
@app.get("/api/demo-bars")
def bars(): return {"label":"Deterministic demo dataset — local development only, not market data", "symbol":"SPY", "bars":demo_bars()}
@app.post("/api/strategies")
def create_strategy(strategy: Strategy):
    body = strategy.model_dump(mode="json"); sid = str(uuid.uuid4()); h = digest(body)
    with db() as con: con.execute("INSERT INTO strategies VALUES(?,?,?,?)", (sid, canonical(body), h, now().isoformat()))
    audit("strategy.created", {"strategy_id":sid,"strategy_hash":h}); return {"strategy_id":sid,"strategy_hash":h,"strategy":body}
@app.get("/api/strategies/{strategy_id}")
def get_strategy(strategy_id: str):
    with db() as con: row = con.execute("SELECT * FROM strategies WHERE id=?", (strategy_id,)).fetchone()
    if not row: raise HTTPException(404, "Strategy not found")
    return {"strategy_id":row["id"],"strategy":json.loads(row["body"]),"strategy_hash":row["hash"]}
@app.post("/api/strategies/{strategy_id}/backtest")
def backtest(strategy_id: str):
    item = get_strategy(strategy_id); result = metrics(Strategy.model_validate(item["strategy"]), demo_bars())
    checkpoint_id, result_hash = str(uuid.uuid4()), digest(result)
    payload = {"data":"deterministic-demo-v1", "result":result, "strategy_hash":item["strategy_hash"]}
    with db() as con: con.execute("INSERT INTO checkpoints VALUES(?,?,?,?,?,?,?)", (checkpoint_id, strategy_id, "backtest-baseline", result_hash, ENGINE_HASH, canonical(payload), now().isoformat()))
    audit("backtest.completed", {"strategy_id":strategy_id,"checkpoint_id":checkpoint_id,"data":"deterministic-demo-v1","result_hash":result_hash})
    return {**payload, "checkpoint_id":checkpoint_id, "checkpoint_hash":result_hash, "disclaimer":DISCLAIMER}
@app.get("/api/strategies/{strategy_id}/monitoring")
def monitoring(strategy_id: str):
    item = get_strategy(strategy_id)
    with db() as con:
        checkpoint = con.execute("SELECT * FROM checkpoints WHERE strategy_id=? ORDER BY created_at DESC LIMIT 1", (strategy_id,)).fetchone()
        approval = con.execute("SELECT * FROM approvals WHERE strategy_id=? AND mode='paper' AND revoked=0 ORDER BY expires_at DESC LIMIT 1", (strategy_id,)).fetchone()
    if not checkpoint: raise HTTPException(404, "No immutable backtest baseline")
    active = approval and datetime.fromisoformat(approval["expires_at"]) > now()
    return {"strategy_id":strategy_id, "baseline":{"checkpoint_id":checkpoint["id"],"hash":checkpoint["data_hash"],"created_at":checkpoint["created_at"],"data":"deterministic-demo-v1"}, "lifecycle":{"backtest":"complete","paper_forward":"awaiting broker-reconciled observations" if active else "approval required","broker_reconciliation":"unavailable","execution":"hard-blocked"}, "kill_switch":{"state":"ARMED","action":"revokes execution eligibility when reconciled metrics breach approved limits; no broker calls exist"}, "disclaimer":DISCLAIMER}

@app.post("/api/approvals")
def approve(req: ApprovalRequest):
    if req.mode == "live":
        if os.getenv("ENABLE_LIVE_TRADING") != "true": raise HTTPException(403, "Live trading disabled by server setting")
        raise HTTPException(503, "Live broker verification/reconciliation not implemented; live approval blocked")
    if req.expires_at.tzinfo is None or req.expires_at <= now() or req.expires_at > now() + timedelta(days=30): raise HTTPException(422, "Paper approval expiration must be future UTC and within 30 days")
    item = get_strategy(req.strategy_id); version = item["strategy"]["version"]
    expected = f"APPROVE_AUTONOMOUS {req.strategy_id} {version} {req.account_id} {item['strategy']['risk']['max_position_notional']} {req.expires_at.isoformat()}"
    if req.typed_approval != expected: raise HTTPException(422, "Typed approval must exactly match required approval text")
    approval_id = str(uuid.uuid4())
    with db() as con: con.execute("INSERT INTO approvals VALUES(?,?,?,?,?,?,?,0)", (approval_id, req.strategy_id, req.mode, req.account_id, req.expires_at.isoformat(), item["strategy_hash"], ENGINE_HASH))
    audit("approval.granted", {"approval_id":approval_id,"strategy_id":req.strategy_id,"mode":req.mode}); return {"approval_id":approval_id,"expires_at":req.expires_at,"required_text":expected}
@app.post("/api/orders")
def order(intent: OrderIntent):
    if intent.mode == "live": raise HTTPException(403, "Live order blocked: adapter verification is incomplete")
    with db() as con: approval = con.execute("SELECT * FROM approvals WHERE strategy_id=? AND mode='paper' AND revoked=0 ORDER BY expires_at DESC LIMIT 1", (intent.strategy_id,)).fetchone()
    if not approval or datetime.fromisoformat(approval["expires_at"]) <= now(): raise HTTPException(403, "No active paper approval")
    item = get_strategy(intent.strategy_id)
    if item["strategy_hash"] != approval["strategy_hash"] or approval["engine_hash"] != ENGINE_HASH: raise HTTPException(403, "Immutable approval hash mismatch")
    notional = money(intent.quantity) * money(intent.price); max_order = money(item["strategy"]["risk"]["max_order_notional"])
    if notional > max_order: raise HTTPException(422, "Independent risk gate: order exceeds approved maximum")
    client_order_id = f"gt-{uuid.uuid4()}"; audit("order.intent.persisted", {"strategy_id":intent.strategy_id,"client_order_id":client_order_id,"notional":str(notional)})
    BrokerAdapter("paper").submit_order() # Never simulate a fill.
@app.get("/api/audit")
def audit_events():
    with db() as con: rows = con.execute("SELECT * FROM audit_events ORDER BY id DESC LIMIT 100").fetchall()
    return [dict(r) for r in rows]

STATIC = ROOT / "static"
if STATIC.is_dir(): app.mount("/", StaticFiles(directory=STATIC, html=True), name="frontend")
