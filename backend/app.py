"""Strategy Lab: persistent research and deterministic simulation.

Generated Python is display-only. The trusted host executes reviewed strategy templates,
never arbitrary source. Broker submission is intentionally absent and fail-closed.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import math
import os
import secrets
import socket
import sqlite3
import statistics
import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_DOWN
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlparse

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

ROOT = Path(__file__).parent
DB = Path(os.getenv("TRADING_DB_PATH", ROOT / "trading.db"))
MIGRATION = ROOT / "migrations" / "001_initial.sql"
ENGINE_VERSION = "template-engine-2.0"
ENGINE_HASH = hashlib.sha256((Path(__file__).read_bytes() + MIGRATION.read_bytes())).hexdigest()
DATASET_ID = "DEMO-US-EQUITIES-DAILY-v2"
PROMPT_VERSION = "reviewed-template-v1"
DISCLAIMER = "Backtests and simulations do not predict future returns. Execution can differ materially. Losses can exceed risk thresholds during gaps, slippage, or outages."
SESSION_STATES = {"DRAFT", "GENERATING", "PAUSED", "STOPPING", "BACKTESTING", "COMPLETED", "COMPLETED_WITH_ERRORS", "CANCELED", "FAILED"}
ALLOWED_REMOTE_PORTS = {443}
SESSION_COOKIE = "strategy_lab_session"
SESSION_TTL_SECONDS = 12 * 60 * 60
LOGIN_WINDOW_SECONDS = 15 * 60
LOGIN_MAX_FAILURES = 5
LOGIN_FAILURES: dict[str, list[float]] = {}


def utcnow() -> datetime:
    return datetime.now(UTC)


def iso(value: datetime | None = None) -> str:
    return (value or utcnow()).isoformat()


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value: Any) -> str:
    encoded = value if isinstance(value, bytes) else canonical(value).encode()
    return hashlib.sha256(encoded).hexdigest()


def decimal_value(value: Any, *, positive: bool = False) -> Decimal:
    try:
        result = Decimal(str(value))
        if not result.is_finite() or result < 0 or (positive and result <= 0):
            raise ValueError
        return result
    except (InvalidOperation, ValueError):
        raise ValueError("must be a finite decimal" + (" greater than zero" if positive else ""))


def connect() -> sqlite3.Connection:
    DB.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DB, timeout=30)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys=ON")
    return connection


def init_db() -> None:
    with connect() as connection:
        # Preserve incompatible MVP tables. New schema never guesses at credential/approval migration.
        for table, required in (("audit_events", "event_hash"), ("approvals", "candidate_id")):
            columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
            if columns and required not in columns:
                connection.execute(f"ALTER TABLE {table} RENAME TO legacy_{table}")
        connection.executescript(MIGRATION.read_text())


def audit(kind: str, resource_type: str, resource_id: str, payload: dict[str, Any], actor: str = "local-user") -> None:
    with connect() as connection:
        previous = connection.execute("SELECT event_hash FROM audit_events ORDER BY id DESC LIMIT 1").fetchone()
        previous_hash = previous[0] if previous else None
        at = iso()
        event_hash = digest({"at": at, "actor": actor, "kind": kind, "resource_type": resource_type, "resource_id": resource_id, "payload": payload, "previous_hash": previous_hash})
        connection.execute(
            "INSERT INTO audit_events(at,actor,kind,resource_type,resource_id,payload,previous_hash,event_hash) VALUES(?,?,?,?,?,?,?,?)",
            (at, actor, kind, resource_type, resource_id, canonical(payload), previous_hash, event_hash),
        )


def json_row(row: sqlite3.Row, fields: tuple[str, ...]) -> dict[str, Any]:
    item = dict(row)
    for field in fields:
        if field in item and item[field] is not None:
            item[field] = json.loads(item[field])
    return item


TIMEFRAME_MINUTES = {"1m": 1, "5m": 5, "15m": 15, "1h": 60, "1d": 390}


def demo_bars(timeframe: str = "1d") -> list[dict[str, Any]]:
    """Deterministic OHLCV fixture. Explicitly not observed or symbol-specific data."""
    if timeframe not in TIMEFRAME_MINUTES:
        raise ValueError("unsupported timeframe")
    result: list[dict[str, Any]] = []
    close = Decimal("100")
    current = datetime(2022, 1, 3, 14, 30, tzinfo=UTC)
    step = timedelta(minutes=TIMEFRAME_MINUTES[timeframe])
    while len(result) < 620:
        session_open = current.replace(hour=14, minute=30, second=0, microsecond=0)
        session_close = current.replace(hour=21, minute=0, second=0, microsecond=0)
        eligible = current.weekday() < 5 and (timeframe == "1d" or session_open <= current < session_close)
        if eligible:
            i = len(result)
            regime = Decimal("0.0011") if (i // 75) % 3 != 1 else Decimal("-0.0008")
            wave = Decimal(((i * 29) % 17) - 8) / Decimal("10000")
            open_price = close
            close = (close * (Decimal("1") + regime + wave)).quantize(Decimal("0.01"))
            high = max(open_price, close) * Decimal("1.0025")
            low = min(open_price, close) * Decimal("0.9975")
            result.append({
                "timestamp": (session_close if timeframe == "1d" else current).isoformat(), "open": f"{open_price:.2f}", "high": f"{high:.2f}",
                "low": f"{low:.2f}", "close": f"{close:.2f}", "volume": 1_000_000 + (i * 7919) % 500_000,
            })
        current += step
        if timeframe != "1d" and current >= session_close:
            current = (current + timedelta(days=1)).replace(hour=14, minute=30, second=0, microsecond=0)
        elif timeframe == "1d":
            current = (current + timedelta(days=1)).replace(hour=14, minute=30, second=0, microsecond=0)
    return result


class ProviderInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=2, max_length=80)
    base_url: str = Field(min_length=8, max_length=500)
    api_key: str | None = Field(default=None, max_length=4096)
    model_id: str = Field(min_length=1, max_length=200)
    profile: Literal["chat_completions", "responses"] = "chat_completions"
    custom_headers: dict[str, str] = Field(default_factory=dict)
    timeout_seconds: int = Field(default=30, ge=2, le=120)
    max_output_tokens: int = Field(default=2000, ge=128, le=32000)
    temperature: float | None = Field(default=0.2, ge=0, le=2)
    concurrency_limit: int = Field(default=1, ge=1, le=8)
    max_retries: int = Field(default=2, ge=0, le=5)
    input_price_per_million: str | None = None
    output_price_per_million: str | None = None

    @field_validator("custom_headers")
    @classmethod
    def safe_headers(cls, value: dict[str, str]) -> dict[str, str]:
        forbidden = {"authorization", "cookie", "host", "proxy-authorization", "x-api-key", "connection"}
        if len(value) > 10 or any(k.lower() in forbidden or "\n" in k + v or "\r" in k + v for k, v in value.items()):
            raise ValueError("unsafe custom header")
        return value

    @field_validator("input_price_per_million", "output_price_per_million")
    @classmethod
    def pricing(cls, value: str | None) -> str | None:
        if value is not None:
            decimal_value(value)
        return value


class AlpacaConnectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["data", "paper", "live"]
    label: str = Field(default="Alpaca", min_length=2, max_length=80)
    key_id: str = Field(min_length=8, max_length=200)
    secret_key: str = Field(min_length=8, max_length=300)
    feed: Literal["iex", "sip", "delayed_sip"] | None = None

    @model_validator(mode="after")
    def valid_feed(self):
        if self.mode == "data" and self.feed is None:
            raise ValueError("market-data credentials require a feed")
        if self.mode != "data" and self.feed is not None:
            raise ValueError("feed applies only to market-data credentials")
        return self


class SessionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(default="Momentum research", min_length=3, max_length=120)
    provider_id: str | None = None
    instructions: str = Field(default="Explore robust long-only trend and mean-reversion hypotheses.", min_length=10, max_length=4000)
    instruments: list[str] = Field(default_factory=lambda: ["SPY"], min_length=1, max_length=1)
    market: Literal["US_EQUITIES"] = "US_EQUITIES"
    timeframe: Literal["1m", "5m", "15m", "1h", "1d"] = "1d"
    allowed_families: list[Literal["moving_average", "rsi", "channel_breakout"]] = Field(default_factory=lambda: ["moving_average", "rsi", "channel_breakout"], min_length=1)
    historical_start: str = "2022-01-03"
    historical_end: str = "2024-05-17"
    development_percent: int = Field(default=60, ge=40, le=80)
    validation_percent: int = Field(default=20, ge=10, le=30)
    holdout_percent: int = Field(default=20, ge=10, le=30)
    starting_capital: str = "10000.00"
    allocation_fraction: str = "0.25"
    fee_bps: str = "1.00"
    spread_bps: str = "2.00"
    slippage_bps: str = "3.00"
    max_drawdown_percent: str = "20.00"
    minimum_trade_count: int = Field(default=3, ge=0, le=1000)
    maximum_candidates: int = Field(default=9, ge=1, le=100)
    maximum_duration_minutes: int = Field(default=180, ge=15, le=10080)
    token_budget: int = Field(default=20000, ge=1000, le=10_000_000)
    maximum_repair_attempts: int = Field(default=2, ge=0, le=5)
    generation_interval_minutes: int = Field(default=15, ge=15, le=1440)
    generate_immediately: bool = True

    @model_validator(mode="after")
    def assumptions(self):
        if self.development_percent + self.validation_percent + self.holdout_percent != 100:
            raise ValueError("development, validation, and holdout must total 100")
        symbols = [symbol.strip().upper() for symbol in self.instruments]
        if any(not symbol or len(symbol) > 10 or not symbol[0].isalpha() or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-" for char in symbol) for symbol in symbols):
            raise ValueError("instrument must be a valid US-equity symbol")
        self.instruments = symbols
        for value in (self.starting_capital, self.allocation_fraction, self.fee_bps, self.spread_bps, self.slippage_bps, self.max_drawdown_percent):
            decimal_value(value)
        if not Decimal("0") < Decimal(self.allocation_fraction) <= Decimal("1"):
            raise ValueError("allocation_fraction must be greater than 0 and at most 1")
        return self


class LiveTestInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    backtest_id: str
    data_provider: Literal["alpaca"] = "alpaca"
    entitlement: Literal["real-time", "delayed"]
    delay_minutes: int = Field(default=0, ge=0, le=60)
    starting_virtual_cash: str = "10000.00"
    fee_bps: str = "1.00"
    spread_bps: str = "2.00"
    slippage_bps: str = "3.00"
    max_position_notional: str = "2500.00"
    max_daily_loss: str = "250.00"
    max_drawdown_percent: str = "10.00"
    duration_hours: int = Field(default=24, ge=1, le=720)
    overnight_policy: Literal["hold", "flatten_at_close"] = "flatten_at_close"
    confirmation: Literal["Start Live Data Test"]

    @model_validator(mode="after")
    def values(self):
        for value in (self.starting_virtual_cash, self.fee_bps, self.spread_bps, self.slippage_bps, self.max_position_notional, self.max_daily_loss, self.max_drawdown_percent):
            decimal_value(value)
        if self.entitlement == "real-time" and self.delay_minutes:
            raise ValueError("real-time entitlement cannot declare a delay")
        if self.entitlement == "delayed" and self.delay_minutes == 0:
            raise ValueError("delayed data requires a disclosed delay")
        return self


class LoginInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    password: str = Field(min_length=1, max_length=1024)


class ControlInput(BaseModel):
    action: Literal["pause", "resume", "stop", "stop_immediately", "cancel"]


class LiveControlInput(BaseModel):
    action: Literal["pause_entries", "resume", "stop"]


def password_hash(password: str, salt: bytes | None = None, iterations: int = 600_000) -> str:
    """PBKDF2-HMAC-SHA256 encoded for ADMIN_PASSWORD_HASH."""
    salt = salt or secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(derived).decode()}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, raw_iterations, raw_salt, raw_expected = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        iterations = int(raw_iterations)
        if not 100_000 <= iterations <= 2_000_000:
            return False
        salt = base64.urlsafe_b64decode(raw_salt.encode())
        expected = base64.urlsafe_b64decode(raw_expected.encode())
        actual = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
        return secrets.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def auth_secret() -> bytes:
    value = os.getenv("SESSION_SECRET")
    if not value or len(value) < 32:
        raise HTTPException(503, "SESSION_SECRET must contain at least 32 characters")
    return value.encode()


def session_token(csrf: str, expires: int) -> str:
    payload = base64.urlsafe_b64encode(canonical({"sub": "admin", "csrf": csrf, "exp": expires}).encode()).decode().rstrip("=")
    signature = hmac.new(auth_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{signature}"


def read_session(token: str | None) -> dict[str, Any] | None:
    if not token:
        return None
    try:
        payload, signature = token.rsplit(".", 1)
        expected = hmac.new(auth_secret(), payload.encode(), hashlib.sha256).hexdigest()
        if not secrets.compare_digest(signature, expected):
            return None
        decoded = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        if decoded.get("sub") != "admin" or int(decoded.get("exp", 0)) <= int(time.time()) or not isinstance(decoded.get("csrf"), str):
            return None
        return decoded
    except (ValueError, TypeError, json.JSONDecodeError, HTTPException):
        return None


def secure_cookie() -> bool:
    return os.getenv("COOKIE_SECURE", "true").lower() == "true"


def client_key(request: Request) -> str:
    # Do not trust forwarded headers unless a reviewed proxy normalizes them.
    return request.client.host if request.client else "unknown"


def login_allowed(key: str) -> bool:
    cutoff = time.time() - LOGIN_WINDOW_SECONDS
    LOGIN_FAILURES[key] = [attempt for attempt in LOGIN_FAILURES.get(key, []) if attempt > cutoff]
    return len(LOGIN_FAILURES[key]) < LOGIN_MAX_FAILURES


def encryption() -> Fernet:
    key = os.getenv("APP_ENCRYPTION_KEY")
    if not key:
        raise HTTPException(503, "APP_ENCRYPTION_KEY is required before saving credentials")
    try:
        return Fernet(key.encode())
    except (ValueError, TypeError):
        raise HTTPException(500, "APP_ENCRYPTION_KEY is invalid; use a Fernet key")


def encrypt_secret(value: Any) -> str | None:
    if value in (None, "", {}):
        return None
    return encryption().encrypt(canonical(value).encode()).decode()


def decrypt_secret(value: str | None) -> Any:
    if not value:
        return None
    try:
        return json.loads(encryption().decrypt(value.encode()).decode())
    except InvalidToken:
        raise HTTPException(500, "Saved credential cannot be decrypted; rotate it")


def validate_endpoint(raw_url: str) -> str:
    parsed = urlparse(raw_url.rstrip("/"))
    if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise HTTPException(422, "Endpoint must be an absolute HTTP(S) URL without credentials, query, or fragment")
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    local_allow = {item.strip().lower() for item in os.getenv("AI_LOCAL_ENDPOINT_ALLOWLIST", "").split(",") if item.strip()}
    host_port = f"{parsed.hostname.lower()}:{port}"
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(parsed.hostname, port, type=socket.SOCK_STREAM)}
    except socket.gaierror:
        raise HTTPException(422, "Endpoint hostname does not resolve")
    unsafe = any(ipaddress.ip_address(address).is_private or ipaddress.ip_address(address).is_loopback or ipaddress.ip_address(address).is_link_local or ipaddress.ip_address(address).is_multicast or ipaddress.ip_address(address).is_reserved or ipaddress.ip_address(address).is_unspecified for address in addresses)
    if parsed.scheme != "https" or port not in ALLOWED_REMOTE_PORTS or unsafe:
        if host_port not in local_allow:
            raise HTTPException(422, f"Local/private or non-HTTPS endpoint blocked. Administrator must allow {host_port}")
    return raw_url.rstrip("/")


def mask_identifier(value: str | None) -> str | None:
    if not value:
        return None
    return f"{value[:3]}••••{value[-4:]}" if len(value) > 7 else "••••"


def alpaca_public(row: sqlite3.Row) -> dict[str, Any]:
    return {"mode": row["mode"], "label": row["label"], "key_id_masked": "••••••••", "secret_key_masked": "••••••••", "feed": row["feed"], "base_url": row["base_url"], "last_test_status": row["last_test_status"], "last_test_at": row["last_test_at"], "account_id_masked": row["account_id_masked"], "created_at": row["created_at"], "updated_at": row["updated_at"]}


def provider_public(row: sqlite3.Row) -> dict[str, Any]:
    item = dict(row)
    item.pop("encrypted_api_key", None)
    item.pop("encrypted_headers", None)
    item["api_key_masked"] = "••••••••" if row["encrypted_api_key"] else None
    item["custom_headers_saved"] = bool(row["encrypted_headers"])
    item["supports"] = {"model_discovery": "not assumed", "streaming": "not assumed", "structured_output": "validated locally"}
    return item


def provider_request(row: sqlite3.Row, purpose: str) -> tuple[str, dict[str, Any], dict[str, str]]:
    base_url = validate_endpoint(row["base_url"])
    key = decrypt_secret(row["encrypted_api_key"])
    custom = decrypt_secret(row["encrypted_headers"]) or {}
    headers = {"content-type": "application/json", **custom}
    if key:
        headers["authorization"] = f"Bearer {key}"
    if row["profile"] == "chat_completions":
        url = f"{base_url}/chat/completions"
        body: dict[str, Any] = {"model": row["model_id"], "messages": [{"role": "user", "content": purpose}], "max_tokens": min(row["max_output_tokens"], 2000), "stream": False}
    else:
        url = f"{base_url}/responses"
        body = {"model": row["model_id"], "input": purpose, "max_output_tokens": min(row["max_output_tokens"], 2000), "stream": False}
    if row["temperature"] is not None:
        body["temperature"] = row["temperature"]
    return url, body, headers


def provider_error(exc: Exception) -> HTTPException:
    if isinstance(exc, httpx.HTTPStatusError):
        if exc.response.status_code in {401, 403}:
            return HTTPException(502, "Provider authentication failed. Check the saved API key.")
        if exc.response.status_code == 429:
            return HTTPException(502, "Provider rate limit reached. Reduce concurrency or retry later.")
        return HTTPException(502, f"Provider returned HTTP {exc.response.status_code}.")
    if isinstance(exc, (httpx.TimeoutException, httpx.NetworkError)):
        return HTTPException(504, "Provider timed out or could not be reached")
    return HTTPException(502, "Provider returned malformed JSON")


async def call_provider(row: sqlite3.Row, purpose: str = "test") -> dict[str, Any]:
    prompt = "Reply with JSON only: {\"ok\":true}" if purpose == "test" else purpose
    url, body, headers = provider_request(row, prompt)
    try:
        async with httpx.AsyncClient(timeout=row["timeout_seconds"], follow_redirects=False) as client:
            response = await client.post(url, headers=headers, json=body)
        if 300 <= response.status_code < 400:
            raise HTTPException(502, "Provider redirect rejected")
        response.raise_for_status()
        data = response.json()
        usage = data.get("usage") if isinstance(data, dict) else None
        return {"ok": True, "usage": usage, "response": data}
    except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.NetworkError, ValueError, json.JSONDecodeError) as exc:
        raise provider_error(exc)


def test_alpaca(row: sqlite3.Row) -> dict[str, Any]:
    key_id = decrypt_secret(row["encrypted_key_id"])
    secret_key = decrypt_secret(row["encrypted_secret_key"])
    headers = {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret_key}
    if row["mode"] == "data":
        url = f"{row['base_url']}/v2/stocks/AAPL/bars/latest?feed={row['feed']}"
    else:
        url = f"{row['base_url']}/v2/account"
    try:
        with httpx.Client(timeout=15, follow_redirects=False) as client:
            response = client.get(url, headers=headers)
        if 300 <= response.status_code < 400:
            raise HTTPException(502, "Alpaca redirect rejected")
        response.raise_for_status()
        payload = response.json()
        if not isinstance(payload, dict):
            raise ValueError
        if row["mode"] == "data":
            return {"status": "CONNECTED", "message": f"Market-data credentials accepted for {row['feed']} feed.", "account_id_masked": None}
        account_id = payload.get("id") or payload.get("account_number")
        if not account_id:
            raise ValueError
        return {"status": "CONNECTED", "message": f"{row['mode'].title()} account credentials accepted.", "account_id_masked": mask_identifier(str(account_id)), "account_status": payload.get("status")}
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in {401, 403}:
            raise HTTPException(502, "Alpaca authentication or entitlement failed. Check this mode's credentials and feed.")
        if exc.response.status_code == 429:
            raise HTTPException(502, "Alpaca rate limit reached. Retry later.")
        raise HTTPException(502, f"Alpaca returned HTTP {exc.response.status_code}.")
    except (httpx.TimeoutException, httpx.NetworkError):
        raise HTTPException(504, "Alpaca timed out or could not be reached")
    except (ValueError, json.JSONDecodeError):
        raise HTTPException(502, "Alpaca returned malformed JSON")


def provider_generate(row: sqlite3.Row, instructions: str, families: list[str]) -> tuple[dict[str, Any], int | None]:
    prompt = f"""You are proposing one research hypothesis. Return JSON only. No markdown, citations, code, orders, or profit claims.
