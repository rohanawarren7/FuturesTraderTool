"""
FastAPI Webhook Server
Receives TradingView alerts directly (Phase 2 — bypasses TradersPost).
For the MVP, TradersPost handles webhook receipt. This server is for
the direct-execution upgrade path.

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

sys.path.insert(0, str(Path(__file__).parent.parent))

from fastapi import FastAPI, Request, HTTPException, Header
from fastapi.responses import JSONResponse
from dotenv import load_dotenv

load_dotenv()

from database.db_manager import DBManager

app = FastAPI(title="VWAP Bot Webhook Server")

TV_WEBHOOK_SECRET = os.getenv("TV_WEBHOOK_SECRET", "")
DB_PATH = os.getenv("DB_PATH", "./database/trading_analysis.db")

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)


def verify_signature(body: bytes, signature: str) -> bool:
    """Verifies the TradingView webhook signature."""
    if not TV_WEBHOOK_SECRET:
        return True  # No secret configured — allow all (dev mode)
    expected = hmac.new(
        TV_WEBHOOK_SECRET.encode(), body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, signature or "")


@app.post("/webhook/tradingview")
async def tradingview_webhook(
    request: Request,
    x_tv_signature: str = Header(default=""),
):
    """
    Receives a TradingView alert JSON payload.
    Expected format:
    {
      "ticker": "MES1!",
      "action": "buy" | "sell",
      "orderType": "market",
      "quantity": 1,
      "price": "5840.25",
      "timestamp": "2025-03-15T10:30:00Z",
      "setup": "mean_reversion"
    }
    """
    body = await request.body()

    if not verify_signature(body, x_tv_signature):
        raise HTTPException(status_code=401, detail="Invalid signature")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Log every incoming alert
    log_entry = {
        "received_at": datetime.utcnow().isoformat(),
        "payload":     payload,
    }
    log_path = LOG_DIR / f"webhooks_{datetime.utcnow().strftime('%Y%m%d')}.jsonl"
    with open(log_path, "a") as f:
        f.write(json.dumps(log_entry) + "\n")

    action = payload.get("action", "").lower()
    ticker = payload.get("ticker", "")
    quantity = int(payload.get("quantity", 1))
    price = float(payload.get("price", 0))
    setup = payload.get("setup", "")

    print(f"[Webhook] {datetime.utcnow()} | {action.upper()} {quantity}x {ticker} "
          f"@ {price} | setup={setup}")

    # In Phase 2, this would route to a direct broker API.
    # For the MVP, TradersPost handles execution — this server is supplementary.
    return JSONResponse({"status": "received", "action": action, "ticker": ticker})


@app.get("/health")
async def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


@app.get("/status")
async def status():
    """Returns current account and strategy status from DB."""
    db = DBManager(DB_PATH)
    summaries = db.get_daily_summaries(limit=5)
    recent_trades = db.get_recent_live_trades(limit=10)
    return {
        "recent_daily_summaries": summaries,
        "recent_trades":          recent_trades,
    }
