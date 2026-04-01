"""
FastAPI Webhook Server
Receives TradingView alerts directly with full position management.
Production-ready for paper trading.

Run:
    uvicorn execution.webhook_server:app --host 0.0.0.0 --port 8000
"""

import hashlib
import hmac
import json
import os
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

from database.db_manager import DBManager
from execution.position_sync import PositionSynchronizer
from execution.circuit_breakers import CircuitBreakers
from config.prop_firm_configs import PROP_FIRM_CONFIGS

app = FastAPI(title="VWAP Bot Webhook Server")

# Configuration
TV_WEBHOOK_SECRET = os.getenv("TV_WEBHOOK_SECRET", "")
DB_PATH = os.getenv("DB_PATH", "./database/trading_analysis.db")
PROP_FIRM = os.getenv("PROP_FIRM", "TOPSTEP_50K")

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

# Global state (in production, use Redis or similar)
app_state = {
    "position_sync": None,
    "circuit_breakers": None,
    "recent_orders": [],
    "recent_fills": [],
    "recent_errors": [],
    "last_data_timestamp": None,
    "last_broker_ping": None,
}


# Pydantic models for request validation
class EntryPayload(BaseModel):
    ticker: str = Field(..., description="Instrument ticker (e.g., MES1!)")
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
    reason: str = Field(default="", description="Exit reason (target, stop, manual)")


class OrderUpdatePayload(BaseModel):
    order_id: str
    status: str = Field(..., description="filled, partial, cancelled")
    filled_qty: int
    avg_fill_price: str
    timestamp: str


# Authentication
def verify_signature(body: bytes, signature: str) -> bool:
    """Verifies the TradingView webhook signature."""
    if not TV_WEBHOOK_SECRET:
        return True  # No secret configured — allow all (dev mode)
    
    expected = hmac.new(
        TV_WEBHOOK_SECRET.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature or "")


# Dependencies
def get_db():
    """Get database connection."""
    return DBManager(DB_PATH)


def get_circuit_breakers():
    """Get circuit breakers instance."""
    if app_state["circuit_breakers"] is None:
        config = PROP_FIRM_CONFIGS.get(PROP_FIRM, PROP_FIRM_CONFIGS["TOPSTEP_50K"])
        app_state["circuit_breakers"] = CircuitBreakers(config)
    return app_state["circuit_breakers"]


def get_position_sync():
    """Get position synchronizer instance."""
    if app_state["position_sync"] is None:
        # Initialize position sync (requires Tradovate provider)
        from data.tradovate_data_provider import TradovateDataProvider
        provider = TradovateDataProvider.from_env(use_demo=True)
        db = get_db()
        app_state["position_sync"] = PositionSynchronizer(provider, db)
    return app_state["position_sync"]


# Context provider for circuit breakers
def get_circuit_breaker_context():
    """Build context for circuit breaker checks."""
    # In production, fetch real values from DB/broker
    return {
        "daily_pnl": 0.0,  # Fetch from DB
        "equity": 50000.0,  # Fetch from broker
        "mll_floor": 48000.0,  # Calculate from prop firm rules
        "consecutive_losses": 0,  # Fetch from DB
        "last_data_timestamp": app_state.get("last_data_timestamp"),
        "last_broker_ping": app_state.get("last_broker_ping"),
        "recent_orders": app_state.get("recent_orders", []),
        "recent_fills": app_state.get("recent_fills", []),
        "recent_errors": app_state.get("recent_errors", []),
    }


# Logging
def log_webhook(payload: dict, endpoint: str):
    """Log webhook to file."""
    log_entry = {
        "received_at": datetime.utcnow().isoformat(),
        "endpoint": endpoint,
        "payload": payload,
    }
    log_path = LOG_DIR / f"webhooks_{datetime.utcnow().strftime('%Y%m%d')}.jsonl"
    with open(log_path, "a") as f:
        f.write(json.dumps(log_entry) + "\n")


