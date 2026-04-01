"""
Databento Historical Backtest — MES VWAP Mean Reversion
Uses 2 years of 1-minute OHLCV data to validate rule-based edge.

Signal: VWAP ±1SD mean-reversion touches during RTH (09:00–14:30 CT)
Exit:   Fixed RR using ATR-based stops and targets

Usage:
    python scripts/backtest_databento.py
    python scripts/backtest_databento.py --start 2024-01-01
    python scripts/backtest_databento.py --stop-mult 0.75 --target-mult 1.5
"""
from __future__ import annotations

import sys
import math
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import polars as pl
from data.databento_provider import load_ohlcv, add_daily_vwap

# ── Parameters ───────────────────────────────────────────────────────────────

STOP_ATR_MULT   = 0.5
TARGET_ATR_MULT = 0.75
MES_TICK        = 0.25
MES_TICK_VALUE  = 1.25
COMMISSION_RT   = 3.16
ALPHA           = 0.05

SIGNAL_START_CT = 9 * 60
SIGNAL_END_CT   = 14 * 60 + 30


# ── Binomial p-value (no scipy) ──────────────────────────────────────────────

def binomial_pvalue(wins: int, n: int, p0: float = 0.50) -> float:
    if n < 30:
        return sum(math.comb(n, k) * (p0**k) * ((1-p0)**(n-k)) for k in range(wins, n+1))
    mu = n * p0
    sigma = math.sqrt(n * p0 * (1 - p0))
    z = (wins - 0.5 - mu) / sigma
    return 0.5 * math.erfc(z / math.sqrt(2))


# ── ATR ──────────────────────────────────────────────────────────────────────

def add_daily_atr(df: pl.DataFrame) -> pl.DataFrame:
    daily = (
        df.group_by("date")
        .agg((pl.col("high").max() - pl.col("low").min()).alias("daily_range"))
        .sort("date")
        .with_columns(
            pl.col("daily_range")
              .rolling_mean(window_size=10, min_periods=1)
              .alias("atr")
        )
    )
    return df.join(daily.select(["date", "atr"]), on="date", how="left")


# ── Signal + simulation ──────────────────────────────────────────────────────

def run_backtest(rows: list[dict], stop_mult: float, target_mult: float) -> list[dict]:
    trades = []
    n = len(rows)

    for i in range(1, n):
        bar  = rows[i]
        prev = rows[i - 1]

        if bar["time_ct_minutes"] < SIGNAL_START_CT:
            continue
        if bar["time_ct_minutes"] >= SIGNAL_END_CT:
            continue
        if bar.get("atr") is None or bar["atr"] == 0:
            continue

        atr  = bar["atr"]
        vm1  = bar["vwap_minus1"]
        vp1  = bar["vwap_plus1"]

        for direction, triggered in [
            ("LONG",  prev["close"] < vm1 and bar["close"] >= vm1),
            ("SHORT", prev["close"] > vp1 and bar["close"] <= vp1),
        ]:
            if not triggered:
                continue

            entry = bar["close"]
            stop_dist   = stop_mult   * atr
            target_dist = target_mult * atr

            if direction == "LONG":
                stop   = entry - stop_dist
                target = entry + target_dist
            else:
                stop   = entry + stop_dist
                target = entry - target_dist

            # Walk forward
            outcome    = "TIMEOUT"
            exit_price = entry
            j = i + 1
            while j < n and rows[j]["date"] == bar["date"]:
                b = rows[j]
                if direction == "LONG":
                    if b["low"]  <= stop:   outcome = "LOSS"; exit_price = stop;   break
                    if b["high"] >= target: outcome = "WIN";  exit_price = target; break
                else:
                    if b["high"] >= stop:   outcome = "LOSS"; exit_price = stop;   break
                    if b["low"]  <= target: outcome = "WIN";  exit_price = target; break
                j += 1

            pnl = (exit_price - entry) if direction == "LONG" else (entry - exit_price)
            net = pnl / MES_TICK * MES_TICK_VALUE - COMMISSION_RT

            trades.append({
                "date": bar["date"], "direction": direction,
                "outcome": outcome, "net_pnl": round(net, 2),
            })

    return trades


# ── Main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start",       default="2023-01-01")
    parser.add_argument("--end",         default="2025-01-01")
    parser.add_argument("--stop-mult",   type=float, default=STOP_ATR_MULT)
    parser.add_argument("--target-mult", type=float, default=TARGET_ATR_MULT)
    parser.add_argument("--data-dir",    default=None)
    args = parser.parse_args()

    data_dir = Path(args.data_dir) if args.data_dir else None

    print(f"\n{'='*60}")
    print(f"  Databento MES Backtest  |  {args.start} → {args.end}")
    print(f"  Stop {args.stop_mult}×ATR  |  Target {args.target_mult}×ATR  |  RR {args.target_mult/args.stop_mult:.1f}:1")
    print(f"{'='*60}")

    print("\nLoading data...")
    df = load_ohlcv(data_dir=data_dir, start_date=args.start, end_date=args.end)
    df = add_daily_vwap(df)
    df = add_daily_atr(df)
    print(f"{len(df):,} bars  |  {df['date'].n_unique()} trading days")

    print("Running backtest...")
    trades = run_backtest(df.to_dicts(), args.stop_mult, args.target_mult)

    wins     = [t for t in trades if t["outcome"] == "WIN"]
    losses   = [t for t in trades if t["outcome"] == "LOSS"]
    timeouts = [t for t in trades if t["outcome"] == "TIMEOUT"]
    n        = len(wins) + len(losses)
    wr       = len(wins) / n if n else 0
    total    = sum(t["net_pnl"] for t in trades)

    print(f"\n{'─'*60}")
    print(f"  Trades: {len(trades)}  ({len(wins)}W / {len(losses)}L / {len(timeouts)} timeout)")
    print(f"  Win rate:      {wr:.1%}")
    print(f"  Total net P&L: ${total:,.0f}")
    if losses:
        pf = abs(sum(t["net_pnl"] for t in wins) / sum(t["net_pnl"] for t in losses))
        print(f"  Profit factor: {pf:.2f}")

    if n >= 30:
        p = binomial_pvalue(len(wins), n)
        print(f"  p-value:       {p:.4f}")
        verdict = "✓ GO" if p < ALPHA and wr >= 0.55 else "✗ NO-GO"
        print(f"\n  VERDICT: {verdict}")
    else:
        print(f"\n  VERDICT: INSUFFICIENT DATA ({n} resolved trades)")

    print(f"{'='*60}\n")

    # Monthly P&L
    monthly: dict[str, float] = {}
    for t in trades:
        m = str(t["date"])[:7]
        monthly[m] = monthly.get(m, 0) + t["net_pnl"]
    print("Monthly net P&L:")
    for m in sorted(monthly):
        bar = "█" * max(0, int(abs(monthly[m]) / 100))
        sign = "+" if monthly[m] >= 0 else " "
        print(f"  {m}  {sign}${monthly[m]:7.0f}  {bar}")


if __name__ == "__main__":
    main()
