#!/usr/bin/env python3
"""
Pattern Mining Runner
Mines raw_video_trades for high-probability entry conditions.

Usage:
    python scripts/run_pattern_mining.py
    python scripts/run_pattern_mining.py --min-confidence 0.55  # if data is sparse
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

from database.db_manager import DBManager
from video_analysis.pattern_miner import PatternMiner

RESULTS_DIR = Path("backtesting/results")
RESULTS_DIR.mkdir(exist_ok=True)


def main():
    parser = argparse.ArgumentParser(description="Run pattern mining on video trade data")
    parser.add_argument("--min-confidence", type=float, default=0.65,
                        help="Minimum visual confidence threshold (default: 0.65)")
    parser.add_argument("--top-n", type=int, default=10,
                        help="Number of top conditions to display (default: 10)")
    args = parser.parse_args()

    db_path = os.getenv("DB_PATH", "./database/trading_analysis.db")
    db = DBManager(db_path)
    miner = PatternMiner(db)

    # Data summary
    all_trades = db.get_all_video_trades(min_confidence=0.0)
    high_conf = db.get_all_video_trades(min_confidence=args.min_confidence)
    labelled = [t for t in high_conf if t.get("outcome") in ("WIN", "LOSS")]

    print(f"\nDATABASE SUMMARY")
    print(f"  Total video trades:          {len(all_trades)}")
    print(f"  High confidence (>={args.min_confidence}): {len(high_conf)}")
    print(f"  Labelled WIN/LOSS:           {len(labelled)}")

    if len(labelled) < 30:
        print(f"\n[WARN] Only {len(labelled)} labelled trades — need 30 minimum.")
        print("  Options:")
        print("  - Run more videos: python scripts/run_video_pipeline.py")
        print("  - Lower threshold: python scripts/run_pattern_mining.py --min-confidence 0.55")
        sys.exit(1)

    # Top conditions table
    miner.print_top_conditions(n=args.top_n, min_confidence=args.min_confidence)

    # Win rate by session phase
    by_phase = miner.win_rate_by_hour(min_confidence=args.min_confidence)
    if not by_phase.empty:
        print("WIN RATE BY SESSION PHASE")
        print(f"{'Phase':<15} {'Trades':>8} {'Win%':>8}")
        print("-" * 35)
        for _, row in by_phase.iterrows():
            print(f"{row['session_phase']:<15} {int(row['trades']):>8} "
                  f"{row['win_rate']*100:>7.1f}%")
        print()

    # Recommended strategy
    print(miner.recommend_strategy(min_confidence=args.min_confidence))

    # Save CSV
    try:
        import pandas as pd
        df = pd.DataFrame(miner.get_labelled_trades(min_confidence=args.min_confidence))
        analysis = miner.analyse_conditions(df)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_path = RESULTS_DIR / f"pattern_analysis_{ts}.csv"
        analysis.to_csv(out_path, index=False)
        print(f"Full analysis saved: {out_path}")
    except Exception as e:
        print(f"[WARN] Could not save CSV: {e}")


if __name__ == "__main__":
    main()