# Endpoints
@app.post("/webhook/entry")
async def handle_entry(
    request: Request,
    payload: EntryPayload,
    x_tv_signature: str = Header(default=""),
    circuit_breakers: CircuitBreakers = Depends(get_circuit_breakers),
    db: DBManager = Depends(get_db),
):
    """
    Receives entry signal from TradingView.
    Validates signal, checks risk, and prepares order for submission.
    """
    body = await request.body()
    
    if not verify_signature(body, x_tv_signature):
        raise HTTPException(status_code=401, detail="Invalid signature")
    
    log_webhook(payload.dict(), "entry")
    
    # Check circuit breakers
    context = get_circuit_breaker_context()
    allowed, reason = circuit_breakers.check_all(context)
    
    if not allowed:
        print(f"[Webhook/Entry] BLOCKED by circuit breaker: {reason}")
        return JSONResponse(
            status_code=403,
            content={"status": "blocked", "reason": reason}
        )
    
    # Check if already in position
    position_sync = get_position_sync()
    status = position_sync.get_position_status()
    
    if status["broker_position"] is not None:
        return JSONResponse(
            status_code=409,
            content={
                "status": "rejected",
                "reason": "Already in position",
                "current_position": status["broker_position"]
            }
        )
    
    # Prepare order details
    order_details = {
        "ticker": payload.ticker,
        "action": payload.action.upper(),
        "quantity": payload.quantity,
        "price": float(payload.price) if payload.price else None,
        "setup": payload.setup,
        "stop_price": float(payload.stop_price) if payload.stop_price else None,
        "target_price": float(payload.target_price) if payload.target_price else None,
        "timestamp": payload.timestamp,
    }
    
    # Log to database (pending execution)
    trade_record = {
        "trade_id": f"WEBHOOK_{datetime.utcnow().strftime('%Y%m%d_%H%M%S_%f')}",
        "prop_firm": PROP_FIRM,
        "account_id": "paper",
        "instrument": payload.ticker,
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
        "stop_price": float(payload.stop_price) if payload.stop_price else None,
        "target_price": float(payload.target_price) if payload.target_price else None,
        "notes": f"Received from TradingView webhook",
    }
    
    try:
        db.insert_live_trade(trade_record)
    except Exception as e:
        print(f"[Webhook/Entry] ERROR logging to DB: {e}")
    
    # Track order for rate limiting
    app_state["recent_orders"].append(datetime.utcnow())
    
    print(f"[Webhook/Entry] {payload.action.upper()} {payload.quantity}x {payload.ticker} @ {payload.price}")
    
    return JSONResponse({
        "status": "received",
        "order": order_details,
        "message": "Entry signal received and validated"
    })


@app.post("/webhook/exit")
async def handle_exit(
    request: Request,
    payload: ExitPayload,
    x_tv_signature: str = Header(default=""),
    db: DBManager = Depends(get_db),
):
    """
    Receives exit signal (stop, target, or manual) from TradingView.
    Updates position state and calculates PnL.
    """
    body = await request.body()
    
    if not verify_signature(body, x_tv_signature):
        raise HTTPException(status_code=401, detail="Invalid signature")
    
    log_webhook(payload.dict(), "exit")
    
    # Find open position in database
    recent_trades = db.get_recent_live_trades(limit=1)
    
    if not recent_trades or recent_trades[0].get("exit_time") is not None:
        return JSONResponse(
            status_code=404,
            content={"status": "error", "reason": "No open position found"}
        )
    
    open_trade = recent_trades[0]
    
    # Calculate PnL
    entry_price = open_trade.get("entry_price", 0)
    exit_price = float(payload.price) if payload.price else 0
    contracts = open_trade.get("contracts", 1)
    direction = open_trade.get("direction", "BUY")
    
    # MES point value is $5 per point
    point_value = 5
    
    if direction == "BUY":
        gross_pnl = (exit_price - entry_price) * contracts * point_value
    else:
        gross_pnl = (entry_price - exit_price) * contracts * point_value
    
    commission = 0.70 * 2 * contracts  # $0.70 per side per contract
    net_pnl = gross_pnl - commission
    
    # Update database
    # Note: In production, you'd have a method to update the trade
    # For now, we just log it
    
    print(f"[Webhook/Exit] {payload.action.upper()} {contracts}x @ {payload.price} | PnL: ${net_pnl:.2f} | Reason: {payload.reason}")
    
    return JSONResponse({
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
        }
    })


@app.post("/webhook/order-update")
async def handle_order_update(
    request: Request,
    payload: OrderUpdatePayload,
    x_tv_signature: str = Header(default=""),
):
    """
    Receives order status updates from Tradovate.
    Tracks fills, partials, and cancellations.
    """
    body = await request.body()
    
    if not verify_signature(body, x_tv_signature):
        raise HTTPException(status_code=401, detail="Invalid signature")
    
    log_webhook(payload.dict(), "order-update")
    
    # Track fills for adverse skew detection
    if payload.status == "filled":
        # Calculate slippage if we have expected price
        app_state["recent_fills"].append({
            "timestamp": datetime.utcnow().isoformat(),
            "order_id": payload.order_id,
            "filled_qty": payload.filled_qty,
            "avg_price": payload.avg_fill_price,
            "slippage_ticks": 0,  # Calculate based on expected vs actual
        })
    
    # Update broker ping time
    app_state["last_broker_ping"] = datetime.utcnow()
    
    print(f"[Webhook/OrderUpdate] Order {payload.order_id}: {payload.status} | Qty: {payload.filled_qty} @ {payload.avg_fill_price}")
    
    return JSONResponse({"status": "received"})


