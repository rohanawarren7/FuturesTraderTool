#!/usr/bin/env python3
"""
Phase 0 Edge Validation Gate
Statistical GO / NO-GO test before committing any live capital.

Tests whether the pattern-mined strategy has a statistically significant edge
using a one-tailed binomial test on the labelled trade outcomes from raw_video_trades.

Usage (from WSL2):
    python scripts/edge_validation.py
    python scripts/edge_validation.py --min-confidence 0.65 --min-trades 30

Exit codes:
    0 = GO  (edge confirmed, p < alpha, sample large enough)
    1 = NO-GO (insufficient edge, insufficient data, or error)
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

import os
from scipy import stats
from database.db_manager import DBManager


# ────────────────────────────────────────────────────────────
# Thresholds
# ────────────────────────────────────────────────────────────

ALPHA            = 0.05   # Significance level — reject null if p < 0.05
NULL_WIN_RATE    = 0.50   # Null hypothesis: strategy is a coin flip
MIN_TRADES       = 30     # Minimum labelled trades required for any test
MIN_WIN_RATE     = 0.55   # Minimum observed win rate to pass (economic significance)
STRONG_EDGE_WR   = 0.62   # Win rate that constitutes a "strong" edge


def run_validation(db_path: str, min_confidence: float, min_trades: int) -> dict:
    db = DBManager(db_path)
    trades = db.get_all_video_trades(min_confidence=min_confidence)
    labelled = [t for t in trades if t.get("outcome") in ("WIN", "LOSS")]

    n_total   = len(labelled)
    n_wins    = sum(1 for t in labelled if t["outcome"] == "WIN")
    n_losses  = n_total - n_wins
    win_rate  = n_wins / n_total if n_total > 0 else 0.0

    result = {
        "total_trades":     len(trades),
        "labelled_trades":  n_total,
        "wins":             n_wins,
        "losses":           n_losses,
        "win_rate":         win_rate,
        "alpha":            ALPHA,
        "null_win_rate":    NULL_WIN_RATE,
        "min_trades_req":   min_trades,
        "go":               False,
        "reason":           "",
    }

    # ── Check sample size ──────────────────────────────────
    if n_total < min_trades:
        result["reason"] = (
            f"INSUFFICIENT_DATA: {n_total} labelled trades < {min_trades} minimum. "
            f"Run more videos or lower --min-confidence."
        )
        return result

    # ── Binomial test (one-tailed: win_rate > null) ────────
    # Null H0: p(win) = 0.50  |  Alt H1: p(win) > 0.50
    p_value = stats.binomtest(n_wins, n=n_total, p=NULL_WIN_RATE,
                               alternative="greater").pvalue
    result["p_value"] = p_value

    if p_value >= ALPHA:
        result["reason"] = (
            f"NO_EDGE: p={p_value:.4f} >= alpha={ALPHA}. "
            f"Win rate {win_rate:.1%} not statistically better than random."
        )
        return result

    if win_rate < MIN_WIN_RATE:
        result["reason"] = (
            f"INSUFFICIENT_WIN_RATE: Observed {win_rate:.1%} < economic threshold {MIN_WIN_RATE:.0%} "
            f"(statistically sig but economically marginal after commissions)."
        )
        return result

    # ── Per-setup breakdown ────────────────────────────────
    setup_stats = {}
    for setup in set(t.get("entry_trigger") for t in labelled if t.get("entry_trigger")):
        subset = [t for t in labelled if t.get("entry_trigger") == setup]
        s_wins = sum(1 for t in subset if t["outcome"] == "WIN")
        s_wr   = s_wins / len(subset) if subset else 0.0
        setup_stats[setup] = {
            "n": len(subset), "wins": s_wins, "win_rate": s_wr
        }
    result["setup_breakdown"] = setup_stats

    result["go"] = True
    edge_label = "STRONG" if win_rate >= STRONG_EDGE_WR else "MARGINAL"
    result["reason"] = (
        f"GO [{edge_label}]: {win_rate:.1%} win rate over {n_total} trades, "
        f"p={p_value:.4f} (alpha={ALPHA})"
    )
    return result


def main():
    parser = argparse.ArgumentParser(description="Phase 0 edge validation gate")
    parser.add_argument("--min-confidence", type=float, default=0.65,
                        help="Minimum combined confidence to include trade (default: 0.65)")
    parser.add_argument("--min-trades", type=int, default=MIN_TRADES,
                        help=f"Minimum labelled trades required (default: {MIN_TRADES})")
    args = parser.parse_args()

    db_path = os.getenv("DB_PATH", "./database/trading_analysis.db")
    result  = run_validation(db_path, args.min_confidence, args.min_trades)

    print("\n" + "=" * 55)
    print(" EDGE VALIDATION REPORT")
    print("=" * 55)
    print(f"  Total video trades in DB : {result['total_trades']}")
    print(f"  Labelled (WIN/LOSS)      : {result['labelled_trades']}")
    print(f"  Wins / Losses            : {result.get('wins', 0)} / {result.get('losses', 0)}")
    print(f"  Win rate                 : {result.get('win_rate', 0):.1%}")
    if "p_value" in result:
        print(f"  p-value (binomial)       : {result['p_value']:.4f}")
    print()
    status = "✓ GO" if result["go"] else "✗ NO-GO"
    print(f"  VERDICT: {status}")
    print(f"  Reason : {result['reason']}")

    if result.get("setup_breakdown"):
        print("\n  Per-setup breakdown:")
        for setup, s in sorted(result["setup_breakdown"].items(),
                                key=lambda x: -x[1]["win_rate"]):
            print(f"    {setup:<35} n={s['n']:>3}  WR={s['win_rate']:.0%}")

    print("=" * 55 + "\n")

    sys.exit(0 if result["go"] else 1)


if __name__ == "__main__":
    main()
