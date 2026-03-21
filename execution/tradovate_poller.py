"""
Polls the Tradovate account every N seconds for new filled orders.
Writes fills to the live_trades and daily_account_summary tables.

Run this as a background process alongside the main bot:
    nohup python -m execution.tradovate_poller >> logs/poller.log 2>&1 &
"""

import time
import os
import requests
import json
from datetime import datetime, date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))

from database.db_manager import DBManager
from data.tradovate_data_provider import TradovateDataProvider


class TradovatePoller:
    """
    Polls Tradovate REST API for filled orders and account cash balance.
    Deduplicates using a local set of seen order IDs (persisted across restarts
    via the live_trades table's UNIQUE constraint on trade_id).
    """

    def __init__(
        self,
        provider: TradovateDataProvider,
        db: DBManager,
        poll_interval: int = 30,
        prop_firm: str = "TOPSTEP_50K",
    ):
        self.provider = provider
        self.db = db
        self.poll_interval = poll_interval
        self.prop_firm = prop_firm
        self.seen_order_ids: set[int] = set()

    # ------------------------------------------------------------------
    # Polling
    # ------------------------------------------------------------------

    def poll_once(self):
        """Fetches orders and account summary. Writes new fills to DB."""
        self._poll_orders()
        self._poll_account_summary()

    def _poll_orders(self):
        try:
            resp = requests.get(
                f"{self.provider.base_url}/order/list",
                headers=self.provider._headers(),
                timeout=10,
            )
            resp.raise_for_status()
            orders = resp.json()
        except Exception as e:
            print(f"[Poller] Order fetch error: {e}")
            return

        for order in orders:
            order_id = order.get("id")
            if order_id in self.seen_order_ids:
                continue

            status = order.get("ordStatus", "")
            if status == "Filled":
                self.seen_order_ids.add(order_id)
                try:
                    row_id = self.db.insert_live_trade_from_order(order)
                    print(f"[Poller] New fill recorded: order {order_id} → row {row_id}")
                except Exception as e:
                    print(f"[Poller] DB insert error for order {order_id}: {e}")

    def _poll_account_summary(self):
        """Fetches current cash balance and writes a daily summary row."""
        try:
            resp = requests.get(
                f"{self.provider.base_url}/cashbalance/getcashbalancesnapshot",
                headers=self.provider._headers(),
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            print(f"[Poller] Account fetch error: {e}")
            return

        balance = data.get("cashBalance")
        if balance is None:
            return

        today = str(date.today())
        try:
            self.db.upsert_daily_summary({
                "date":                      today,
                "prop_firm":                 self.prop_firm,
                "account_id":                str(data.get("accountId", "")),
                "opening_balance":           data.get("initialBalance", balance),
                "closing_balance":           balance,
                "daily_pnl":                 data.get("realizedPnl", 0),
                "total_trades":              0,  # updated by trade log
                "winning_trades":            0,
                "mll_floor":                 None,
                "peak_eod_balance":          None,
                "winning_days_since_payout": 0,
                "payout_taken":              0,
                "status":                    "COMBINE",
            })
        except Exception as e:
            print(f"[Poller] Daily summary error: {e}")

    def run(self):
        print(f"[Poller] Starting — polling every {self.poll_interval}s")
        while True:
            try:
                self.poll_once()
            except Exception as e:
                print(f"[Poller] Unexpected error: {e}")
            time.sleep(self.poll_interval)


# ------------------------------------------------------------------
# Entrypoint
# ------------------------------------------------------------------

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    Path("logs").mkdir(exist_ok=True)

    provider = TradovateDataProvider.from_env(
        use_demo=os.getenv("TRADOVATE_USE_DEMO", "true").lower() == "true"
    )
    db = DBManager(os.getenv("DB_PATH", "./database/trading_analysis.db"))
    prop_firm = os.getenv("PROP_FIRM", "TOPSTEP_50K")

    poller = TradovatePoller(provider, db, poll_interval=30, prop_firm=prop_firm)
    poller.run()