Schema: {{\"family\": one of {families}, \"variant\": integer 0..2, \"name\": string max 80, \"hypothesis\": string 20..500}}.
The family and variant select a reviewed local template; your output is never executed as code.
Research instructions: {instructions}"""
    url, body, headers = provider_request(row, prompt)
    try:
        with httpx.Client(timeout=row["timeout_seconds"], follow_redirects=False) as client:
            response = client.post(url, headers=headers, json=body)
        if 300 <= response.status_code < 400:
            raise HTTPException(502, "Provider redirect rejected")
        response.raise_for_status()
        payload = response.json()
        if row["profile"] == "chat_completions":
            text = payload["choices"][0]["message"]["content"]
        else:
            text = payload.get("output_text")
            if not text:
                text = payload["output"][0]["content"][0]["text"]
        proposal = json.loads(text)
        if set(proposal) != {"family", "variant", "name", "hypothesis"}:
            raise ValueError("unexpected schema")
        family, variant = proposal["family"], proposal["variant"]
        if family not in families or not isinstance(variant, int) or isinstance(variant, bool) or not 0 <= variant <= 2:
            raise ValueError("unsupported template selection")
        if not isinstance(proposal["name"], str) or not 3 <= len(proposal["name"]) <= 80:
            raise ValueError("invalid name")
        if not isinstance(proposal["hypothesis"], str) or not 20 <= len(proposal["hypothesis"]) <= 500:
            raise ValueError("invalid hypothesis")
        usage = payload.get("usage") or {}
        tokens = usage.get("total_tokens") or usage.get("total_tokens", None)
        return proposal, tokens if isinstance(tokens, int) else None
    except HTTPException:
        raise
    except (httpx.HTTPStatusError, httpx.TimeoutException, httpx.NetworkError) as exc:
        raise provider_error(exc)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        raise HTTPException(502, "Provider output failed the required local JSON schema")


TEMPLATES = {
    "moving_average": {
        "name": "Adaptive MA crossover", "hypothesis": "A slower trend filter may reduce participation during directionless periods.",
        "variants": [{"fast": 10, "slow": 40}, {"fast": 20, "slow": 80}, {"fast": 30, "slow": 120}],
    },
    "rsi": {
        "name": "RSI recovery", "hypothesis": "Oversold recovery in a broad equity index may capture bounded mean reversion.",
        "variants": [{"period": 10, "entry": 30, "exit": 55}, {"period": 14, "entry": 35, "exit": 60}, {"period": 20, "entry": 40, "exit": 65}],
    },
    "channel_breakout": {
        "name": "Price-channel breakout", "hypothesis": "Closing above a prior price channel may identify persistent directional movement.",
        "variants": [{"lookback": 20, "exit": 10}, {"lookback": 55, "exit": 20}, {"lookback": 100, "exit": 40}],
    },
}


def strategy_source(family: str, params: dict[str, int]) -> str:
    return f'''"""Reviewed Strategy Lab template. Display/download source; trusted host executes the template ID."""
from strategy_lab import Observation, Portfolio, StrategyResult

TEMPLATE_ID = {family!r}
PARAMETERS = {params!r}

def initialize(config):
    return {{"bars_seen": 0}}

def on_closed_bar(config, observation: Observation, portfolio: Portfolio, state):
    """Return target fraction. Future bars, network, files, broker clients unavailable."""
    state = {{"bars_seen": state["bars_seen"] + 1}}
    signal = observation.reviewed_signal(TEMPLATE_ID, PARAMETERS)
    return StrategyResult(target_fraction=signal, state=state, diagnostics={{"signal": signal}})
'''


def create_candidate(session_id: str) -> dict[str, Any]:
    with connect() as connection:
        session = connection.execute("SELECT * FROM research_sessions WHERE id=?", (session_id,)).fetchone()
        if not session:
            raise HTTPException(404, "Research session not found")
        config = json.loads(session["config"])
        ordinal = session["generation_count"] + 1
        if ordinal > config["maximum_candidates"]:
            return {"generated": False, "reason": "candidate budget reached"}
        families = config["allowed_families"]
        provider = connection.execute("SELECT * FROM providers WHERE id=?", (config.get("provider_id"),)).fetchone() if config.get("provider_id") else None
        family = families[(ordinal - 1) % len(families)]
        variant_index = ((ordinal - 1) // len(families)) % 3
        name, hypothesis, tokens = None, None, None
        if provider:
            proposal = None
            last_error = None
            for _ in range(config["maximum_repair_attempts"] + 1):
                try:
                    proposal, tokens = provider_generate(provider, config["instructions"], families)
                    break
                except HTTPException as exc:
                    last_error = str(exc.detail)
            if proposal is None:
                candidate_id = str(uuid.uuid4())
                warnings = ["Provider generation failed. Attempt retained; no fallback provider or model used."]
                connection.execute(
                    "INSERT INTO candidates VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (candidate_id, session_id, ordinal, "INVALID", None, f"Failed provider attempt · {ordinal}", "No validated hypothesis returned.", "{}", "", digest(b""), digest({"invalid": candidate_id}), "[]", provider["id"], provider["model_id"], PROMPT_VERSION, tokens, None, canonical(warnings), last_error, iso()),
                )
                next_run = utcnow() + timedelta(minutes=config["generation_interval_minutes"])
                connection.execute("UPDATE research_sessions SET generation_count=?,token_count=token_count+?,next_run_at=?,last_error=? WHERE id=?", (ordinal, tokens or 0, next_run.isoformat(), last_error, session_id))
                return {"generated": True, "candidate_id": candidate_id, "status": "INVALID"}
            family, variant_index = proposal["family"], proposal["variant"]
            name, hypothesis = proposal["name"], proposal["hypothesis"]
        template = TEMPLATES[family]
        variant = template["variants"][variant_index]
        source = strategy_source(family, variant)
        source_hash = digest(source.encode())
        normalized_hash = digest({"family": family, "parameters": variant})
        duplicate = connection.execute("SELECT id FROM candidates WHERE session_id=? AND normalized_hash=?", (session_id, normalized_hash)).fetchone()
        status = "DUPLICATE" if duplicate else "VALID"
        candidate_id = str(uuid.uuid4())
        warnings = ["Reviewed template mode: arbitrary generated Python execution is disabled.", "Model-generated hypothesis; external research and citations were not verified."]
        connection.execute(
            "INSERT INTO candidates VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (candidate_id, session_id, ordinal, status, family, name or f"{template['name']} · {ordinal}", hypothesis or template["hypothesis"], canonical(variant), source, source_hash, normalized_hash, canonical(["strategy-lab-stdlib==2.0"]), provider["id"] if provider else None, provider["model_id"] if provider else "reviewed-template-demo", PROMPT_VERSION, tokens, None, canonical(warnings), None, iso()),
        )
        interval = timedelta(minutes=config["generation_interval_minutes"])
        next_run = utcnow() + interval
        connection.execute("UPDATE research_sessions SET generation_count=?,token_count=token_count+?,next_run_at=?,last_error=NULL WHERE id=?", (ordinal, tokens or 0, next_run.isoformat(), session_id))
    audit("candidate.generated", "candidate", candidate_id, {"session_id": session_id, "status": status, "source_hash": source_hash})
    return {"generated": True, "candidate_id": candidate_id, "status": status}


def moving_average(values: list[Decimal], period: int, index: int) -> Decimal | None:
    if index + 1 < period:
        return None
    return sum(values[index - period + 1:index + 1]) / period


def rsi(values: list[Decimal], period: int, index: int) -> Decimal | None:
    if index < period:
        return None
    changes = [values[j] - values[j - 1] for j in range(index - period + 1, index + 1)]
    gains = sum(max(change, Decimal("0")) for change in changes) / period
    losses = sum(max(-change, Decimal("0")) for change in changes) / period
    if losses == 0:
        return Decimal("100")
    return Decimal("100") - Decimal("100") / (Decimal("1") + gains / losses)


def desired_position(family: str, params: dict[str, int], closes: list[Decimal], index: int, current: bool) -> bool:
    if family == "moving_average":
        fast, slow = moving_average(closes, params["fast"], index), moving_average(closes, params["slow"], index)
        return current if fast is None or slow is None else fast > slow
    if family == "rsi":
        value = rsi(closes, params["period"], index)
        if value is None:
            return current
        if not current and value < params["entry"]:
            return True
        if current and value > params["exit"]:
            return False
        return current
    lookback, exit_period = params["lookback"], params["exit"]
    if index < lookback:
        return current
    prior_high = max(closes[index - lookback:index])
    prior_low = min(closes[index - exit_period:index])
    return True if not current and closes[index] > prior_high else False if current and closes[index] < prior_low else current


def backtest_candidate(candidate: sqlite3.Row, config: dict[str, Any]) -> dict[str, Any]:
    bars = demo_bars(config["timeframe"])
    closes = [Decimal(bar["close"]) for bar in bars]
    opens = [Decimal(bar["open"]) for bar in bars]
    params = json.loads(candidate["parameters"])
    cash = decimal_value(config["starting_capital"], positive=True)
    starting = cash
    fraction = decimal_value(config["allocation_fraction"], positive=True)
    round_trip_bps = decimal_value(config["fee_bps"]) + decimal_value(config["spread_bps"]) / 2 + decimal_value(config["slippage_bps"])
    shares = Decimal("0")
    position = False
    pending: bool | None = None
    entry_value = Decimal("0")
    costs = Decimal("0")
    turnover = Decimal("0")
    trades: list[dict[str, Any]] = []
    equity: list[Decimal] = []
    drawdowns: list[Decimal] = []
    period_returns: list[float] = []
    peak = starting
    days_in_market = 0
    for i, bar in enumerate(bars):
        # Intent from prior closed bar fills only at this bar open: no same-bar access/fill.
        if pending is not None and pending != position:
            raw_price = opens[i]
            adjustment = round_trip_bps / Decimal("10000")
            fill_price = raw_price * (Decimal("1") + adjustment if pending else Decimal("1") - adjustment)
            if pending:
                notional = cash * fraction
                quantity = (notional / fill_price).quantize(Decimal("0.001"), rounding=ROUND_DOWN)
                fee = quantity * raw_price * decimal_value(config["fee_bps"]) / Decimal("10000")
                if quantity > 0:
                    cash -= quantity * fill_price + fee
                    shares = quantity
                    entry_value = quantity * fill_price + fee
                    costs += quantity * (fill_price - raw_price) + fee
                    turnover += quantity * raw_price
                    position = True
                    trades.append({"entry_time": bar["timestamp"], "entry_price": f"{fill_price:.4f}", "quantity": f"{quantity:.3f}", "fees": f"{fee:.2f}", "exit_time": None, "exit_price": None, "net_pnl": None})
            else:
                fee = shares * raw_price * decimal_value(config["fee_bps"]) / Decimal("10000")
                proceeds = shares * fill_price - fee
                cash += proceeds
                costs += shares * (raw_price - fill_price) + fee
                turnover += shares * raw_price
                if trades:
                    trades[-1].update({"exit_time": bar["timestamp"], "exit_price": f"{fill_price:.4f}", "fees": f"{Decimal(trades[-1]['fees']) + fee:.2f}", "net_pnl": f"{proceeds - entry_value:.2f}"})
                shares = Decimal("0")
                position = False
            pending = None
        value = cash + shares * Decimal(bar["close"])
        if equity:
            period_returns.append(float(value / equity[-1] - 1))
        equity.append(value)
        peak = max(peak, value)
        drawdowns.append((value / peak - 1) * Decimal("100"))
        days_in_market += int(position)
        desired = desired_position(candidate["family"], params, closes, i, position)
        if desired != position:
            pending = desired
    ending = equity[-1]
    net_return = (ending / starting - 1) * 100
    benchmark = (closes[-1] / closes[0] - 1) * 100
    maximum_drawdown = abs(min(drawdowns, default=Decimal("0")))
    closed = [trade for trade in trades if trade["net_pnl"] is not None]
    wins = [Decimal(trade["net_pnl"]) for trade in closed if Decimal(trade["net_pnl"]) > 0]
    losses = [Decimal(trade["net_pnl"]) for trade in closed if Decimal(trade["net_pnl"]) < 0]
    mean = statistics.mean(period_returns) if period_returns else 0
    stdev = statistics.stdev(period_returns) if len(period_returns) > 1 else 0
    downside = [min(item, 0) for item in period_returns]
    downside_dev = math.sqrt(sum(item * item for item in downside) / len(downside)) if downside else 0
    sharpe = mean / stdev * math.sqrt(252) if stdev else None
    sortino = mean / downside_dev * math.sqrt(252) if downside_dev else None
    warnings = ["Deterministic demo dataset; not observed market data.", "Daily bars; next-open fills use disclosed costs, not queue position or broker parity.", "Final holdout is excluded from selection ranking and remains locked in this demo.", "Multiple testing can inflate apparent performance."]
    if len(closed) < config["minimum_trade_count"]:
        warnings.append("Trade count below configured minimum; ratios are unstable.")
    metrics = {
        "net_return_percent": round(float(net_return), 2), "benchmark_return_percent": round(float(benchmark), 2),
        "maximum_drawdown_percent": round(float(maximum_drawdown), 2), "worst_day_percent": round(min(period_returns, default=0) * 100, 2),
        "sharpe": round(sharpe, 2) if sharpe is not None else None, "sortino": round(sortino, 2) if sortino is not None else None,
        "profit_factor": round(float(sum(wins) / abs(sum(losses))), 2) if losses else None,
        "win_rate_percent": round(len(wins) / len(closed) * 100, 2) if closed else None,
        "average_win": f"{sum(wins) / len(wins):.2f}" if wins else None, "average_loss": f"{sum(losses) / len(losses):.2f}" if losses else None,
        "trade_count": len(closed), "turnover_percent": round(float(turnover / starting * 100), 2),
        "exposure_percent": round(days_in_market / len(bars) * 100, 2), "time_in_market_percent": round(days_in_market / len(bars) * 100, 2),
        "costs": f"{costs:.2f}", "ending_equity": f"{ending:.2f}",
        "validation_stability": "Insufficient evidence" if len(closed) < max(config["minimum_trade_count"], 5) else "Needs walk-forward review",
    }
    return {"metrics": metrics, "equity_curve": [round(float(v), 2) for v in equity], "drawdown_curve": [round(float(v), 2) for v in drawdowns], "trades": trades, "warnings": warnings}


def run_backtests(session_id: str) -> None:
    with connect() as connection:
        session = connection.execute("SELECT * FROM research_sessions WHERE id=?", (session_id,)).fetchone()
        if not session:
            raise HTTPException(404, "Research session not found")
        config = json.loads(session["config"])
        connection.execute("UPDATE research_sessions SET state='BACKTESTING',next_run_at=NULL,in_flight=0 WHERE id=?", (session_id,))
        candidates = connection.execute("SELECT * FROM candidates WHERE session_id=? ORDER BY ordinal", (session_id,)).fetchall()
    errors = 0
    for candidate in candidates:
        if candidate["status"] != "VALID":
            continue
        backtest_id = str(uuid.uuid4())
        assumptions = {key: config[key] for key in ("instruments", "timeframe", "starting_capital", "fee_bps", "spread_bps", "slippage_bps", "historical_start", "historical_end", "development_percent", "validation_percent", "holdout_percent")}
        try:
            result = backtest_candidate(candidate, config)
            with connect() as connection:
                connection.execute(
                    "INSERT OR IGNORE INTO backtests VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (backtest_id, session_id, candidate["id"], "COMPLETED", f"{DATASET_ID}-{config['timeframe']}", digest(demo_bars(config["timeframe"])), ENGINE_VERSION, ENGINE_HASH, canonical(assumptions), canonical(result["metrics"]), canonical(result["equity_curve"]), canonical(result["drawdown_curve"]), canonical(result["trades"]), canonical(result["warnings"]), None, iso(), iso()),
                )
            audit("backtest.completed", "backtest", backtest_id, {"candidate_id": candidate["id"], "dataset": f"{DATASET_ID}-{config['timeframe']}", "instrument": config["instruments"][0], "timeframe": config["timeframe"]})
        except Exception as exc:
            errors += 1
            with connect() as connection:
                connection.execute("INSERT OR IGNORE INTO backtests VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (backtest_id, session_id, candidate["id"], "FAILED", f"{DATASET_ID}-{config['timeframe']}", digest(demo_bars(config["timeframe"])), ENGINE_VERSION, ENGINE_HASH, canonical(assumptions), None, None, None, None, "[]", str(exc)[:500], iso(), iso()))
    with connect() as connection:
        state = "COMPLETED_WITH_ERRORS" if errors else "COMPLETED"
        connection.execute("UPDATE research_sessions SET state=?,stopped_at=? WHERE id=?", (state, iso(), session_id))
    audit("session.finalized", "research_session", session_id, {"state": state, "errors": errors})


def process_due_sessions() -> int:
    current = iso()
    with connect() as connection:
        sessions = connection.execute("SELECT id,config,generation_count,token_count,started_at FROM research_sessions WHERE state='GENERATING' AND in_flight=0 AND next_run_at<=?", (current,)).fetchall()
    processed = 0
    for session in sessions:
        config = json.loads(session["config"])
        started = datetime.fromisoformat(session["started_at"]) if session["started_at"] else utcnow()
        exhausted = session["generation_count"] >= config["maximum_candidates"] or session["token_count"] >= config["token_budget"] or utcnow() >= started + timedelta(minutes=config["maximum_duration_minutes"])
        if exhausted:
            run_backtests(session["id"])
            processed += 1
            continue
        with connect() as connection:
            changed = connection.execute("UPDATE research_sessions SET in_flight=1 WHERE id=? AND state='GENERATING' AND in_flight=0", (session["id"],)).rowcount
        if not changed:
            continue
        try:
            create_candidate(session["id"])
        finally:
            with connect() as connection:
                connection.execute("UPDATE research_sessions SET in_flight=0 WHERE id=?", (session["id"],))
        processed += 1
    return processed


async def scheduler(stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            process_due_sessions()
        except Exception:
            pass
        try:
            await asyncio.wait_for(stop.wait(), timeout=5)
        except TimeoutError:
            pass


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    stop = asyncio.Event()
    task = asyncio.create_task(scheduler(stop))
    yield
    stop.set()
    await task


init_db()
app = FastAPI(title="Strategy Lab", version="2.0", lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=[item for item in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if item], allow_credentials=True, allow_methods=["GET", "POST", "DELETE"], allow_headers=["content-type", "x-csrf-token"])

PUBLIC_API_PATHS = {"/api/status", "/api/health", "/api/readiness", "/api/auth/session", "/api/auth/login"}


@app.middleware("http")
async def authenticate(request: Request, call_next):
    if not request.url.path.startswith("/api/") or request.url.path in PUBLIC_API_PATHS:
        return await call_next(request)
    session = read_session(request.cookies.get(SESSION_COOKIE))
    if not session:
        return JSONResponse({"detail": "Authentication required"}, status_code=401)
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not secrets.compare_digest(request.headers.get("x-csrf-token", ""), session["csrf"]):
        return JSONResponse({"detail": "CSRF validation failed"}, status_code=403)
    request.state.user = session["sub"]
    return await call_next(request)


@app.get("/api/status")
def status():
    return {"status": "ok", "mode": "DEMO", "authentication_required": True, "live_trading_enabled": False, "arbitrary_python_enabled": False, "broker_submission_enabled": False, "engine_version": ENGINE_VERSION, "engine_hash": ENGINE_HASH, "disclaimer": DISCLAIMER}


@app.get("/api/auth/session")
def auth_session(request: Request):
    session = read_session(request.cookies.get(SESSION_COOKIE))
    return {"authenticated": bool(session), "user": session["sub"] if session else None, "csrf_token": session["csrf"] if session else None}


@app.post("/api/auth/login")
def login(value: LoginInput, request: Request, response: Response):
    key = client_key(request)
    if not login_allowed(key):
        raise HTTPException(429, "Too many failed attempts. Try again in 15 minutes.")
    configured = os.getenv("ADMIN_PASSWORD_HASH", "")
    if not configured:
        raise HTTPException(503, "ADMIN_PASSWORD_HASH is not configured")
    if not verify_password(value.password, configured):
        LOGIN_FAILURES.setdefault(key, []).append(time.time())
        audit("auth.login_failed", "user", "admin", {"client": key}, actor="anonymous")
        # Hash work already dominates; fixed delay limits online guessing further.
        time.sleep(0.5)
        raise HTTPException(401, "Invalid password")
    LOGIN_FAILURES.pop(key, None)
    csrf = secrets.token_urlsafe(32)
    expires = int(time.time()) + SESSION_TTL_SECONDS
    response.set_cookie(SESSION_COOKIE, session_token(csrf, expires), max_age=SESSION_TTL_SECONDS, httponly=True, secure=secure_cookie(), samesite="strict", path="/")
    audit("auth.login_succeeded", "user", "admin", {"client": key})
    return {"authenticated": True, "user": "admin", "csrf_token": csrf, "expires_at": datetime.fromtimestamp(expires, UTC).isoformat()}


@app.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie(SESSION_COOKIE, path="/", secure=secure_cookie(), httponly=True, samesite="strict")
    audit("auth.logout", "user", "admin", {})
    return {"authenticated": False}


@app.get("/api/health")
def health():
    return {"status": "healthy", "time": iso()}


@app.get("/api/readiness")
def readiness():
    with connect() as connection:
        connection.execute("SELECT 1").fetchone()
    return {"status": "ready", "database": str(DB)}


@app.get("/api/demo-bars")
def get_demo_bars():
    timeframe = "1d"
    return {"label": "Demo · deterministic sample data · not market or symbol-specific data", "dataset_id": f"{DATASET_ID}-{timeframe}", "symbol": "DEMO", "timeframe": timeframe, "bars": demo_bars(timeframe)}


@app.get("/api/providers")
def list_providers():
    with connect() as connection:
        return [provider_public(row) for row in connection.execute("SELECT * FROM providers ORDER BY created_at DESC").fetchall()]


@app.post("/api/providers")
def save_provider(value: ProviderInput):
    base_url = validate_endpoint(value.base_url)
    provider_id = str(uuid.uuid4())
    encrypted_key = encrypt_secret(value.api_key)
    encrypted_headers = encrypt_secret(value.custom_headers)
    with connect() as connection:
        connection.execute("INSERT INTO providers VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (provider_id, value.name, base_url, value.profile, value.model_id, encrypted_key, encrypted_headers, value.timeout_seconds, value.max_output_tokens, value.temperature, value.concurrency_limit, value.max_retries, value.input_price_per_million, value.output_price_per_million, iso(), iso()))
        row = connection.execute("SELECT * FROM providers WHERE id=?", (provider_id,)).fetchone()
    audit("provider.saved", "provider", provider_id, {"name": value.name, "base_url": base_url, "profile": value.profile})
    return provider_public(row)


@app.delete("/api/providers/{provider_id}")
def delete_provider(provider_id: str):
    with connect() as connection:
        deleted = connection.execute("DELETE FROM providers WHERE id=?", (provider_id,)).rowcount
    if not deleted:
        raise HTTPException(404, "Provider not found")
    audit("provider.deleted", "provider", provider_id, {})
    return {"deleted": True}


@app.post("/api/providers/{provider_id}/test")
async def test_provider(provider_id: str):
    with connect() as connection:
        row = connection.execute("SELECT * FROM providers WHERE id=?", (provider_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Provider not found")
    result = await call_provider(row)
    audit("provider.tested", "provider", provider_id, {"ok": True})
    return {"ok": True, "message": "Connection succeeded", "usage_reported": result["usage"] is not None}


@app.get("/api/alpaca-connections")
def list_alpaca_connections():
    with connect() as connection:
        rows = connection.execute("SELECT * FROM alpaca_connections ORDER BY CASE mode WHEN 'data' THEN 1 WHEN 'paper' THEN 2 ELSE 3 END").fetchall()
    return [alpaca_public(row) for row in rows]


@app.post("/api/alpaca-connections")
def save_alpaca_connection(value: AlpacaConnectionInput):
    if value.mode == "live" and os.getenv("ENABLE_LIVE_TRADING", "false").lower() != "true":
        raise HTTPException(403, "Live credential storage disabled by server setting")
    base_url = {"data": "https://data.alpaca.markets", "paper": "https://paper-api.alpaca.markets", "live": "https://api.alpaca.markets"}[value.mode]
    current = iso()
    encrypted_key = encrypt_secret(value.key_id)
    encrypted_secret = encrypt_secret(value.secret_key)
    with connect() as connection:
        existing = connection.execute("SELECT created_at FROM alpaca_connections WHERE mode=?", (value.mode,)).fetchone()
        connection.execute(
            "INSERT INTO alpaca_connections VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(mode) DO UPDATE SET label=excluded.label,encrypted_key_id=excluded.encrypted_key_id,encrypted_secret_key=excluded.encrypted_secret_key,feed=excluded.feed,base_url=excluded.base_url,last_test_status=NULL,last_test_at=NULL,account_id_masked=NULL,updated_at=excluded.updated_at",
            (value.mode, value.label, encrypted_key, encrypted_secret, value.feed, base_url, None, None, None, existing["created_at"] if existing else current, current),
        )
        row = connection.execute("SELECT * FROM alpaca_connections WHERE mode=?", (value.mode,)).fetchone()
    audit("alpaca.credentials_saved", "alpaca_connection", value.mode, {"mode": value.mode, "feed": value.feed, "base_url": base_url})
    return alpaca_public(row)


@app.delete("/api/alpaca-connections/{mode}")
def delete_alpaca_connection(mode: Literal["data", "paper", "live"]):
    with connect() as connection:
        deleted = connection.execute("DELETE FROM alpaca_connections WHERE mode=?", (mode,)).rowcount
    if not deleted:
        raise HTTPException(404, "Alpaca connection not found")
    audit("alpaca.credentials_deleted", "alpaca_connection", mode, {"mode": mode})
    return {"deleted": True}


@app.post("/api/alpaca-connections/{mode}/test")
def test_alpaca_connection(mode: Literal["data", "paper", "live"]):
    with connect() as connection:
        row = connection.execute("SELECT * FROM alpaca_connections WHERE mode=?", (mode,)).fetchone()
    if not row:
        raise HTTPException(404, "Alpaca connection not found")
    try:
        result = test_alpaca(row)
    except HTTPException as exc:
        with connect() as connection:
            connection.execute("UPDATE alpaca_connections SET last_test_status='FAILED',last_test_at=? WHERE mode=?", (iso(), mode))
        audit("alpaca.connection_failed", "alpaca_connection", mode, {"mode": mode, "error": str(exc.detail)})
        raise
    with connect() as connection:
        connection.execute("UPDATE alpaca_connections SET last_test_status=?,last_test_at=?,account_id_masked=? WHERE mode=?", (result["status"], iso(), result["account_id_masked"], mode))
    audit("alpaca.connection_tested", "alpaca_connection", mode, {"mode": mode, "status": result["status"], "account_id_masked": result["account_id_masked"]})
    return result


@app.get("/api/research-sessions")
def list_sessions():
    with connect() as connection:
        rows = connection.execute("SELECT * FROM research_sessions ORDER BY created_at DESC").fetchall()
    return [json_row(row, ("config",)) for row in rows]


@app.post("/api/research-sessions")
def create_session(config: SessionConfig):
    if config.provider_id:
        with connect() as connection:
            if not connection.execute("SELECT id FROM providers WHERE id=?", (config.provider_id,)).fetchone():
                raise HTTPException(422, "Selected provider does not exist")
    session_id = str(uuid.uuid4())
    body = config.model_dump(mode="json")
    with connect() as connection:
        connection.execute("INSERT INTO research_sessions(id,name,state,config,config_hash,created_at) VALUES(?,?,?,?,?,?)", (session_id, config.name, "DRAFT", canonical(body), digest(body), iso()))
    audit("session.created", "research_session", session_id, {"config_hash": digest(body)})
    return get_session(session_id)


@app.get("/api/research-sessions/{session_id}")
def get_session(session_id: str):
    with connect() as connection:
        row = connection.execute("SELECT * FROM research_sessions WHERE id=?", (session_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Research session not found")
        candidates = connection.execute("SELECT * FROM candidates WHERE session_id=? ORDER BY ordinal", (session_id,)).fetchall()
        backtests = connection.execute("SELECT * FROM backtests WHERE session_id=? ORDER BY completed_at DESC", (session_id,)).fetchall()
    session = json_row(row, ("config",))
    session["candidates"] = [json_row(item, ("parameters", "dependency_manifest", "warnings")) for item in candidates]
    session["backtests"] = [json_row(item, ("assumptions", "metrics", "equity_curve", "drawdown_curve", "trades", "warnings")) for item in backtests]
    counts: dict[str, int] = {}
    for candidate in session["candidates"]:
        counts[candidate["status"]] = counts.get(candidate["status"], 0) + 1
    session["summary"] = {"candidates_generated": len(candidates), "completed_tests": sum(item["status"] == "COMPLETED" for item in session["backtests"]), "failed_tests": sum(item["status"] == "FAILED" for item in session["backtests"]), **{key.lower(): value for key, value in counts.items()}}
    return session


@app.post("/api/research-sessions/{session_id}/start")
def start_session(session_id: str):
    with connect() as connection:
        row = connection.execute("SELECT * FROM research_sessions WHERE id=?", (session_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Research session not found")
        if row["state"] != "DRAFT":
            raise HTTPException(409, "Only a draft session can start")
        config = json.loads(row["config"])
        due = utcnow() if config["generate_immediately"] else utcnow() + timedelta(minutes=config["generation_interval_minutes"])
        connection.execute("UPDATE research_sessions SET state='GENERATING',started_at=?,next_run_at=? WHERE id=?", (iso(), due.isoformat(), session_id))
    process_due_sessions()
    audit("session.started", "research_session", session_id, {"generate_immediately": config["generate_immediately"]})
    return get_session(session_id)


@app.post("/api/research-sessions/{session_id}/control")
def control_session(session_id: str, control: ControlInput):
    with connect() as connection:
        row = connection.execute("SELECT * FROM research_sessions WHERE id=?", (session_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Research session not found")
        config = json.loads(row["config"])
        state = row["state"]
        if control.action == "pause" and state == "GENERATING":
            connection.execute("UPDATE research_sessions SET state='PAUSED',next_run_at=NULL WHERE id=?", (session_id,))
        elif control.action == "resume" and state == "PAUSED":
            due = utcnow() + timedelta(minutes=config["generation_interval_minutes"])
            connection.execute("UPDATE research_sessions SET state='GENERATING',next_run_at=? WHERE id=?", (due.isoformat(), session_id))
        elif control.action in {"stop", "stop_immediately"} and state in {"GENERATING", "PAUSED", "STOPPING"}:
            connection.execute("UPDATE research_sessions SET state='STOPPING',next_run_at=NULL,cancel_epoch=cancel_epoch+? WHERE id=?", (int(control.action == "stop_immediately"), session_id))
        elif control.action == "cancel" and state not in {"COMPLETED", "COMPLETED_WITH_ERRORS", "CANCELED"}:
            connection.execute("UPDATE research_sessions SET state='CANCELED',next_run_at=NULL,stopped_at=?,cancel_epoch=cancel_epoch+1 WHERE id=?", (iso(), session_id))
        else:
            raise HTTPException(409, f"Cannot {control.action} session in {state}")
    if control.action in {"stop", "stop_immediately"}:
        run_backtests(session_id)
    audit(f"session.{control.action}", "research_session", session_id, {})
    return get_session(session_id)


@app.post("/api/internal/process-due")
def run_due_for_local_testing():
    if os.getenv("ENABLE_INTERNAL_TEST_ROUTES", "false").lower() != "true":
        raise HTTPException(404, "Not found")
    return {"processed": process_due_sessions()}


@app.get("/api/backtests")
def list_backtests(session_id: str | None = None):
    query = "SELECT b.*,c.name,c.family,c.source,c.source_hash,c.parameters,c.hypothesis,c.dependency_manifest FROM backtests b JOIN candidates c ON c.id=b.candidate_id"
    params: tuple[Any, ...] = ()
    if session_id:
        query += " WHERE b.session_id=?"
        params = (session_id,)
    query += " ORDER BY b.completed_at DESC"
    with connect() as connection:
        rows = connection.execute(query, params).fetchall()
    return [json_row(row, ("assumptions", "metrics", "equity_curve", "drawdown_curve", "trades", "warnings", "parameters", "dependency_manifest")) for row in rows]


@app.get("/api/backtests/{backtest_id}")
def get_backtest(backtest_id: str):
    with connect() as connection:
        row = connection.execute("SELECT b.*,c.name,c.family,c.source,c.source_hash,c.parameters,c.hypothesis,c.dependency_manifest,c.prompt_version,c.created_at AS generated_at FROM backtests b JOIN candidates c ON c.id=b.candidate_id WHERE b.id=?", (backtest_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Backtest not found")
    return json_row(row, ("assumptions", "metrics", "equity_curve", "drawdown_curve", "trades", "warnings", "parameters", "dependency_manifest"))


@app.post("/api/live-tests")
def create_live_test(value: LiveTestInput):
    with connect() as connection:
        backtest = connection.execute("SELECT b.*,c.source,c.source_hash,c.parameters,c.dependency_manifest FROM backtests b JOIN candidates c ON c.id=b.candidate_id WHERE b.id=? AND b.status='COMPLETED'", (value.backtest_id,)).fetchone()
    if not backtest:
        raise HTTPException(422, "A completed compatible backtest is required")
    live_id = str(uuid.uuid4())
    config = value.model_dump(mode="json", exclude={"confirmation"})
    cash = f"{decimal_value(value.starting_virtual_cash):.2f}"
    expires = utcnow() + timedelta(hours=value.duration_hours)
    warnings = ["Current market data with app-simulated orders only. No broker order route exists.", "Waiting for configured Alpaca market-data credentials and an eligible market session.", "Simulated fills cannot reproduce queue position or exact broker execution."]
    state = "WAITING_FOR_DATA"
    with connect() as connection:
        connection.execute("INSERT INTO live_tests VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", (live_id, backtest["id"], backtest["candidate_id"], backtest["source_hash"], backtest["source"], backtest["parameters"], backtest["dependency_manifest"], ENGINE_VERSION, state, "LIVE_DATA_SIMULATED", canonical(config), cash, cash, "[]", "[]", "[]", None, iso(), expires.isoformat(), 0, canonical(warnings)))
    audit("live_test.created", "live_test", live_id, {"backtest_id": backtest["id"], "strategy_hash": backtest["source_hash"], "mode": "LIVE_DATA_SIMULATED"})
    return get_live_test(live_id)


@app.get("/api/live-tests")
def list_live_tests():
    with connect() as connection:
        rows = connection.execute("SELECT l.*,c.name FROM live_tests l JOIN candidates c ON c.id=l.candidate_id ORDER BY l.created_at DESC").fetchall()
    return [json_row(row, ("config", "positions", "pending_orders", "fills", "warnings")) for row in rows]


@app.get("/api/live-tests/{live_id}")
def get_live_test(live_id: str):
    with connect() as connection:
        row = connection.execute("SELECT l.*,c.name FROM live_tests l JOIN candidates c ON c.id=l.candidate_id WHERE l.id=?", (live_id,)).fetchone()
    if not row:
        raise HTTPException(404, "Live data test not found")
    return json_row(row, ("config", "positions", "pending_orders", "fills", "warnings"))


@app.post("/api/live-tests/{live_id}/control")
def control_live_test(live_id: str, value: LiveControlInput):
    with connect() as connection:
        row = connection.execute("SELECT * FROM live_tests WHERE id=?", (live_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Live data test not found")
        if value.action == "pause_entries":
            connection.execute("UPDATE live_tests SET paused_entries=1,state='PAUSED_ENTRIES' WHERE id=?", (live_id,))
        elif value.action == "resume":
            connection.execute("UPDATE live_tests SET paused_entries=0,state='WAITING_FOR_DATA' WHERE id=?", (live_id,))
        else:
            connection.execute("UPDATE live_tests SET state='STOPPED',paused_entries=1 WHERE id=?", (live_id,))
    audit(f"live_test.{value.action}", "live_test", live_id, {})
    return get_live_test(live_id)


@app.get("/api/activity")
def activity(limit: int = Query(default=100, ge=1, le=500)):
    with connect() as connection:
        rows = connection.execute("SELECT * FROM audit_events ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [json_row(row, ("payload",)) for row in rows]


@app.get("/api/trading/status")
def trading_status():
    return {"broker_paper": "DISABLED_UNTIL_REVIEWED_ADAPTER", "live": "DISABLED", "live_environment_flag": os.getenv("ENABLE_LIVE_TRADING", "false").lower() == "true", "broker_submission_code_path": False, "emergency_stop": "HALTED", "reason": "Authenticated broker execution, reconciliation, and independent deployment are not implemented."}


@app.post("/api/orders")
def orders_are_blocked():
    raise HTTPException(403, "Broker order submission is not implemented. No paper or live order was sent.")


STATIC = ROOT / "static"
if STATIC.is_dir():
    app.mount("/", StaticFiles(directory=STATIC, html=True), name="frontend")
