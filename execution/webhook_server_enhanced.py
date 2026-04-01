"""
FastAPI webhook server for TradingView alerts.

This version is hardened for funded-account style deployments:
- persistent runtime state
- broker-fed account snapshot checks
- strict live mode secret enforcement
- instrument allow-list enforcement
- automatic flatten when a hard breaker opens
"""

import hashlib
import hmac
import json
import os
import re
import sys
from pathlib import Path
from datetime import datetime
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, Request, HTTPException, Header, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from dotenv import load_dotenv

load_dotenv()

from config.prop_firm_configs import PROP_FIRM_CONFIGS
from config.instrument_specs import INSTRUMENT_SPECS
from core.prop_firm_simulator import PropFirmSimulator
from database.db_manager import DBManager
from database.state_store import StateStore
from execution.position_sync import PositionSynchronizer
from execution.circuit_breakers import CircuitBreakers

app = FastAPI(title="VWAP Bot Webhook Server")

TV_WEBHOOK_SECRET = os.getenv("TV_WEBHOOK_SECRET", "")
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "")
DB_PATH = os.getenv("DB_PATH", "./database/trading_analysis.db")
STATE_PATH = os.getenv("STATE_PATH", "./database/runtime_state.json")
PROP_FIRM = os.getenv("PROP_FIRM", "TOPSTEP_50K")
TRADING_MODE = os.getenv("MODE", "paper").upper()
TRADOVATE_USE_DEMO = os.getenv(
    "TRADOVATE_USE_DEMO",
    "false" if TRADING_MODE == "TOPSTEP_LIVE" else "true",
).lower() == "true"
INTERNAL_DAILY_HARD_STOP_USD = float(os.getenv("INTERNAL_DAILY_HARD_STOP_USD", "350"))
MAX_DATA_AGE_SECONDS = int(os.getenv("MAX_DATA_AGE_SECONDS", "60"))
MAX_BROKER_PING_AGE_SECONDS = int(os.getenv("MAX_BROKER_PING_AGE_SECONDS", "15"))

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

runtime = {
    "position_sync": None,
    "circuit_breakers": None,
    "state_store": None,
}


class EntryPayload(BaseModel):
    ticker: str = Field(..., description="Instrument ticker, for example MES1!")
    action: str = Field(..., description="buy or sell")
    order_type: str = Field(default="market", alias="orderType")
    quantity: int = Field(default=1)
    price: str = Field(..., description="Entry price")
    timestamp: str = Field(..., description="ISO timestamp")
    setup: str = Field(default="", description="Setup type")
    stop_price: Optional[str] = Field(default=None, alias="stopPrice")
    target_price: Optional[str] = Field(default=None, alias="targetPrice")


class ExitPayload(BaseModel):
    ticker: str
    action: str = Field(..., description="exit or close")
    quantity: int
    price: str
    timestamp: str
    reason: str = Field(default="", description="Exit reason")


class OrderUpdatePayload(BaseModel):
    order_id: str
    status: str = Field(..., description="filled, partial, cancelled")
    filled_qty: int
    avg_fill_price: str
    timestamp: str


def get_state_store() -> StateStore:
    if runtime["state_store"] is None:
        runtime["state_store"] = StateStore(STATE_PATH)
    return runtime["state_store"]


def get_db():
    return DBManager(DB_PATH)


def get_prop_config() -> dict:
    return PROP_FIRM_CONFIGS.get(PROP_FIRM, PROP_FIRM_CONFIGS["TOPSTEP_50K"])


def verify_signature(body: bytes, signature: str) -> bool:
    """Verifies the TradingView webhook signature."""
    if not TV_WEBHOOK_SECRET:
        return TRADING_MODE != "TOPSTEP_LIVE"

    expected = hmac.new(TV_WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature or "")


def normalise_symbol(ticker: str) -> str:
    letters = re.sub(r"[^A-Z]", "", ticker.upper())
    for symbol in sorted(INSTRUMENT_SPECS.keys(), key=len, reverse=True):
        if letters.startswith(symbol):
            return symbol
    return letters