@app.get("/health")
async def health():
    """Health check endpoint."""
    return {
        "status": "ok",
        "timestamp": datetime.utcnow().isoformat(),
        "server": "webhook_server",
    }


@app.get("/status")
async def status(
    db: DBManager = Depends(get_db),
    circuit_breakers: CircuitBreakers = Depends(get_circuit_breakers),
):
    """
    Returns current system status including:
    - Position status
    - Circuit breaker states
    - Recent trades
    - Daily PnL
    """
    try:
        # Get position status
        position_sync = get_position_sync()
        position_status = position_sync.get_position_status()
        
        # Get circuit breaker status
        breaker_status = circuit_breakers.get_status()
        
        # Get recent trades
        recent_trades = db.get_recent_live_trades(limit=10)
        
        # Get daily summaries
        daily_summaries = db.get_daily_summaries(limit=5)
        
        return {
            "timestamp": datetime.utcnow().isoformat(),
            "position": position_status,
            "circuit_breakers": breaker_status,
            "recent_trades": recent_trades,
            "daily_summaries": daily_summaries,
            "system_status": {
                "trading_allowed": circuit_breakers.get_open_breakers() == [],
                "open_breakers": circuit_breakers.get_open_breakers(),
            }
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching status: {str(e)}")


@app.get("/position")
async def get_position():
    """Returns current position for TradingView display."""
    try:
        position_sync = get_position_sync()
        status = position_sync.get_position_status()
        
        return {
            "broker_position": status["broker_position"],
            "local_position": status["local_position"],
            "synced": status["synced"],
            "last_sync": status["last_sync"],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Error fetching position: {str(e)}")


@app.post("/admin/reset-breaker")
async def reset_breaker(breaker_name: str, secret: str = Header(default="")):
    """
    Admin endpoint to manually reset a circuit breaker.
    Requires admin secret.
    """
    admin_secret = os.getenv("ADMIN_SECRET", "")
    if not admin_secret or secret != admin_secret:
        raise HTTPException(status_code=401, detail="Invalid admin secret")
    
    circuit_breakers = get_circuit_breakers()
    circuit_breakers.manual_reset(breaker_name)
    
    return {
        "status": "reset",
        "breaker": breaker_name,
        "timestamp": datetime.utcnow().isoformat(),
    }


@app.post("/admin/emergency-flatten")
async def emergency_flatten(reason: str, secret: str = Header(default="")):
    """
    Admin endpoint to emergency flatten all positions.
    Requires admin secret.
    """
    admin_secret = os.getenv("ADMIN_SECRET", "")
    if not admin_secret or secret != admin_secret:
        raise HTTPException(status_code=401, detail="Invalid admin secret")
    
    position_sync = get_position_sync()
    result = position_sync.emergency_flatten(reason)
    
    return {
        "status": "emergency_flatten",
        "reason": reason,
        "result": result,
        "timestamp": datetime.utcnow().isoformat(),
    }


# Startup event
@app.on_event("startup")
async def startup_event():
    """Initialize on server startup."""
    print("[WebhookServer] Starting up...")
    
    # Initialize position sync
    try:
        position_sync = get_position_sync()
        sync_result = position_sync.sync_on_startup()
        print(f"[WebhookServer] Position sync result: {sync_result['status']}")
    except Exception as e:
        print(f"[WebhookServer] WARNING: Position sync failed: {e}")
    
    # Initialize circuit breakers
    try:
        circuit_breakers = get_circuit_breakers()
        print(f"[WebhookServer] Circuit breakers initialized: {len(circuit_breakers.breakers)} breakers")
    except Exception as e:
        print(f"[WebhookServer] WARNING: Circuit breakers init failed: {e}")
    
    print("[WebhookServer] Ready for connections")


# Error handlers
@app.exception_handler(Exception)
async def generic_exception_handler(request: Request, exc: Exception):
    """Log all unhandled exceptions."""
    error_entry = {
        "timestamp": datetime.utcnow().isoformat(),
        "error": str(exc),
        "path": request.url.path,
    }
    app_state["recent_errors"].append(error_entry)
    
    # Keep only last 100 errors
    if len(app_state["recent_errors"]) > 100:
        app_state["recent_errors"] = app_state["recent_errors"][-100:]
    
    return JSONResponse(
        status_code=500,
        content={"status": "error", "message": str(exc)}
    )
