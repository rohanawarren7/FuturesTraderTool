"""
TEST: Short ±1SD on Prior-Day TRENDING Days

Origin: test_regime_forward.py Section H found that SHORT ±1SD cross-backs
on days following a TRENDING prior day produce 57% WR, n=1189, p=0.000.

Hypothesis: After a trending day, residual directional positioning creates
asymmetric mean-reversion — shorts have more fuel than longs because strong
prior-day up-trends leave buyers extended, and prior-day down-trends leave
momentum that overshoots intraday.

This test dissects:
  A. Prior-day trend direction (up-trend vs down-trend)
  B. Short vs long breakdown at each prior-regime type
  C. RR sensitivity
  D. Time of day
  E. Prior-day magnitude (how far did it trend?)
  F. Multi-day trending streaks
  G. Combined: prior-trend direction + time window
  H. Monthly P&L
"""
from __future__ import annotations
import sys, math
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import polars as pl
from data.databento_provider import load_ohlcv, add_daily_vwap
from collections import defaultdict

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
    if n == 0: return 0, 0, 0.0, 0.0, 0.0, 1.0, "✗"
    wr = len(w)/n
    pnl = sum(t["net_pnl"] for t in trades)
    pf = abs(sum(t["net_pnl"] for t in w)/sum(t["net_pnl"] for t in l)) if l and w else 0
    p = pval(len(w), n) if n>=5 else 1.0
    sig = "✓ GO" if wr>=0.55 and p<0.05 and n>=30 else "~ EDGE" if wr>=0.50 and n>=15 else "✗"
    return n, len(w), wr, pnl, pf, p, sig

def fmt(label, n, wins, wr, pnl, pf, p, sig):
    return (f"  {label:<62} n={n:>4}  WR={wr:.0%}  "
            f"P&L=${pnl:>8,.0f}  PF={pf:.2f}  p={p:.3f}  {sig}")

def add_daily_atr(df):
    daily = (df.group_by("date")
               .agg((pl.col("high").max()-pl.col("low").min()).alias("dr"))
               .sort("date")
               .with_columns(pl.col("dr").rolling_mean(window_size=10,min_samples=1).alias("atr")))
    return df.join(daily.select(["date","atr"]), on="date", how="left")

def compute_day_metrics(bars: list) -> dict | None:
    if len(bars) < 10:
        return None
    closes = [b["close"] for b in bars]
    vwaps  = [b["vwap"]  for b in bars]
    vm1s   = [b["vwap_minus1"] for b in bars]
    vp1s   = [b["vwap_plus1"]  for b in bars]
    highs  = [b["high"] for b in bars]
    lows   = [b["low"]  for b in bars]
    n_bars = len(bars)

    above_pct     = sum(1 for c, v in zip(closes, vwaps) if c >= v) / n_bars
    daily_range   = max(highs) - min(lows)
    vwap_change   = abs(vwaps[-1] - vwaps[0])
    vwap_slope    = vwap_change / daily_range if daily_range > 0 else 1.0
    directionality = abs(closes[-1] - closes[0]) / daily_range if daily_range > 0 else 1.0
    in_sd1        = sum(1 for c, m1, p1 in zip(closes, vm1s, vp1s) if m1 <= c <= p1) / n_bars
    net_move      = closes[-1] - closes[0]   # + = up-trend, - = down-trend
    open_price    = closes[0]

    return {
        "above_pct":      above_pct,
        "vwap_slope":     vwap_slope,
        "directionality": directionality,
        "in_sd1":         in_sd1,
        "daily_range":    daily_range,
        "net_move":       net_move,
        "open_price":     open_price,
    }

def regime_votes(m, above_lo=0.35, above_hi=0.65, slope_thresh=0.25,
                 direction_thresh=0.40, sd1_thresh=0.70):
    v = 0
    if above_lo <= m["above_pct"] <= above_hi: v += 1
    if m["vwap_slope"] < slope_thresh:         v += 1
    if m["directionality"] < direction_thresh: v += 1
    if m["in_sd1"] > sd1_thresh:               v += 1
    return v

