#!/usr/bin/env python3
"""
Walk-Forward Optimisation Runner

Usage (from WSL2):
    nohup python scripts/run_wfo.py --firm TOPSTEP_50K > logs/wfo.log 2>&1 &
    python scripts/run_wfo.py --firm TOPSTEP_50K --trials 50  # fast mode
"""

import argparse
import json
import os
import sys
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from data.tradovate_data_provider import TradovateDataProvider, get_current_front_month
from optimisation.walk_forward import WalkForwardOptimiser
from database.db_manager import DBManager

RESULTS_DIR = Path("backtesting/results")
RESULTS_DIR.mkdir(exist_ok=True)


def main():
    parser = argparse.ArgumentParser(description="Run Walk-Forward Optimisation")
    parser.add_argument("--firm", default="TOPSTEP_50K")
    parser.add_argument("--instrument", default="MES")
    parser.add_argument("--data", default=None,
                        help="Path to CSV file (default: fetch from Tradovate)")
    parser.add_argument("--trials", type=int, default=100,
                        help="Optuna trials per IS window (default: 100)")
    parser.add_argument("--is-months", type=int, default=4,
                        help="In-sample months per window (default: 4)")
    parser.add_argument("--oos-months", type=int, default=1,
                        help="Out-of-sample months per window (default: 1)")
    args = parser.parse_args()

    db_path = os.getenv("DB_PATH", "./database/trading_analysis.db")
    db = DBManager(db_path)

    # Load data
    if args.data:
        import pandas as pd
        df = pd.read_csv(args.data, parse_dates=["timestamp"])
        print(f"[WFO] Loaded {len(df)} bars from {args.data}")
    else:
        print("[WFO] Fetching 12 months of data from Tradovate...")
        provider = TradovateDataProvider.from_env()
        symbol = get_current_front_month(args.instrument)
        df = provider.get_12_months_5m(symbol)

    wfo = WalkForwardOptimiser(args.firm, args.instrument)
    wfo.IS_MONTHS = args.is_months
    wfo.OOS_MONTHS = args.oos_months
    wfo.N_TRIALS = args.trials

    print(f"[WFO] Starting — IS={args.is_months}mo, OOS={args.oos_months}mo, "
          f"trials={args.trials}")

    result = wfo.run_full_wfo(df)

    if "error" in result:
        print(f"[ERROR] {result['error']}")
        sys.exit(1)

    # Print summary
    print(f"\n{'='*55}")
    print(f"WALK-FORWARD OPTIMISATION RESULTS")
    print(f"{'='*55}")
    print(f"Windows tested:      {result['windows_tested']}")
    print(f"Avg IS PF:           {result['avg_is_profit_factor']:.3f}")
    print(f"Avg OOS PF:          {result['avg_oos_profit_factor']:.3f}")
    print(f"WFE Score:           {result['wfe_score']:.3f}")
    print(f"Assessment:          {result['assessment']}")
    print(f"\nBest params: {json.dumps(result.get('best_params', {}), indent=2)}")
    print(f"{'='*55}")

    # Save best params to DB
    if result.get("best_params") and result["wfe_score"] >= 0.40:
        from datetime import date
        db.save_strategy_params({
            "version":           f"wfo_{datetime.now().strftime('%Y%m%d')}",
            "regime":            "BALANCED",
            "sd_mult_entry":     result["best_params"].get("sd_mult_entry", 1.0),
            "sd_mult_stop":      result["best_params"].get("sd_mult_stop", 1.5),
            "rr_ratio":          result["best_params"].get("rr_ratio", 2.0),
            "delta_threshold":   result["best_params"].get("delta_threshold", 300),
            "volume_threshold":  result["best_params"].get("volume_threshold", 1.8),
            "session_start":     "09:45",
            "session_end":       "14:00",
            "max_trades_per_day": 4,
            "valid_from":        str(date.today()),
            "valid_to":          None,
            "wfe_score":         result["wfe_score"],
        })
        print(f"[WFO] Best params saved to strategy_params table (is_active=1)")
    else:
        print(f"[WFO] WFE too low — params NOT saved. Review and reduce free parameters.")

    # Save full report
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = RESULTS_DIR / f"wfo_{args.firm}_{ts}.json"
    out_path.write_text(json.dumps(result, indent=2, default=str))
    print(f"[WFO] Full report saved: {out_path}")


if __name__ == "__main__":
    main()