def ensure_allowed_instrument(ticker: str):
    base_symbol = normalise_symbol(ticker)
    allowed = set(get_prop_config().get("instruments", []))
    if allowed and base_symbol not in allowed:
        raise HTTPException(
            status_code=403,
            detail=f"Instrument {base_symbol} is not allowed for {PROP_FIRM}",
        )
    return base_symbol


def build_signal_id(ticker: str, action: str, timestamp: str, price: str) -> str:
    raw = f"{ticker}|{action}|{timestamp}|{price}"
    return hashlib.sha256(raw.encode()).hexdigest()


def get_circuit_breakers():
    if runtime["circuit_breakers"] is None:
        breakers = CircuitBreakers(get_prop_config())
        breakers.import_state(get_state_store().get("circuit_breakers", {}))
        runtime["circuit_breakers"] = breakers
    return runtime["circuit_breakers"]


def save_breaker_state():
    breakers = runtime.get("circuit_breakers")
    if breakers is not None:
        get_state_store().set("circuit_breakers", breakers.export_state())


def get_position_sync():
    if runtime["position_sync"] is None:
        from data.tradovate_data_provider import TradovateDataProvider

        provider = TradovateDataProvider.from_env(use_demo=TRADOVATE_USE_DEMO)
        db = get_db()
        runtime["position_sync"] = PositionSynchronizer(provider, db)
    return runtime["position_sync"]


def _derive_consecutive_losses(db: DBManager, limit: int = 10) -> int:
    trades = db.get_recent_live_trades(limit=limit)
    count = 0
    for trade in trades:
        net_pnl = trade.get("net_pnl")
        if net_pnl is None:
            continue
        if net_pnl < 0:
            count += 1
        else:
            break
    return count


def _calculate_mll_floor(config: dict, equity: float, latest_summary: Optional[dict]) -> float:
    simulator = PropFirmSimulator(config)
    if latest_summary:
        opening_balance = latest_summary.get("opening_balance")
        closing_balance = latest_summary.get("closing_balance")
        peak_eod_balance = latest_summary.get("peak_eod_balance")
        if opening_balance:
            simulator.opening_balance_today = opening_balance
        if closing_balance:
            simulator.balance = closing_balance
        if peak_eod_balance:
            simulator.peak_eod_balance = peak_eod_balance
        elif closing_balance:
            simulator.peak_eod_balance = max(simulator.account_size, closing_balance)
    else:
        simulator.balance = equity
        simulator.peak_eod_balance = max(simulator.account_size, equity)
    simulator.total_pnl = simulator.balance - simulator.account_size
    return simulator.get_mll_floor()


def get_circuit_breaker_context(db: DBManager) -> dict:
    store = get_state_store()
    config = get_prop_config()
    position_sync = get_position_sync()
    position_status = position_sync.refresh_positions()
    broker_snapshot = position_sync.fetch_account_snapshot() or store.get("account_metrics", {})
    daily_summaries = db.get_daily_summaries(limit=1)
    latest_summary = daily_summaries[0] if daily_summaries else None

    equity = broker_snapshot.get("cash_balance") or broker_snapshot.get("equity") or config["account_size"]
    opening_balance = broker_snapshot.get("opening_balance") or (latest_summary.get("opening_balance") if latest_summary else equity)
    daily_pnl = broker_snapshot.get("realized_pnl")
    if daily_pnl is None:
        daily_pnl = latest_summary.get("daily_pnl") if latest_summary else 0.0

    mll_floor = _calculate_mll_floor(config, equity, latest_summary)
    daily_loss_limit = config.get("daily_loss_limit")
    dll_floor = opening_balance - daily_loss_limit if daily_loss_limit else None
    consecutive_losses = _derive_consecutive_losses(db)

    store.update_account_metrics(
        account_id=broker_snapshot.get("account_id") or (latest_summary.get("account_id") if latest_summary else None),
        opening_balance=opening_balance,
        closing_balance=broker_snapshot.get("cash_balance") or (latest_summary.get("closing_balance") if latest_summary else None),
        equity=equity,
        daily_pnl=daily_pnl,
        mll_floor=mll_floor,
        dll_floor=dll_floor,
        consecutive_losses=consecutive_losses,
        buffer_to_mll=equity - mll_floor,
        active_risk_tier=config.get("risk_expansion_model"),
    )

    if broker_snapshot:
        store.set("last_broker_ping", broker_snapshot.get("timestamp", datetime.utcnow().isoformat()))

    return {
        "daily_pnl": daily_pnl,
        "equity": equity,
        "mll_floor": mll_floor,
        "dll_floor": dll_floor,
        "consecutive_losses": consecutive_losses,
        "last_data_timestamp": store.get("last_data_timestamp"),
        "last_broker_ping": store.get("last_broker_ping"),
        "recent_orders": store.get("recent_orders", []),
        "recent_fills": store.get("recent_fills", []),
        "recent_errors": store.get("recent_errors", []),
        "internal_daily_hard_stop": INTERNAL_DAILY_HARD_STOP_USD,
        "mll_proximity_threshold": config.get("max_loss_limit", 2000) * 0.10,
        "max_data_age_seconds": MAX_DATA_AGE_SECONDS,
        "max_broker_ping_age_seconds": MAX_BROKER_PING_AGE_SECONDS,
        "position_status": position_status,
    }