def is_trending(m):
    return regime_votes(m) < 3

def simulate(rows, day_map, sorted_dates, stop_m, target_m,
             prior_regime=None,         # None=all, "TRENDING", "BALANCED"
             prior_direction=None,      # None=all, "UP", "DOWN"
             only_direction=None,       # None=both, "LONG", "SHORT"
             time_start=9*60, time_end=14*60+30,
             min_prior_directionality=0.0,   # prior day moved at least X% of range
             max_prior_directionality=1.0,
             streak_min=1):             # require at least N prior trending days
    """Walk-forward sim using prior-day regime to filter today's trades."""

    # Pre-compute all day metrics
    date_metrics = {}
    for d in sorted_dates:
        m = compute_day_metrics(day_map.get(d, []))
        if m is not None:
            date_metrics[d] = m

    trades = []
    n = len(rows)

    for i in range(1, n):
        bar = rows[i]; prev = rows[i-1]
        if bar.get("atr") is None or bar["atr"] == 0: continue
        t_ct = bar["time_ct_minutes"]
        if t_ct < time_start or t_ct >= time_end: continue

        date_str = str(bar["date"])
        date_idx = sorted_dates.index(date_str) if date_str in sorted_dates else -1
        if date_idx < 1: continue

        prior_date = sorted_dates[date_idx - 1]
        pm = date_metrics.get(prior_date)
        if pm is None: continue

        # Prior regime filter
        if prior_regime == "TRENDING" and not is_trending(pm): continue
        if prior_regime == "BALANCED" and is_trending(pm):     continue

        # Prior direction filter
        if prior_direction == "UP"   and pm["net_move"] <= 0: continue
        if prior_direction == "DOWN" and pm["net_move"] >= 0: continue

        # Prior directionality magnitude
        if pm["directionality"] < min_prior_directionality: continue
        if pm["directionality"] > max_prior_directionality: continue

        # Streak filter: require N consecutive prior trending days
        if streak_min > 1:
            streak = 0
            j = date_idx - 1
            while j >= 0:
                m_j = date_metrics.get(sorted_dates[j])
                if m_j and is_trending(m_j):
                    streak += 1
                    j -= 1
                else:
                    break
            if streak < streak_min: continue

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
            trades.append({
                "date": date_str,
                "direction": direction,
                "outcome": outcome,
                "net_pnl": round(pnl, 2),
                "prior_regime": "TRENDING" if is_trending(pm) else "BALANCED",
                "prior_direction": "UP" if pm["net_move"] > 0 else "DOWN",
                "prior_directionality": round(pm["directionality"], 3),
            })
    return trades


