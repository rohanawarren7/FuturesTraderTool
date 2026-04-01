"""
OPTION 2 (CORRECTED): Forward-Looking Regime Filter — No Lookahead Bias

Original test_regime_filter.py classified TODAY's regime using TODAY's bars,
then filtered trades taken during that same day. This is lookahead bias: at
10am you don't yet know if today will be balanced.

This version classifies YESTERDAY's regime and uses that to filter TODAY's trades.
If the edge survives, it's real and tradeable live. If it collapses, the original
81% WR was entirely an artefact.

Regime metrics (computed on day D-1, applied to trades on day D):
  1. VWAP-side time: % of RTH bars price spent above VWAP (balanced = 35–65%)
  2. VWAP slope: vwap_change / daily_range (balanced = flat, < 0.25)
  3. Range efficiency: |open-close| / (high-low) — low = choppy/balanced
  4. SD1 containment: % bars within ±1SD (balanced > 0.70)

Also tests:
  - 2-day lookback (average of D-1 and D-2 classification)
  - Rolling 5-day balanced streak
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
    return (f"  {label:<60} n={n:>4}  WR={wr:.0%}  "
            f"P&L=${pnl:>8,.0f}  PF={pf:.2f}  p={p:.3f}  {sig}")

def add_daily_atr(df):
    daily = (df.group_by("date")
               .agg((pl.col("high").max()-pl.col("low").min()).alias("dr"))
               .sort("date")
               .with_columns(pl.col("dr").rolling_mean(window_size=10,min_samples=1).alias("atr")))
    return df.join(daily.select(["date","atr"]), on="date", how="left")

def compute_day_metrics(bars: list) -> dict | None:
    """
    Compute regime metrics for a single day from its bars.
    Returns None if insufficient data.
    """
    if len(bars) < 10:
        return None
    closes  = [b["close"] for b in bars]
    vwaps   = [b["vwap"]  for b in bars]
    vm1s    = [b["vwap_minus1"] for b in bars]
    vp1s    = [b["vwap_plus1"]  for b in bars]
    highs   = [b["high"] for b in bars]
    lows    = [b["low"]  for b in bars]
    n_bars  = len(bars)

    above_pct    = sum(1 for c, v in zip(closes, vwaps) if c >= v) / n_bars
    vwap_change  = abs(vwaps[-1] - vwaps[0])
    daily_range  = max(highs) - min(lows)
    vwap_slope   = vwap_change / daily_range if daily_range > 0 else 1.0
    directionality = abs(closes[-1] - closes[0]) / daily_range if daily_range > 0 else 1.0
    in_sd1       = sum(1 for c, m1, p1 in zip(closes, vm1s, vp1s) if m1 <= c <= p1) / n_bars

    return {
        "above_pct":      above_pct,
        "vwap_slope":     vwap_slope,
        "directionality": directionality,
        "in_sd1":         in_sd1,
        "daily_range":    daily_range,
    }

def regime_label(metrics, above_lo=0.35, above_hi=0.65, slope_thresh=0.25,
                 direction_thresh=0.40, sd1_thresh=0.70):
    votes = 0
    if above_lo <= metrics["above_pct"] <= above_hi: votes += 1
    if metrics["vwap_slope"] < slope_thresh:         votes += 1
    if metrics["directionality"] < direction_thresh: votes += 1
    if metrics["in_sd1"] > sd1_thresh:               votes += 1
    return "BALANCED" if votes >= 3 else "TRENDING"

def build_day_map(rows):
    """Group bars by date. Returns {date_str: [bars]}."""
    from collections import defaultdict
    days = defaultdict(list)
    for r in rows:
        days[str(r["date"])].append(r)
    return days

def simulate_lookahead(rows, day_stats, stop_m, target_m,
                       time_start=9*60, time_end=14*60+30):
    """Replicates original lookahead approach for comparison."""
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
        if regime_label(m) != "BALANCED": continue
        atr = bar["atr"]
        vm1 = bar["vwap_minus1"]; vp1 = bar["vwap_plus1"]
        for direction, triggered in [
            ("LONG",  prev["close"] < vm1 and bar["close"] >= vm1),
            ("SHORT", prev["close"] > vp1 and bar["close"] <= vp1),
        ]:
            if not triggered: continue
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
            trades.append({"date":date_str,"direction":direction,"outcome":outcome,"net_pnl":round(pnl,2)})
    return trades


def simulate_forward(rows, day_map, sorted_dates, stop_m, target_m,
                     lookback=1,           # 1 = use D-1, 2 = use D-1 and D-2
                     regime_filter=None,   # None=all, "BALANCED", "TRENDING"
                     only_direction=None,
                     time_start=9*60, time_end=14*60+30,
                     above_lo=0.35, above_hi=0.65,
                     slope_thresh=0.25, direction_thresh=0.40,
                     sd1_thresh=0.70,
                     streak_required=0):   # require N consecutive prior balanced days
    """
    Forward-only simulation: classify regime using prior day(s), trade today.
    """
    # Pre-compute metrics for every date
    date_metrics = {}
    for d in sorted_dates:
        m = compute_day_metrics(day_map.get(d, []))
        if m is not None:
            date_metrics[d] = m

    # Build per-date regime label using lookback
    date_regime = {}
    for i, d in enumerate(sorted_dates):
        if i < lookback:
            continue  # not enough history
        prior_metrics = []
        for lb in range(1, lookback+1):
            m = date_metrics.get(sorted_dates[i-lb])
            if m is not None:
                prior_metrics.append(m)
        if not prior_metrics:
            continue
        # Average metrics across lookback window
        avg = {
            k: sum(m[k] for m in prior_metrics) / len(prior_metrics)
            for k in prior_metrics[0]
        }
        date_regime[d] = regime_label(avg, above_lo, above_hi, slope_thresh,
                                      direction_thresh, sd1_thresh)

    # Build streak map: how many consecutive prior balanced days
    if streak_required > 0:
        date_streak = {}
        for i, d in enumerate(sorted_dates):
            streak = 0
            j = i - 1
            while j >= 0 and date_regime.get(sorted_dates[j]) == "BALANCED":
                streak += 1
                j -= 1
            date_streak[d] = streak
    else:
        date_streak = {}

    trades = []
    n = len(rows)

    for i in range(1, n):
        bar = rows[i]; prev = rows[i-1]
        if bar.get("atr") is None or bar["atr"] == 0: continue
        t_ct = bar["time_ct_minutes"]
        if t_ct < time_start or t_ct >= time_end: continue

        date_str = str(bar["date"])

        if regime_filter:
            r = date_regime.get(date_str)
            if r is None or r != regime_filter:
                continue

        if streak_required > 0:
            if date_streak.get(date_str, 0) < streak_required:
                continue

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
            trades.append({"date": date_str, "direction": direction,
                            "outcome": outcome, "net_pnl": round(pnl,2),
                            "regime_used": date_regime.get(date_str, "UNKNOWN")})
    return trades


def main():
    print("\n" + "="*78)
    print("  OPTION 2 (CORRECTED): FORWARD-LOOKING REGIME FILTER — NO LOOKAHEAD BIAS")
    print("="*78)
    print("  Regime is classified from PREVIOUS DAY's bars and applied to TODAY's trades.")
    print("  If edge survives vs 81% WR in original test → it's real and tradeable.\n")

    print("Loading 2yr MES data...")
    df = load_ohlcv(start_date="2023-01-01", end_date="2025-01-01")
    df = add_daily_vwap(df)
    df = add_daily_atr(df)
    rows = df.to_dicts()
    print(f"{len(df):,} bars  |  {df['date'].n_unique()} trading days\n")

    day_map = build_day_map(rows)
    sorted_dates = sorted(day_map.keys())

    # Count how many days are classified as BALANCED (forward basis)
    date_metrics = {}
    for d in sorted_dates:
        m = compute_day_metrics(day_map.get(d, []))
        if m is not None:
            date_metrics[d] = m

    forward_balanced = sum(
        1 for i, d in enumerate(sorted_dates)
        if i >= 1 and date_metrics.get(sorted_dates[i-1]) is not None
        and regime_label(date_metrics[sorted_dates[i-1]]) == "BALANCED"
    )
    print(f"  Days with BALANCED prior day: {forward_balanced} / {len(sorted_dates)-1}")
    print(f"  (Original lookahead test had 76 balanced days in 508)\n")

    # ── Section A: Head-to-head vs original ─────────────────────────────────
    print("─"*78)
    print("  A. HEAD-TO-HEAD: LOOKAHEAD vs FORWARD-ONLY (stop 0.5, target 0.5)")
    print("─"*78)

    # Reproduce original lookahead result inline (classify today using today's bars)
    day_stats_lookahead = {d: compute_day_metrics(day_map[d])
                           for d in sorted_dates if compute_day_metrics(day_map[d]) is not None}
    t_la = simulate_lookahead(rows, day_stats_lookahead, 0.5, 0.5)
    print(fmt("  ORIGINAL (lookahead — classify today using today's bars)", *stats(t_la)))

    # Forward-only: use D-1
    t_fwd = simulate_forward(rows, day_map, sorted_dates, 0.5, 0.5, regime_filter="BALANCED")
    print(fmt("  FORWARD  (no lookahead — classify today using yesterday's bars)", *stats(t_fwd)))

    # All days baseline
    t_all = simulate_forward(rows, day_map, sorted_dates, 0.5, 0.5, regime_filter=None)
    print(fmt("  BASELINE (no regime filter)", *stats(t_all)))

    # ── Section B: RR sensitivity on forward-balanced days ───────────────────
    print("\n" + "─"*78)
    print("  B. FORWARD BALANCED DAYS — RR SENSITIVITY")
    print("─"*78)
    for sm, tm in [(0.25,0.25),(0.5,0.5),(0.5,0.75),(0.5,1.0),(0.75,0.75),(1.0,1.0)]:
        t = simulate_forward(rows, day_map, sorted_dates, sm, tm, regime_filter="BALANCED")
        print(fmt(f"  Stop {sm}×ATR  Target {tm}×ATR  (RR {tm/sm:.1f}:1)", *stats(t)))

    # ── Section C: Direction on forward-balanced days ────────────────────────
    print("\n" + "─"*78)
    print("  C. DIRECTION BREAKDOWN — FORWARD BALANCED DAYS (stop 0.5, target 0.5)")
    print("─"*78)
    for d, label in [(None,"Both"),(  "LONG","Long only"),("SHORT","Short only")]:
        t = simulate_forward(rows, day_map, sorted_dates, 0.5, 0.5,
                              regime_filter="BALANCED", only_direction=d)
        print(fmt(f"  {label}", *stats(t)))

    # ── Section D: Multi-day lookback ────────────────────────────────────────
    print("\n" + "─"*78)
    print("  D. LOOKBACK WINDOW — HOW MANY PRIOR DAYS TO AVERAGE (stop 0.5, target 0.5)")
    print("─"*78)
    for lb, label in [(1,"D-1 only"),(2,"Avg D-1 & D-2"),(3,"Avg D-1 to D-3"),(5,"Avg D-1 to D-5")]:
        t = simulate_forward(rows, day_map, sorted_dates, 0.5, 0.5,
                              regime_filter="BALANCED", lookback=lb)
        print(fmt(f"  {label}", *stats(t)))

    # ── Section E: Streak filter (require consecutive balanced prior days) ───
    print("\n" + "─"*78)
    print("  E. STREAK FILTER — REQUIRE N CONSECUTIVE PRIOR BALANCED DAYS")
    print("─"*78)
    for streak, label in [(0,"No streak req"),(1,"≥1 prior balanced"),(2,"≥2 prior balanced"),(3,"≥3 prior balanced")]:
        t = simulate_forward(rows, day_map, sorted_dates, 0.5, 0.5,
                              regime_filter="BALANCED", streak_required=streak)
        print(fmt(f"  {label}", *stats(t)))

    # ── Section F: Threshold sensitivity (forward basis) ────────────────────
    print("\n" + "─"*78)
    print("  F. REGIME THRESHOLD SENSITIVITY — FORWARD ONLY (stop 0.5, target 0.5)")
    print("─"*78)
    configs = [
        ("Strict balanced (slope<0.20, dir<0.35, sd1>0.75)", 0.40,0.60,0.20,0.35,0.75),
        ("Default balanced (slope<0.25, dir<0.40, sd1>0.70)", 0.35,0.65,0.25,0.40,0.70),
        ("Loose balanced (slope<0.30, dir<0.45, sd1>0.65)",   0.30,0.70,0.30,0.45,0.65),
        ("Very loose (slope<0.35, dir<0.50, sd1>0.60)",       0.30,0.70,0.35,0.50,0.60),
    ]
    for label, alo, ahi, slope, direc, sd1 in configs:
        t = simulate_forward(rows, day_map, sorted_dates, 0.5, 0.5,
                              regime_filter="BALANCED",
                              above_lo=alo, above_hi=ahi,
                              slope_thresh=slope, direction_thresh=direc,
                              sd1_thresh=sd1)
        print(fmt(f"  {label}", *stats(t)))

    # ── Section G: Time of day — forward balanced days ───────────────────────
    print("\n" + "─"*78)
    print("  G. TIME OF DAY — FORWARD BALANCED DAYS (stop 0.5, target 0.5)")
    print("─"*78)
    windows = [
        ("09:00–10:30 CT", 9*60,     10*60+30),
        ("09:00–11:00 CT", 9*60,     11*60),
        ("10:00–12:00 CT", 10*60,    12*60),
        ("11:00–14:00 CT", 11*60,    14*60),
        ("09:00–14:30 CT (full)", 9*60, 14*60+30),
    ]
    for label, ts, te in windows:
        t = simulate_forward(rows, day_map, sorted_dates, 0.5, 0.5,
                              regime_filter="BALANCED", time_start=ts, time_end=te)
        print(fmt(f"  {label}", *stats(t)))

    # ── Section H: Trending days — does prior-day TRENDING have any edge? ───
    print("\n" + "─"*78)
    print("  H. PRIOR-DAY TRENDING DAYS — ANY EDGE? (stop 0.5, target 0.5)")
    print("─"*78)
    for d, label in [(None,"Both"),(  "LONG","Long only"),("SHORT","Short only")]:
        t = simulate_forward(rows, day_map, sorted_dates, 0.5, 0.5,
                              regime_filter="TRENDING", only_direction=d)
        print(fmt(f"  {label}", *stats(t)))

    # ── Section I: Monthly P&L on forward balanced days ─────────────────────
    print("\n" + "─"*78)
    print("  I. MONTHLY P&L — FORWARD BALANCED DAYS (stop 0.5, target 0.5)")
    print("─"*78)
    all_t = simulate_forward(rows, day_map, sorted_dates, 0.5, 0.5, regime_filter="BALANCED")
    monthly = {}
    for t in all_t:
        m = t["date"][:7]
        monthly[m] = monthly.get(m, 0) + t["net_pnl"]
    pos = sum(1 for v in monthly.values() if v >= 0)
    print(f"  Profitable months: {pos}/{len(monthly)}\n")
    for m in sorted(monthly):
        bar_str = "█" * max(0, int(abs(monthly[m])/50))
        sign = "+" if monthly[m] >= 0 else " "
        print(f"    {m}  {sign}${monthly[m]:6.0f}  {bar_str}")

    print()
    print("─"*78)
    print("  INTERPRETATION GUIDE")
    print("─"*78)
    print("  If forward WR ≈ lookahead WR (81%) → edge is real, regime is predictive")
    print("  If forward WR ≈ baseline (51%)     → edge was 100% lookahead artefact")
    print("  If forward WR is between (55–70%)  → partial predictive value, worth refining")
    print()


if __name__ == "__main__":
    main()