def log_webhook(payload: dict, endpoint: str):
    log_entry = {
        "received_at": datetime.utcnow().isoformat(),
        "endpoint": endpoint,
        "payload": payload,
    }
    log_path = LOG_DIR / f"webhooks_{datetime.utcnow().strftime('%Y%m%d')}.jsonl"
    with open(log_path, "a") as handle:
        handle.write(json.dumps(log_entry) + "\n")


def record_error(message: str, path: str):
    get_state_store().append_recent(
        "recent_errors",
        {"timestamp": datetime.utcnow().isoformat(), "error": message, "path": path},
        max_items=100,
    )


def maybe_emergency_flatten(reason: str):
    breakers = get_circuit_breakers()
    if not breakers.should_flatten():
        return None

    position_sync = get_position_sync()
    status = position_sync.get_position_status()
    if status.get("broker_position") is None:
        return {"status": "not_needed", "reason": reason}

    result = position_sync.emergency_flatten(reason)
    get_state_store().set(
        "last_emergency_action",
        {
            "timestamp": datetime.utcnow().isoformat(),
            "reason": reason,
            "result": result,
        },
    )
    return result


@app.post("/webhook/entry")
async def handle_entry(
    request: Request,
    payload: EntryPayload,
    x_tv_signature: str = Header(default=""),
    circuit_breakers: CircuitBreakers = Depends(get_circuit_breakers),
    db: DBManager = Depends(get_db),
):
    body = await request.body()
    if not verify_signature(body, x_tv_signature):
        raise HTTPException(status_code=401, detail="Invalid signature")

    ensure_allowed_instrument(payload.ticker)
    signal_id = build_signal_id(payload.ticker, payload.action, payload.timestamp, payload.price)
    store = get_state_store()
    if store.has_seen_signal(signal_id):
        return JSONResponse({"status": "ignored", "reason": "Duplicate signal"})

    log_webhook(payload.dict(), "entry")
    context = get_circuit_breaker_context(db)
    allowed, reason = circuit_breakers.check_all(context)
    save_breaker_state()

    if not allowed:
        flatten_result = maybe_emergency_flatten(reason)
        return JSONResponse(
            status_code=403,
            content={
                "status": "blocked",
                "reason": reason,
                "flatten_result": flatten_result,
            },
        )

    position_sync = get_position_sync()
    status = position_sync.refresh_positions()
    if status["broker_position"] is not None:
        return JSONResponse(
            status_code=409,
            content={
                "status": "rejected",
                "reason": "Already in position",
                "current_position": status["broker_position"],
            },
        )

    order_details = {
        "ticker": payload.ticker,
        "base_symbol": normalise_symbol(payload.ticker),
        "action": payload.action.upper(),
        "quantity": payload.quantity,
        "price": float(payload.price) if payload.price else None,
        "setup": payload.setup,
        "stop_price": float(payload.stop_price) if payload.stop_price else None,
        "target_price": float(payload.target_price) if payload.target_price else None,
        "timestamp": payload.timestamp,
        "mode": TRADING_MODE,
    }

    trade_record = {
        "trade_id": f"WEBHOOK_{datetime.utcnow().strftime('%Y%m%d_%H%M%S_%f')}",
        "prop_firm": PROP_FIRM,
        "account_id": store.get("account_metrics", {}).get("account_id") or "runtime",
        "instrument": normalise_symbol(payload.ticker),
        "direction": "BUY" if payload.action.lower() == "buy" else "SELL",
        "entry_time": payload.timestamp,
        "exit_time": None,
        "entry_price": float(payload.price) if payload.price else None,
        "exit_price": None,
        "contracts": payload.quantity,
        "gross_pnl": None,
        "commission": None,
        "net_pnl": None,
        "setup_type": payload.setup,
        "vwap_at_entry": None,
        "vwap_position": None,
        "market_state": None,
        "signal_confidence": None,
        "stop_price": float(payload.stop_price) if payload.stop_price else None,
        "target_price": float(payload.target_price) if payload.target_price else None,
        "r_multiple": None,
        "tradovate_order_id": None,
        "notes": f"Received from TradingView webhook in {TRADING_MODE} mode",
    }

    try:
        db.insert_live_trade(trade_record)
    except Exception as exc:
        record_error(str(exc), "/webhook/entry")

    store.append_recent("recent_orders", datetime.utcnow().isoformat(), max_items=200)
    store.remember_signal(signal_id)

    return JSONResponse(
        {
            "status": "received",
            "order": order_details,
            "message": "Entry signal received and validated",
        }
    )