def main():
    print("\n" + "="*78)
    print("  TEST: SHORT ±1SD CROSS-BACK ON PRIOR-DAY TRENDING DAYS")
    print("="*78)
    print("  Origin: forward regime test found SHORT 57% WR (n=1189, p=0.000)")
    print("  Now dissecting: why, when, and how strong is the edge?\n")

    print("Loading 2yr MES data...")
    df = load_ohlcv(start_date="2023-01-01", end_date="2025-01-01")
    df = add_daily_vwap(df)
    df = add_daily_atr(df)
    rows = df.to_dicts()
    print(f"{len(df):,} bars  |  {df['date'].n_unique()} trading days\n")

    day_map = defaultdict(list)
    for r in rows:
        day_map[str(r["date"])].append(r)
    sorted_dates = sorted(day_map.keys())

    # ── Section A: Replicate finding + direction breakdown ───────────────────
    print("─"*78)
    print("  A. REPLICATE FINDING — PRIOR-DAY TRENDING: LONG vs SHORT (stop 0.5, tgt 0.5)")
    print("─"*78)
    for regime, label in [(None,"All days (baseline)"),
                          ("TRENDING","Prior-day TRENDING — both"),
                          ("BALANCED","Prior-day BALANCED — both")]:
        t = simulate(rows, day_map, sorted_dates, 0.5, 0.5, prior_regime=regime)
        print(fmt(label, *stats(t)))

    print()
    for regime, direction, label in [
        ("TRENDING","LONG",  "Prior-day TRENDING — LONG only"),
        ("TRENDING","SHORT", "Prior-day TRENDING — SHORT only"),
        ("BALANCED","LONG",  "Prior-day BALANCED — LONG only"),
        ("BALANCED","SHORT", "Prior-day BALANCED — SHORT only"),
    ]:
        t = simulate(rows, day_map, sorted_dates, 0.5, 0.5,
                     prior_regime=regime, only_direction=direction)
        print(fmt(label, *stats(t)))

    # ── Section B: Prior trend direction (up vs down) ────────────────────────
    print("\n" + "─"*78)
    print("  B. PRIOR-DAY TREND DIRECTION — UP-TRENDING vs DOWN-TRENDING")
    print("─"*78)
    combos = [
        ("TRENDING", "UP",   "LONG",  "Prior UP-trend  → LONG today"),
        ("TRENDING", "UP",   "SHORT", "Prior UP-trend  → SHORT today"),
        ("TRENDING", "DOWN", "LONG",  "Prior DOWN-trend → LONG today"),
        ("TRENDING", "DOWN", "SHORT", "Prior DOWN-trend → SHORT today"),
    ]
    for regime, pd_dir, td_dir, label in combos:
        t = simulate(rows, day_map, sorted_dates, 0.5, 0.5,
                     prior_regime=regime, prior_direction=pd_dir, only_direction=td_dir)
        print(fmt(f"  {label}", *stats(t)))

    # ── Section C: RR sensitivity on SHORT / prior-trending ─────────────────
    print("\n" + "─"*78)
    print("  C. RR SENSITIVITY — SHORT ON PRIOR-TRENDING DAYS")
    print("─"*78)
    for sm, tm in [(0.25,0.25),(0.5,0.5),(0.5,0.75),(0.5,1.0),(0.75,0.75),(0.75,1.0),(1.0,1.0)]:
        t = simulate(rows, day_map, sorted_dates, sm, tm,
                     prior_regime="TRENDING", only_direction="SHORT")
        print(fmt(f"  Stop {sm}×ATR  Target {tm}×ATR  (RR {tm/sm:.1f}:1)", *stats(t)))

    # ── Section D: Time of day ───────────────────────────────────────────────
    print("\n" + "─"*78)
    print("  D. TIME OF DAY — SHORT ON PRIOR-TRENDING DAYS (stop 0.5, tgt 0.5)")
    print("─"*78)
    windows = [
        ("09:00–10:00 CT (first hour)",     9*60,     10*60),
        ("09:00–10:30 CT",                  9*60,     10*60+30),
        ("09:00–11:00 CT",                  9*60,     11*60),
        ("10:00–11:30 CT",                  10*60,    11*60+30),
        ("10:00–12:00 CT",                  10*60,    12*60),
        ("11:00–13:00 CT",                  11*60,    13*60),
        ("11:30–13:00 CT (lunch)",          11*60+30, 13*60),
        ("13:00–14:30 CT (afternoon)",      13*60,    14*60+30),
        ("09:00–14:30 CT (full session)",   9*60,     14*60+30),
    ]
    for label, ts, te in windows:
        t = simulate(rows, day_map, sorted_dates, 0.5, 0.5,
                     prior_regime="TRENDING", only_direction="SHORT",
                     time_start=ts, time_end=te)
        print(fmt(f"  {label}", *stats(t)))

    # ── Section E: Prior-day magnitude (how much did it trend?) ─────────────
    print("\n" + "─"*78)
    print("  E. PRIOR-DAY DIRECTIONALITY MAGNITUDE — SHORT ON PRIOR TRENDING")
    print("     (directionality = |open-close| / daily_range, higher = stronger trend)")
    print("─"*78)
    buckets = [
        (0.0, 0.30, "Weak trend    (dir < 0.30)"),
        (0.30,0.50, "Moderate trend (0.30–0.50)"),
        (0.50,0.70, "Strong trend   (0.50–0.70)"),
        (0.70,1.01, "Very strong    (dir > 0.70)"),
    ]
    for lo, hi, label in buckets:
        t = simulate(rows, day_map, sorted_dates, 0.5, 0.5,
                     prior_regime="TRENDING", only_direction="SHORT",
                     min_prior_directionality=lo, max_prior_directionality=hi)
        print(fmt(f"  {label}", *stats(t)))

    # ── Section F: Consecutive trending streak ───────────────────────────────
    print("\n" + "─"*78)
    print("  F. TRENDING STREAK — SHORT AFTER N CONSECUTIVE TRENDING DAYS")
    print("─"*78)
    for streak, label in [(1,"≥1 prior trending day"),(2,"≥2 consecutive"),(3,"≥3 consecutive")]:
        t = simulate(rows, day_map, sorted_dates, 0.5, 0.5,
                     prior_regime="TRENDING", only_direction="SHORT",
                     streak_min=streak)
        print(fmt(f"  {label}", *stats(t)))

    # ── Section G: Best combo — prior direction × time window ───────────────
    print("\n" + "─"*78)
    print("  G. BEST COMBOS — PRIOR TREND DIRECTION × TIME WINDOW × DIRECTION")
    print("─"*78)
    combos = [
        ("UP-trend prior → SHORT 09:00-11:00",  "UP",   "SHORT", 9*60,     11*60),
        ("UP-trend prior → SHORT 10:00-12:00",  "UP",   "SHORT", 10*60,    12*60),
        ("UP-trend prior → SHORT full day",      "UP",   "SHORT", 9*60,     14*60+30),
        ("DOWN-trend prior → SHORT 09:00-11:00","DOWN",  "SHORT", 9*60,     11*60),
        ("DOWN-trend prior → SHORT 10:00-12:00","DOWN",  "SHORT", 10*60,    12*60),
        ("DOWN-trend prior → SHORT full day",   "DOWN",  "SHORT", 9*60,     14*60+30),
        ("UP-trend prior → LONG 09:00-11:00",   "UP",   "LONG",  9*60,     11*60),
        ("DOWN-trend prior → LONG 09:00-11:00", "DOWN",  "LONG",  9*60,     11*60),
    ]
    for label, pd_dir, td_dir, ts, te in combos:
        t = simulate(rows, day_map, sorted_dates, 0.5, 0.5,
                     prior_regime="TRENDING", prior_direction=pd_dir,
                     only_direction=td_dir, time_start=ts, time_end=te)
        print(fmt(f"  {label}", *stats(t)))

    # ── Section H: Monthly P&L — best candidate ─────────────────────────────
    print("\n" + "─"*78)
    print("  H. MONTHLY P&L — SHORT ON PRIOR-DAY TRENDING (stop 0.5, tgt 0.5)")
    print("─"*78)
    all_t = simulate(rows, day_map, sorted_dates, 0.5, 0.5,
                     prior_regime="TRENDING", only_direction="SHORT")
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

    # ── Section I: Yearly stability ──────────────────────────────────────────
    print("\n" + "─"*78)
    print("  I. YEARLY BREAKDOWN — SHORT ON PRIOR-DAY TRENDING")
    print("─"*78)
    all_t = simulate(rows, day_map, sorted_dates, 0.5, 0.5,
                     prior_regime="TRENDING", only_direction="SHORT")
    for yr in ["2023","2024"]:
        yt = [t for t in all_t if t["date"].startswith(yr)]
        print(fmt(f"  {yr}", *stats(yt)))

    print()


if __name__ == "__main__":
    main()
