"""
Performance Dashboard (Streamlit)
Real-time monitoring of account status, trades, and strategy metrics.

Run:
    streamlit run dashboard/performance_dashboard.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import os
import time
import pandas as pd
import numpy as np
from datetime import date, datetime
from dotenv import load_dotenv
load_dotenv()

import streamlit as st

from database.db_manager import DBManager
from config.prop_firm_configs import PROP_FIRM_CONFIGS

st.set_page_config(
    page_title="VWAP Bot Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="collapsed",
)

DB_PATH = os.getenv("DB_PATH", "./database/trading_analysis.db")
PROP_FIRM = os.getenv("PROP_FIRM", "TOPSTEP_50K")
HALT_FLAG = Path("HALT_TRADING.flag")


@st.cache_resource
def get_db():
    return DBManager(DB_PATH)


def get_status_color(equity: float, mll_floor: float, account_size: float) -> str:
    distance = equity - mll_floor
    mll = account_size - mll_floor
    pct = distance / mll if mll > 0 else 1.0
    if pct > 0.30:
        return "🟢"
    if pct > 0.15:
        return "🟡"
    return "🔴"


def main():
    db = get_db()
    config = PROP_FIRM_CONFIGS.get(PROP_FIRM, {})
    account_size = config.get("account_size", 50_000)
    mll = config.get("max_loss_limit", 2_000)

    st.title("📈 VWAP Bot — Live Dashboard")
    st.caption(f"Prop Firm: **{PROP_FIRM}** | Account: **${account_size:,}** | "
               f"MLL: **${mll:,}** | Refreshes every 30s")

    if HALT_FLAG.exists():
        st.error(f"⛔ HALT FLAG ACTIVE: {HALT_FLAG.read_text().strip()}")

    # ------------------------------------------------------------------
    # Panel 1: Live Account Status
    # ------------------------------------------------------------------
    summaries = db.get_daily_summaries(limit=1)
    today_summary = summaries[0] if summaries else {}

    closing = today_summary.get("closing_balance", account_size)
    daily_pnl = today_summary.get("daily_pnl", 0) or 0
    mll_floor = today_summary.get("mll_floor") or (account_size - mll)
    winning_days = today_summary.get("winning_days_since_payout", 0)
    status = today_summary.get("status", "COMBINE")

    distance_to_mll = closing - mll_floor
    color = get_status_color(closing, mll_floor, account_size)

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Balance", f"${closing:,.0f}",
                delta=f"${daily_pnl:+,.0f} today")
    col2.metric("Daily PnL", f"${daily_pnl:+,.0f}",
                delta=f"Budget: ${mll * 0.40:,.0f}")
    col3.metric("MLL Floor", f"${mll_floor:,.0f}",
                delta=f"{color} ${distance_to_mll:,.0f} away")
    col4.metric("Winning Days", f"{winning_days} / 5",
                delta="Payout eligible" if winning_days >= 5 else f"{5-winning_days} more needed")
    col5.metric("Status", status)

    st.divider()

    # ------------------------------------------------------------------
    # Panel 2: Today's Trades
    # ------------------------------------------------------------------
    st.subheader("Today's Trades")
    trades = db.get_recent_live_trades(limit=50)
    today_str = str(date.today())
    today_trades = [t for t in trades if (t.get("entry_time") or "")[:10] == today_str]

    if today_trades:
        df_today = pd.DataFrame(today_trades)
        display_cols = ["entry_time", "direction", "setup_type", "contracts",
                        "entry_price", "exit_price", "net_pnl", "r_multiple"]
        available = [c for c in display_cols if c in df_today.columns]
        st.dataframe(
            df_today[available].style.applymap(
                lambda v: "color: green" if isinstance(v, (int, float)) and v > 0
                          else ("color: red" if isinstance(v, (int, float)) and v < 0 else ""),
                subset=[c for c in ["net_pnl"] if c in available]
            ),
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.info("No trades today yet.")

    st.divider()

    # ------------------------------------------------------------------
    # Panel 3: Cumulative Stats (last 20 trades)
    # ------------------------------------------------------------------
    st.subheader("Cumulative Performance (Last 20 Trades)")
    last_20 = trades[:20]

    if last_20:
        pnls = [t.get("net_pnl", 0) or 0 for t in last_20]
        wins = [p for p in pnls if p > 0]
        losses = [p for p in pnls if p <= 0]
        win_rate = len(wins) / len(pnls) if pnls else 0
        pf = abs(sum(wins) / sum(losses)) if losses and sum(losses) != 0 else float("inf")

        # Combine progress
        profit_so_far = closing - account_size
        profit_target = config.get("profit_target", 3_000)
        combine_pct = min(100, max(0, profit_so_far / profit_target * 100))

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Win Rate", f"{win_rate*100:.1f}%",
                  delta="✓" if win_rate >= 0.52 else "Below threshold")
        c2.metric("Profit Factor", f"{pf:.2f}" if pf != float("inf") else "∞",
                  delta="✓" if pf >= 1.3 else "Below threshold")
        c3.metric("Net PnL (20)", f"${sum(pnls):+,.0f}")
        c4.metric("Combine Progress",
                  f"${profit_so_far:+,.0f} / ${profit_target:,}",
                  delta=f"{combine_pct:.0f}%")

        st.progress(combine_pct / 100, text=f"Combine: {combine_pct:.0f}% complete")
    else:
        st.info("No trades in database yet.")

    st.divider()

    # ------------------------------------------------------------------
    # Panel 4: Daily History
    # ------------------------------------------------------------------
    st.subheader("Daily Account History (Last 30 Days)")
    all_summaries = db.get_daily_summaries(limit=30)
    if all_summaries:
        df_hist = pd.DataFrame(all_summaries)
        df_hist = df_hist[["date", "daily_pnl", "closing_balance", "mll_floor",
                            "winning_days_since_payout", "status"]].sort_values("date")
        st.dataframe(df_hist, use_container_width=True, hide_index=True)
    else:
        st.info("No daily summaries yet.")

    # Auto-refresh
    st.caption(f"Last updated: {datetime.now().strftime('%H:%M:%S')}")
    time.sleep(30)
    st.rerun()


if __name__ == "__main__":
    main()