@app.post("/webhook/exit")
async def handle_exit(
    request: Request,
    payload: ExitPayload,
    x_tv_signature: str = Header(default=""),
    db: DBManager = Depends(get_db),
):
    body = await request.body()
    if not verify_signature(body, x_tv_signature):
        raise HTTPException(status_code=401, detail="Invalid signature")

    base_symbol = ensure_allowed_instrument(payload.ticker)
    log_webhook(payload.dict(), "exit")

    recent_trades = db.get_recent_live_trades(limit=1)
    if not recent_trades or recent_trades[0].get("exit_time") is not None:
        return JSONResponse(status_code=404, content={"status": "error", "reason": "No open position found"})

    open_trade = recent_trades[0]
    entry_price = open_trade.get("entry_price", 0)
    exit_price = float(payload.price) if payload.price else 0
    contracts = open_trade.get("contracts", 1)
    direction = open_trade.get("direction", "BUY")
    point_value = INSTRUMENT_SPECS.get(base_symbol, INSTRUMENT_SPECS["MES"]).get("point_value", 5)
    commission_per_side = INSTRUMENT_SPECS.get(base_symbol, INSTRUMENT_SPECS["MES"]).get("commission_per_side", 1.58)

    if direction == "BUY":
        gross_pnl = (exit_price - entry_price) * contracts * point_value
    else:
        gross_pnl = (entry_price - exit_price) * contracts * point_value

    commission = commission_per_side * 2 * contracts
    net_pnl = gross_pnl - commission

    get_state_store().update_account_metrics(daily_pnl=get_state_store().get("account_metrics", {}).get("daily_pnl", 0.0) + net_pnl)

    return JSONResponse(
        {
            "status": "received",
            "exit": {
                "ticker": payload.ticker,
                "action": payload.action,
                "price": exit_price,
                "quantity": contracts,
                "reason": payload.reason,
            },
            "pnl": {
                "gross": gross_pnl,
                "commission": commission,
                "net": net_pnl,
            },
        }
    )


@app.post("/webhook/order-update")
async def handle_order_update(
    request: Request,
    payload: OrderUpdatePayload,
    x_tv_signature: str = Header(default=""),
):
    body = await request.body()
    if not verify_signature(body, x_tv_signature):
        raise HTTPException(status_code=401, detail="Invalid signature")

    log_webhook(payload.dict(), "order-update")
    store = get_state_store()

    if payload.status.lower() == "filled":
        store.append_recent(
            "recent_fills",
            {
                "timestamp": datetime.utcnow().isoformat(),
                "order_id": payload.order_id,
                "filled_qty": payload.filled_qty,
                "avg_price": payload.avg_fill_price,
                "slippage_ticks": 0,
            },
            max_items=100,
        )

    store.set("last_broker_ping", datetime.utcnow().isoformat())
    return JSONResponse({"status": "received"})


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat(),
        "server": "webhook_server_enhanced",
        "mode": TRADING_MODE,
        "tradovate_use_demo": TRADOVATE_USE_DEMO,
    }


