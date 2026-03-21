#!/usr/bin/env python3
"""
Weekly Self-Improvement Loop
Run every Sunday to re-optimise strategy params based on recent live trades.

Usage:
    python scripts/weekly_improvement.py

Schedule via crontab (WSL2):
    crontab -e
    # Add: 0 18 * * 0 cd /path/to/project && python scripts/weekly_improvement.py >> logs/weekly.log 2>&1
"""

import json
import os
import sys
from pathlib import Path
from datetime import datetime, date, timedelta

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

import pandas as pd
import numpy as np

from database.db_manager import DBManager
from backtesting.backtest_runner import BacktestRunner
from backtesting.monte_carlo import MonteCarloSimulator
from optimisation.walk_forward import WalkForwardOptimiser
from data.tradovate_data_provider import TradovateDataProvider, get_current_front_month

RESULTS_DIR = Path("backtesting/results")
RESULTS_DIR.mkdir(exist_ok=True)
LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)

HALT_FLAG_PATH = Path("HALT_TRADING.flag")


def compute_live_stats(trades: list[dict]) -> dict:
    if not trades:
        return {}
    pnls = [t.get("net_pnl", 0) for t in trades if t.get("net_pnl") is not None]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]
    gross_profit = sum(wins)
    gross_loss = abs(sum(losses))
    win_rate = len(wins) / len(pnls) if pnls else 0
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Sharpe (daily)
    by_day = {}
    for t in trades:
        day = (t.get("entry_time") or "")[:10]
        if day:
            by_day[day] = by_day.get(day, 0) + (t.get("net_pnl") or 0)
    daily_pnls = list(by_day.values())
    sharpe = 0.0
    if len(daily_pnls) >= 2:
        arr = np.array(daily_pnls)
        sharpe = float(arr.mean() / arr.std() * np.sqrt(252)) if arr.std() > 0 else 0.0

    return {
        "total_trades":   len(pnls),
        "win_rate":        round(win_rate, 3),
        "profit_factor":   round(profit_factor, 3),
        "sharpe_ratio":    round(sharpe, 3),
        "total_net_pnl":   round(sum(pnls), 2),
        "avg_r_multiple":  round(
            np.mean([t.get("r_multiple") for t in trades
                     if t.get("r_multiple") is not None] or [0]), 3
        ),
    }


def check_halt_conditions(stats: dict) -> tuple[bool, str]:
    """Returns (should_halt, reason)."""
    if stats.get("total_trades", 0) >= 20:
        if stats.get("win_rate", 1) < 0.48:
            return True, f"Win rate {stats['win_rate']*100:.1f}% < 48% over last 20 trades"
        if stats.get("profit_factor", 1) < 1.0:
            return True, f"Profit factor {stats['profit_factor']:.2f} < 1.0"
    return False, ""


def main():
    db_path = os.getenv("DB_PATH", "./database/trading_analysis.db")
    prop_firm = os.getenv("PROP_FIRM", "TOPSTEP_50K")
    db = DBManager(db_path)

    today = date.today()
    week_ago = today - timedelta(days=7)
    print(f"\n{'='*60}")
    print(f"WEEKLY IMPROVEMENT — {today}")
    print(f"{'='*60}")

    # 1. Analyse last 7 days of live trades
    recent_trades = db.get_recent_live_trades(limit=200)
    last_week = [t for t in recent_trades
                 if t.get("entry_time", "") >= str(week_ago)]

    stats = compute_live_stats(last_week)
    print(f"\nLAST 7 DAYS: {stats.get('total_trades', 0)} trades")
    if stats:
        print(f"  Win rate:      {stats['win_rate']*100:.1f}%")
        print(f"  Profit factor: {stats['profit_factor']:.2f}")
        print(f"  Sharpe:        {stats['sharpe_ratio']:.2f}")
        print(f"  Net PnL:       ${stats['total_net_pnl']:,.0f}")

    # 2. Check halt conditions
    should_halt, halt_reason = check_halt_conditions(stats)
    if should_halt:
        print(f"\n[!] HALT FLAG SET: {halt_reason}")
        HALT_FLAG_PATH.write_text(
            f"HALT set {today}: {halt_reason}\n"
        )
        print(f"    Created {HALT_FLAG_PATH}. Bot will not trade until flag is removed.")
    elif HALT_FLAG_PATH.exists():
        print(f"[OK] Performance recovered — removing halt flag.")
        HALT_FLAG_PATH.unlink()

    # 3. Monte Carlo on last 30 trades (or all recent)
    recent_30 = db.get_recent_live_trades(limit=30)
    if len(recent_30) >= 10:
        pnls = [t.get("net_pnl", 0) for t in recent_30
                if t.get("net_pnl") is not None]
        mc = MonteCarloSimulator(prop_firm, n_simulations=5_000)
        mc_result = mc.run(pnls, seed=42)
        print(f"\nMONTE CARLO (last {len(pnls)} trades):")
        print(f"  Ruin probability: {mc_result['ruin_pct']:.1f}%")
        print(f"  Pass rate:        {mc_result['combine_pass_pct']:.1f}%")
        print(f"  Assessment:       {mc_result['assessment']}")

    # 4. Re-run WFO on last 3 months of market data
    print(f"\nFetching 3 months of market data for WFO...")
    try:
        provider = TradovateDataProvider.from_env()
        symbol = get_current_front_month("MES")
        df = provider.get_history(symbol, bar_minutes=5, lookback_days=90)

        wfo = WalkForwardOptimiser(prop_firm, "MES")
        wfo.IS_MONTHS = 2
        wfo.OOS_MONTHS = 1
        wfo.N_TRIALS = 50
        wfo_result = wfo.run_full_wfo(df)

        print(f"\nWFO RESULTS:")
        print(f"  WFE Score:   {wfo_result.get('wfe_score', 0):.3f}")
        print(f"  Assessment:  {wfo_result.get('assessment', 'N/A')}")

        stored = db.get_latest_strategy_params()
        stored_wfe = stored.get("wfe_score", 0) if stored else 0
        new_wfe = wfo_result.get("wfe_score", 0)

        if new_wfe > stored_wfe and new_wfe >= 0.40:
            db.save_strategy_params({
                "version":           f"weekly_{today}",
                "regime":            "BALANCED",
                "sd_mult_entry":     wfo_result["best_params"].get("sd_mult_entry", 1.0),
                "sd_mult_stop":      wfo_result["best_params"].get("sd_mult_stop", 1.5),
                "rr_ratio":          wfo_result["best_params"].get("rr_ratio", 2.0),
                "delta_threshold":   wfo_result["best_params"].get("delta_threshold", 300),
                "volume_threshold":  wfo_result["best_params"].get("volume_threshold", 1.8),
                "session_start":     "09:45",
                "session_end":       "14:00",
                "max_trades_per_day": 4,
                "valid_from":        str(today),
                "valid_to":          None,
                "wfe_score":         new_wfe,
            })
            print(f"  [OK] Improved params saved (WFE {stored_wfe:.3f} → {new_wfe:.3f})")
        else:
            print(f"  [OK] Keeping existing params (stored WFE {stored_wfe:.3f} >= new {new_wfe:.3f})")

    except Exception as e:
        print(f"  [WARN] WFO failed: {e}. Keeping existing params.")

    # 5. Save weekly report
    report = {
        "date":          str(today),
        "live_stats":    stats,
        "halt_set":      should_halt,
        "halt_reason":   halt_reason if should_halt else None,
    }
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = RESULTS_DIR / f"weekly_{ts}.json"
    out_path.write_text(json.dumps(report, indent=2))
    print(f"\nWeekly report saved: {out_path}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
