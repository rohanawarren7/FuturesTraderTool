"""
OPTION 2: Regime Filter — Trade VWAP Mean Reversion on Balanced Days Only
Classifies each day as TRENDING or BALANCED using multiple metrics,
then tests whether the VWAP ±1SD signal has edge on balanced days.

Regime metrics:
  1. VWAP-side time: % of RTH bars price spends above VWAP (balanced = 35–65%)
  2. VWAP slope: (end_vwap - start_vwap) / session_points (balanced = flat)
  3. Range efficiency: (|open-close|) / (high-low) — low = choppy/balanced
  4. SD1 containment: % bars within ±1SD (balanced = high, >75%)
"""
from __future__ import annotations
import sys, math
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import polars as pl
from data.databento_provider import load_ohlcv, add_daily_vwap

MES_TICK = 0.25; TICK_VAL = 1.25; COMMISSION = 3.16

def pval(wins, n, p0=0.50):
    if n < 2: return 1.0
    mu = n*p0; s = math.sqrt(n*p0*(1-p0))
    z = (wins-0.5-mu)/s
    return 0.5*math.erfc(z/math.sqrt(2))

def stats(trades):
    w = [t for t in trades if t["outcome"]=="WIN"]
    l = [t for t in trades if t["outcome"]=="LOSS"]
    n = len(w)+len(l)
    wr = len(w)/n if n else 0
    pnl = sum(t["net_pnl"] for t in trades)
    pf = abs(sum(t["net_pnl"] for t in w)/sum(t["net_pnl"] for t in l)) if l and w else 0
    p = pval(len(w), n) if n>=5 else 1.0
    sig = "✓ GO" if wr>=0.55 and p<0.05 and n>=30 else "~ EDGE" if wr>=0.50 and n>=15 else "✗"
    return n, len(w), wr, pnl, pf, p, sig

def fmt(label, n, wins, wr, pnl, pf, p, sig):
    return (f"  {label:<55} n={n:>4}  WR={wr:.0%}  "
            f"P&L=${pnl:>8,.0f}  PF={pf:.2f}  p={p:.3f}  {sig}")

def add_daily_atr(df):
    daily = (df.group_by("date")
               .agg((pl.col("high").max()-pl.col("low").min()).alias("dr"))
               .sort("date")
               .with_columns(pl.col("dr").rolling_mean(window_size=10,min_samples=1).alias("atr")))
    return df.join(daily.select(["date","atr"]), on="date", how="left")

def classify_days(rows):
    """
    For each date compute regime metrics. Returns dict: date_str -> metrics.
    """
    from collections import defaultdict
    days = defaultdict(list)
    for r in rows:
        days[str(r["date"])].append(r)

    day_stats = {}
    for date, bars in days.items():
        if len(bars) < 10:
            continue
        closes  = [b["close"] for b in bars]
        vwaps   = [b["vwap"]  for b in bars]
        vm1s    = [b["vwap_minus1"] for b in bars]
        vp1s    = [b["vwap_plus1"]  for b in bars]
        highs   = [b["high"] for b in bars]
        lows    = [b["low"]  for b in bars]

        n_bars = len(bars)

        # Metric 1: % time above VWAP (balanced = 35-65%)
        above_pct = sum(1 for c, v in zip(closes, vwaps) if c >= v) / n_bars

        # Metric 2: VWAP slope (balanced = near 0)
        vwap_change = abs(vwaps[-1] - vwaps[0])
        daily_range = max(highs) - min(lows)
        vwap_slope  = vwap_change / daily_range if daily_range > 0 else 1.0

        # Metric 3: Range efficiency — how much of H-L range was directional
        open_price  = closes[0]
        close_price = closes[-1]
        directionality = abs(close_price - open_price) / daily_range if daily_range > 0 else 1.0

        # Metric 4: SD1 containment — bars where close is within ±1SD
        in_sd1 = sum(1 for c, m1, p1 in zip(closes, vm1s, vp1s) if m1 <= c <= p1) / n_bars

        day_stats[date] = {
            "above_pct":       above_pct,
            "vwap_slope":      vwap_slope,
            "directionality":  directionality,
            "in_sd1":          in_sd1,
            "daily_range":     daily_range,
        }
    return day_stats

def regime_label(metrics, above_lo=0.35, above_hi=0.65, slope_thresh=0.25,
                 direction_thresh=0.40, sd1_thresh=0.70):
    """
    Returns 'BALANCED' or 'TRENDING' based on threshold combination.
    """
    balanced_votes = 0
    if above_lo <= metrics["above_pct"] <= above_hi: balanced_votes += 1
    if metrics["vwap_slope"] < slope_thresh:         balanced_votes += 1
    if metrics["directionality"] < direction_thresh: balanced_votes += 1
    if metrics["in_sd1"] > sd1_thresh:               balanced_votes += 1
    return "BALANCED" if balanced_votes >= 3 else "TRENDING"

