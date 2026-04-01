"""
TEST: Lunchtime SD2 Fade (11:30–13:00 CT)

Origin: test_sd2_volume.py Section E found that ±2SD cross-back signals
during 11:30–13:00 CT produce 64% WR, n=135, p=0.001.

Hypothesis: During the lunch window (low liquidity, narrow range), price that
has extended to ±2SD is statistically more likely to snap back because:
  1. Institutional desks reduce activity — thin book = easier reversal
  2. Morning trend has typically exhausted by 11:30
  3. Volume dries up, so SD bands effectively widen relative to actual movement

This test dissects:
  A. Time window sensitivity (where does the edge start and stop?)
  B. RR sensitivity within the lunch window
  C. Direction breakdown (long vs short)
  D. Volume interaction (does volume spike help or hurt at lunch?)
  E. Prior-day regime interaction (does TRENDING prior day improve lunch SD2?)
  F. SD band level (SD2 vs SD1 at lunch — does SD1 also have edge?)
  G. Combining lunch SD2 with trend alignment
  H. Monthly P&L and yearly stability
  I. Day-of-week breakdown
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

def add_rolling_avg_volume(df, window=20):
    return df.with_columns(
        pl.col("volume").rolling_mean(window_size=window, min_samples=5).alias("avg_vol")
    )

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

    above_pct      = sum(1 for c, v in zip(closes, vwaps) if c >= v) / n_bars
    daily_range    = max(highs) - min(lows)
    vwap_change    = abs(vwaps[-1] - vwaps[0])
    vwap_slope     = vwap_change / daily_range if daily_range > 0 else 1.0
    directionality = abs(closes[-1] - closes[0]) / daily_range if daily_range > 0 else 1.0
    in_sd1         = sum(1 for c, m1, p1 in zip(closes, vm1s, vp1s) if m1 <= c <= p1) / n_bars
    net_move       = closes[-1] - closes[0]

    return {
        "above_pct": above_pct, "vwap_slope": vwap_slope,
        "directionality": directionality, "in_sd1": in_sd1,
        "daily_range": daily_range, "net_move": net_move,
    }

def is_trending(m, above_lo=0.35, above_hi=0.65, slope_thresh=0.25,
                direction_thresh=0.40, sd1_thresh=0.70):
    v = 0
    if above_lo <= m["above_pct"] <= above_hi: v += 1
    if m["vwap_slope"] < slope_thresh:         v += 1
    if m["directionality"] < direction_thresh: v += 1
    if m["in_sd1"] > sd1_thresh:               v += 1
    return v < 3

def simulate_sd2_lunch(rows, day_map, sorted_dates, stop_m, target_m,
                        time_start=11*60+30, time_end=13*60,
                        vol_mult=0.0,          # volume filter (0 = off)
                        only_direction=None,   # None / "LONG" / "SHORT"
                        trend_align=False,     # LONG only if above VWAP, SHORT only if below
                        prior_regime=None,     # None / "TRENDING" / "BALANCED"
                        use_sd1=False):        # test SD1 cross-back instead of SD2
    """
    Simulate SD2 (or SD1) cross-back fades in a given time window.
    Entry: price crosses back inside the band from outside.
    """
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

        # Prior-day regime filter
        if prior_regime is not None:
            date_idx = sorted_dates.index(date_str) if date_str in sorted_dates else -1
            if date_idx < 1: continue
            pm = date_metrics.get(sorted_dates[date_idx - 1])
            if pm is None: continue
            trending = is_trending(pm)
            if prior_regime == "TRENDING" and not trending: continue
            if prior_regime == "BALANCED" and trending:     continue

        atr     = bar["atr"]
        vm2     = bar["vwap_minus2"]; vp2 = bar["vwap_plus2"]
        vm1     = bar["vwap_minus1"]; vp1 = bar["vwap_plus1"]
        above   = bar["close"] >= bar["vwap"]
        vol     = bar.get("volume", 0)
        avg_vol = bar.get("avg_vol", vol) or vol

        if use_sd1:
            # SD1 cross-back: prev outside SD1, now inside
            signals = [
                ("LONG",  prev["close"] < vm1 and bar["close"] >= vm1),
                ("SHORT", prev["close"] > vp1 and bar["close"] <= vp1),
            ]
        else:
            # SD2 cross-back: prev outside SD2, now inside
            signals = [
                ("LONG",  prev["close"] < vm2 and bar["close"] >= vm2),
                ("SHORT", prev["close"] > vp2 and bar["close"] <= vp2),
            ]

        for direction, triggered in signals:
            if not triggered: continue
            if only_direction and direction != only_direction: continue
            if trend_align:
                if direction == "LONG"  and not above: continue
                if direction == "SHORT" and above:     continue
            if vol_mult > 0 and avg_vol > 0 and vol < vol_mult * avg_vol: continue

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
                "vol_ratio": round(vol/avg_vol, 2) if avg_vol else 0,
                "dow": bar.get("dow", "?"),
            })
    return trades


def main():
    print("\n" + "="*78)
    print("  TEST: LUNCHTIME SD2 FADE (11:30–13:00 CT)")
    print("="*78)
    print("  Origin: test_sd2_volume.py found 64% WR, n=135, p=0.001 in this window.")
    print("  Now dissecting: time precision, direction, volume, regime interaction.\n")

    print("Loading 2yr MES data...")
    df = load_ohlcv(start_date="2023-01-01", end_date="2025-01-01")
    df = add_daily_vwap(df)
    df = add_daily_atr(df)
    df = add_rolling_avg_volume(df, window=20)
    rows = df.to_dicts()
    print(f"{len(df):,} bars  |  {df['date'].n_unique()} trading days\n")

    day_map = defaultdict(list)
    for r in rows:
        day_map[str(r["date"])].append(r)
    sorted_dates = sorted(day_map.keys())

    # Add day-of-week to rows
    dow_map = {str(r["date"]): r.get("dow") for r in rows}

    # ── Section A: Time window precision ────────────────────────────────────
    print("─"*78)
    print("  A. TIME WINDOW PRECISION — WHERE DOES THE EDGE LIVE? (stop 0.5, tgt 0.5)")
    print("─"*78)
    windows = [
        ("All day baseline 09:00–14:30",   9*60,      14*60+30),
        ("Morning 09:00–11:00",             9*60,      11*60),
        ("Pre-lunch 10:30–11:30",          10*60+30,  11*60+30),
        ("Lunch open 11:00–12:00",         11*60,     12*60),
        ("Core lunch 11:30–12:30",         11*60+30,  12*60+30),
        ("Core lunch 11:30–13:00",         11*60+30,  13*60),
        ("Full lunch 11:00–13:00",         11*60,     13*60),
        ("Lunch end 12:00–13:00",          12*60,     13*60),
        ("Transition 12:30–13:30",         12*60+30,  13*60+30),
        ("Afternoon 13:00–14:30",          13*60,     14*60+30),
    ]
    for label, ts, te in windows:
        t = simulate_sd2_lunch(rows, day_map, sorted_dates, 0.5, 0.5,
                                time_start=ts, time_end=te)
        print(fmt(f"  {label}", *stats(t)))

    # ── Section B: RR sensitivity in core lunch window ───────────────────────
    print("\n" + "─"*78)
    print("  B. RR SENSITIVITY — CORE LUNCH 11:30–13:00")
    print("─"*78)
    for sm, tm in [(0.25,0.25),(0.5,0.5),(0.5,0.75),(0.5,1.0),(0.75,0.75),(0.75,1.0),(1.0,1.0)]:
        t = simulate_sd2_lunch(rows, day_map, sorted_dates, sm, tm)
        print(fmt(f"  Stop {sm}×ATR  Target {tm}×ATR  (RR {tm/sm:.1f}:1)", *stats(t)))

    # ── Section C: Direction breakdown ──────────────────────────────────────
    print("\n" + "─"*78)
    print("  C. DIRECTION BREAKDOWN — LUNCH SD2 (stop 0.5, tgt 0.5)")
    print("─"*78)
    for d, label in [(None,"Both"),(  "LONG","Long only"),("SHORT","Short only")]:
        t = simulate_sd2_lunch(rows, day_map, sorted_dates, 0.5, 0.5, only_direction=d)
        print(fmt(f"  {label}", *stats(t)))

    # ── Section D: Volume interaction ────────────────────────────────────────
    print("\n" + "─"*78)
    print("  D. VOLUME INTERACTION — LUNCH SD2 (stop 0.5, tgt 0.5)")
    print("─"*78)
    for vm, label in [(0.0,"No volume filter"),(0.5,"Vol ≥ 0.5× avg"),
                      (0.75,"Vol ≥ 0.75× avg"),(1.0,"Vol ≥ 1.0× avg"),
                      (1.5,"Vol ≥ 1.5× avg"),(2.0,"Vol ≥ 2.0× avg")]:
        t = simulate_sd2_lunch(rows, day_map, sorted_dates, 0.5, 0.5, vol_mult=vm)
        print(fmt(f"  {label}", *stats(t)))

    # Low volume filter (quiet lunch = better fade)
    print()
    all_t = simulate_sd2_lunch(rows, day_map, sorted_dates, 0.5, 0.5, vol_mult=0)
    low_vol  = [t for t in all_t if t["vol_ratio"] < 0.75]
    high_vol = [t for t in all_t if t["vol_ratio"] >= 0.75]
    print(fmt("  Low vol (ratio < 0.75×) — quiet lunch", *stats(low_vol)))
    print(fmt("  High vol (ratio ≥ 0.75×) — busy lunch", *stats(high_vol)))

    # ── Section E: Prior-day regime interaction ──────────────────────────────
    print("\n" + "─"*78)
    print("  E. PRIOR-DAY REGIME × LUNCH SD2 (stop 0.5, tgt 0.5)")
    print("─"*78)
    for regime, label in [(None,"All days"),(  "TRENDING","Prior-day TRENDING"),
                           ("BALANCED","Prior-day BALANCED")]:
        t = simulate_sd2_lunch(rows, day_map, sorted_dates, 0.5, 0.5, prior_regime=regime)
        print(fmt(f"  {label}", *stats(t)))

    print()
    # Cross direction × prior regime
    for regime, direction, label in [
        ("TRENDING","SHORT", "Prior TRENDING → SHORT lunch SD2"),
        ("TRENDING","LONG",  "Prior TRENDING → LONG lunch SD2"),
        ("BALANCED","SHORT", "Prior BALANCED → SHORT lunch SD2"),
        ("BALANCED","LONG",  "Prior BALANCED → LONG lunch SD2"),
    ]:
        t = simulate_sd2_lunch(rows, day_map, sorted_dates, 0.5, 0.5,
                                prior_regime=regime, only_direction=direction)
        print(fmt(f"  {label}", *stats(t)))

    # ── Section F: SD1 vs SD2 at lunch ──────────────────────────────────────
    print("\n" + "─"*78)
    print("  F. SD1 vs SD2 AT LUNCH — WHICH BAND HAS MORE EDGE? (stop 0.5, tgt 0.5)")
    print("─"*78)
    t_sd2 = simulate_sd2_lunch(rows, day_map, sorted_dates, 0.5, 0.5, use_sd1=False)
    t_sd1 = simulate_sd2_lunch(rows, day_map, sorted_dates, 0.5, 0.5, use_sd1=True)
    print(fmt("  SD2 cross-back (±2σ)", *stats(t_sd2)))
    print(fmt("  SD1 cross-back (±1σ)", *stats(t_sd1)))
    print()
    for d, label in [("LONG","LONG"),("SHORT","SHORT")]:
        t_sd2d = simulate_sd2_lunch(rows, day_map, sorted_dates, 0.5, 0.5,
                                     use_sd1=False, only_direction=d)
        t_sd1d = simulate_sd2_lunch(rows, day_map, sorted_dates, 0.5, 0.5,
                                     use_sd1=True, only_direction=d)
        print(fmt(f"  SD2 {label}", *stats(t_sd2d)))
        print(fmt(f"  SD1 {label}", *stats(t_sd1d)))

    # ── Section G: Best combination ──────────────────────────────────────────
    print("\n" + "─"*78)
    print("  G. BEST COMBO SWEEP — LUNCH 11:30–13:00 SD2 (stop 0.5, tgt 0.5)")
    print("─"*78)
    combos = [
        ("Baseline",                      {}, False),
        ("Short only",                    {"only_direction":"SHORT"}, False),
        ("Long only",                     {"only_direction":"LONG"}, False),
        ("Trend aligned",                 {"trend_align":True}, False),
        ("Short + prior TRENDING",        {"only_direction":"SHORT","prior_regime":"TRENDING"}, False),
        ("Short + prior TRENDING + low vol", {"only_direction":"SHORT","prior_regime":"TRENDING"}, True),
        ("All + prior TRENDING",          {"prior_regime":"TRENDING"}, False),
        ("Short + no vol filter",         {"only_direction":"SHORT","vol_mult":0.0}, False),
        ("Short + low vol (< 0.75×)",     {"only_direction":"SHORT"}, True),
    ]
    for label, kwargs, low_vol_filter in combos:
        t = simulate_sd2_lunch(rows, day_map, sorted_dates, 0.5, 0.5, **kwargs)
        if low_vol_filter:
            t = [x for x in t if x["vol_ratio"] < 0.75]
        print(fmt(f"  {label}", *stats(t)))

    # ── Section H: Monthly P&L ───────────────────────────────────────────────
    print("\n" + "─"*78)
    print("  H. MONTHLY P&L — LUNCH SD2 BASELINE (stop 0.5, tgt 0.5)")
    print("─"*78)
    all_t = simulate_sd2_lunch(rows, day_map, sorted_dates, 0.5, 0.5)
    monthly = {}
    for t in all_t:
        m = t["date"][:7]
        monthly[m] = monthly.get(m, 0) + t["net_pnl"]
    pos = sum(1 for v in monthly.values() if v >= 0)
    print(f"  Profitable months: {pos}/{len(monthly)}\n")
    for m in sorted(monthly):
        bar_str = "█" * max(0, int(abs(monthly[m])/30))
        sign = "+" if monthly[m] >= 0 else " "
        print(f"    {m}  {sign}${monthly[m]:6.0f}  {bar_str}")

    # ── Section I: Yearly stability ──────────────────────────────────────────
    print("\n" + "─"*78)
    print("  I. YEARLY BREAKDOWN — LUNCH SD2")
    print("─"*78)
    all_t = simulate_sd2_lunch(rows, day_map, sorted_dates, 0.5, 0.5)
    for yr in ["2023","2024"]:
        yt = [t for t in all_t if t["date"].startswith(yr)]
        print(fmt(f"  {yr}", *stats(yt)))

    print()


if __name__ == "__main__":
    main()