@app.get("/status")
async def status(
    db: DBManager = Depends(get_db),
    circuit_breakers: CircuitBreakers = Depends(get_circuit_breakers),
):
    try:
        position_sync = get_position_sync()
        position_status = position_sync.refresh_positions()
        context = get_circuit_breaker_context(db)
        breaker_status = circuit_breakers.get_status()
        save_breaker_state()

        return {
            "timestamp": datetime.utcnow().isoformat(),
            "mode": TRADING_MODE,
            "prop_firm": PROP_FIRM,
            "position": position_status,
            "account_metrics": get_state_store().get("account_metrics", {}),
            "circuit_breakers": breaker_status,
            "recent_trades": db.get_recent_live_trades(limit=10),
            "daily_summaries": db.get_daily_summaries(limit=5),
            "system_status": {
                "trading_allowed": circuit_breakers.get_open_breakers() == [],
                "open_breakers": circuit_breakers.get_open_breakers(),
                "flatten_recommended": circuit_breakers.should_flatten(),
                "context_snapshot": context,
            },
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error fetching status: {str(exc)}")


@app.get("/position")
async def get_position():
    try:
        position_sync = get_position_sync()
        return position_sync.refresh_positions()
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Error fetching position: {str(exc)}")


@app.post("/admin/reset-breaker")
async def reset_breaker(breaker_name: str, secret: str = Header(default="")):
    if not ADMIN_SECRET or secret != ADMIN_SECRET:
        raise HTTPException(status_code=401, detail="Invalid admin secret")

    circuit_breakers = get_circuit_breakers()
    circuit_breakers.manual_reset(breaker_name)
    save_breaker_state()
    return {"status": "reset", "breaker": breaker_name, "timestamp": datetime.utcnow().isoformat()}


@app.post("/admin/emergency-flatten")
async def emergency_flatten(reason: str, secret: str = Header(default="")):
    if not ADMIN_SECRET or secret != ADMIN_SECRET:
        raise HTTPException(status_code=401, detail="Invalid admin secret")

    position_sync = get_position_sync()
    result = position_sync.emergency_flatten(reason)
    get_state_store().set(
        "last_emergency_action",
        {"timestamp": datetime.utcnow().isoformat(), "reason": reason, "result": result},
    )
    return {
        "status": "emergency_flatten",
        "reason": reason,
        "result": result,
        "timestamp": datetime.utcnow().isoformat(),
    }


@app.on_event("startup")
async def startup_event():
    print("[WebhookServer] Starting up...")
    store = get_state_store()

    if TRADING_MODE == "TOPSTEP_LIVE" and not TV_WEBHOOK_SECRET:
        raise RuntimeError("TV_WEBHOOK_SECRET must be configured in TOPSTEP_LIVE mode")

    try:
        position_sync = get_position_sync()
        sync_result = position_sync.sync_on_startup()
        store.set("position_sync", sync_result)
        print(f"[WebhookServer] Position sync result: {sync_result['status']}")
    except Exception as exc:
        record_error(str(exc), "startup.position_sync")
        print(f"[WebhookServer] WARNING: Position sync failed: {exc}")

    try:
        circuit_breakers = get_circuit_breakers()
        save_breaker_state()
        print(f"[WebhookServer] Circuit breakers initialized: {len(circuit_breakers.breakers)} breakers")
    except Exception as exc:
        record_error(str(exc), "startup.circuit_breakers")
        print(f"[WebhookServer] WARNING: Circuit breakers init failed: {exc}")

    print("[WebhookServer] Ready for connections")


@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    record_error(str(exc), request.url.path)
    return JSONResponse(status_code=500, content={"status": "error", "message": str(exc)})