def simulate_with_regime(rows, day_stats, stop_m, target_m,
                         regime_filter=None,    # None=all, "BALANCED", "TRENDING"
                         only_direction=None,
                         time_start=9*60, time_end=14*60+30,
                         above_lo=0.35, above_hi=0.65,
                         slope_thresh=0.25, direction_thresh=0.40,
                         sd1_thresh=0.70):
    trades = []
    n = len(rows)

    for i in range(1, n):
        bar = rows[i]; prev = rows[i-1]
        if bar.get("atr") is None or bar["atr"] == 0: continue
        t_ct = bar["time_ct_minutes"]
        if t_ct < time_start or t_ct >= time_end: continue

        date_str = str(bar["date"])
        m = day_stats.get(date_str)
        if m is None: continue

        if regime_filter:
            r = regime_label(m, above_lo, above_hi, slope_thresh,
                             direction_thresh, sd1_thresh)
            if r != regime_filter: continue

        atr = bar["atr"]
        vm1 = bar["vwap_minus1"]; vp1 = bar["vwap_plus1"]

        for direction, triggered in [
            ("LONG",  prev["close"] < vm1 and bar["close"] >= vm1),
            ("SHORT", prev["close"] > vp1 and bar["close"] <= vp1),
        ]:
            if not triggered: continue
            if only_direction and direction != only_direction: continue

            entry = bar["close"]
            stop  = entry - stop_m*atr if direction=="LONG" else entry + stop_m*atr
            tgt   = entry + target_m*atr if direction=="LONG" else entry - target_m*atr

            outcome="TIMEOUT"; ep=entry; j=i+1
            while j<n and rows[j]["date"]==bar["date"]:
                b=rows[j]
                if direction=="LONG":
                    if b["low"]<=stop:  outcome="LOSS"; ep=stop; break
                    if b["high"]>=tgt:  outcome="WIN";  ep=tgt;  break
                else:
                    if b["high"]>=stop: outcome="LOSS"; ep=stop; break
                    if b["low"]<=tgt:   outcome="WIN";  ep=tgt;  break
                j+=1

            pnl=((ep-entry) if direction=="LONG" else (entry-ep))/MES_TICK*TICK_VAL-COMMISSION
            trades.append({"date":date_str,"direction":direction,
                            "outcome":outcome,"net_pnl":round(pnl,2),
                            "regime": regime_label(m, above_lo, above_hi,
                                                   slope_thresh, direction_thresh, sd1_thresh)})
    return trades

def main():
    print("\n" + "="*75)
    print("  OPTION 2: REGIME FILTER — BALANCED VS TRENDING DAYS")
    print("="*75)

    print("Loading 2yr MES data...")
    df = load_ohlcv(start_date="2023-01-01", end_date="2025-01-01")
    df = add_daily_vwap(df)
    df = add_daily_atr(df)
    rows = df.to_dicts()
    print(f"{len(df):,} bars  |  {df['date'].n_unique()} trading days\n")

    print("Classifying days...")
    day_stats = classify_days(rows)
    balanced = sum(1 for m in day_stats.values()
                   if regime_label(m) == "BALANCED")
    trending = len(day_stats) - balanced
    print(f"  BALANCED days: {balanced}  ({balanced/len(day_stats):.0%})")
    print(f"  TRENDING days: {trending}  ({trending/len(day_stats):.0%})\n")

    # ── Section A: Baseline by regime ────────────────────────────────────────
    print("─"*75)
    print("  A. ±1SD SIGNAL — ALL vs BALANCED vs TRENDING (stop 0.5, target 0.5)")
    print("─"*75)
    for regime, label in [(None,"All days"),(  "BALANCED","BALANCED days only"),
                           ("TRENDING","TRENDING days only")]:
        t = simulate_with_regime(rows, day_stats, 0.5, 0.5, regime_filter=regime)
        print(fmt(label, *stats(t)))

    # ── Section B: RR sensitivity on balanced days ────────────────────────────
    print("\n" + "─"*75)
    print("  B. BALANCED DAYS — RR SENSITIVITY")
    print("─"*75)
    for sm, tm in [(0.25,0.25),(0.5,0.5),(0.5,0.75),(0.5,1.0),(0.75,0.75),(1.0,1.0)]:
        t = simulate_with_regime(rows, day_stats, sm, tm, regime_filter="BALANCED")
        print(fmt(f"  Stop {sm}×ATR  Target {tm}×ATR  (RR {tm/sm:.1f}:1)", *stats(t)))

    # ── Section C: Regime threshold sensitivity ───────────────────────────────
    print("\n" + "─"*75)
    print("  C. REGIME THRESHOLD SENSITIVITY (stop 0.5, target 0.5)")
    print("─"*75)
    configs = [
        ("Strict balanced (2+ votes of 4)",   0.40,0.60,0.20,0.35,0.75),
        ("Default balanced (3+ votes of 4)",  0.35,0.65,0.25,0.40,0.70),
        ("Loose balanced (2+ votes, wide)",   0.30,0.70,0.30,0.45,0.65),
        ("VWAP-side only (35-65% above)",     0.35,0.65,1.0, 1.0, 0.0 ),
        ("Low directionality only (<30%)",    0.0, 1.0, 1.0, 0.30,0.0 ),
        ("High SD1 containment only (>75%)",  0.0, 1.0, 1.0, 1.0, 0.75),
    ]
    for label, alo, ahi, slope, direc, sd1 in configs:
        # Count balanced days for this config
        bal = sum(1 for m in day_stats.values()
                  if regime_label(m, alo, ahi, slope, direc, sd1)=="BALANCED")
        t = simulate_with_regime(rows, day_stats, 0.5, 0.5,
                                  regime_filter="BALANCED",
                                  above_lo=alo, above_hi=ahi,
                                  slope_thresh=slope, direction_thresh=direc,
                                  sd1_thresh=sd1)
        print(fmt(f"  {label} [{bal}d]", *stats(t)))

    # ── Section D: Direction on balanced days ─────────────────────────────────
    print("\n" + "─"*75)
    print("  D. DIRECTION BREAKDOWN ON BALANCED DAYS (stop 0.5, target 0.5)")
    print("─"*75)
    for d, label in [(None,"Both"),(  "LONG","Long only"),("SHORT","Short only")]:
        t = simulate_with_regime(rows, day_stats, 0.5, 0.5,
                                  regime_filter="BALANCED", only_direction=d)
        print(fmt(f"  {label}", *stats(t)))

    # ── Section E: Time of day on balanced days ───────────────────────────────
    print("\n" + "─"*75)
    print("  E. TIME OF DAY ON BALANCED DAYS (stop 0.5, target 0.5)")
    print("─"*75)
    windows = [
        ("09:00–10:30 CT", 9*60,     10*60+30),
        ("09:00–11:00 CT", 9*60,     11*60),
        ("10:00–12:00 CT", 10*60,    12*60),
        ("11:00–14:00 CT", 11*60,    14*60),
        ("13:00–14:30 CT", 13*60,    14*60+30),
        ("09:00–14:30 CT (full)", 9*60, 14*60+30),
    ]
    for label, ts, te in windows:
        t = simulate_with_regime(rows, day_stats, 0.5, 0.5,
                                  regime_filter="BALANCED",
                                  time_start=ts, time_end=te)
        print(fmt(f"  {label}", *stats(t)))

    # ── Section F: Regime metric distribution ─────────────────────────────────
    print("\n" + "─"*75)
    print("  F. DAY CLASSIFICATION BREAKDOWN (metric distribution)")
    print("─"*75)
    metrics_list = list(day_stats.values())
    for metric, label in [
        ("above_pct",      "% time above VWAP"),
        ("vwap_slope",     "VWAP slope ratio"),
        ("directionality", "Range directionality"),
        ("in_sd1",         "% bars within ±1SD"),
    ]:
        vals = sorted(m[metric] for m in metrics_list)
        n = len(vals)
        p25 = vals[n//4]; p50 = vals[n//2]; p75 = vals[3*n//4]
        print(f"  {label:<35} p25={p25:.2f}  p50={p50:.2f}  p75={p75:.2f}")

    # ── Section G: Monthly P&L on balanced days ───────────────────────────────
    print("\n" + "─"*75)
    print("  G. MONTHLY P&L — BALANCED DAYS (stop 0.5, target 0.5)")
    print("─"*75)
    all_t = simulate_with_regime(rows, day_stats, 0.5, 0.5, regime_filter="BALANCED")
    monthly = {}
    for t in all_t:
        m = t["date"][:7]
        monthly[m] = monthly.get(m, 0) + t["net_pnl"]
    pos = sum(1 for v in monthly.values() if v >= 0)
    print(f"  Profitable months: {pos}/{len(monthly)}\n")
    for m in sorted(monthly):
        bar = "█" * max(0, int(abs(monthly[m])/50))
        sign = "+" if monthly[m] >= 0 else " "
        print(f"    {m}  {sign}${monthly[m]:6.0f}  {bar}")

    print()

if __name__ == "__main__":
    main()
