"""
OPTION 1: SD2 Extreme Fade + Volume Spike
Tests ±2SD cross-back signals filtered by volume confirmation.
Hypothesis: genuine exhaustion shows up as volume spike at the extreme.
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
    return (f"  {label:<52} n={n:>3}  WR={wr:.0%}  "
            f"P&L=${pnl:>8,.0f}  PF={pf:.2f}  p={p:.3f}  {sig}")

def add_daily_atr(df):
    daily = (df.group_by("date")
               .agg((pl.col("high").max()-pl.col("low").min()).alias("dr"))
               .sort("date")
               .with_columns(pl.col("dr").rolling_mean(window_size=10,min_samples=1).alias("atr")))
    return df.join(daily.select(["date","atr"]), on="date", how="left")

def add_rolling_avg_volume(df, window=20):
    """Rolling 20-bar average volume per bar (within session context)."""
    return df.with_columns(
        pl.col("volume").rolling_mean(window_size=window, min_samples=5).alias("avg_vol")
    )

def simulate_sd2_volume(rows, stop_m, target_m, vol_mult=1.0, trend_align=False,
                         time_start=9*60, time_end=14*60+30):
    trades = []
    n = len(rows)
    for i in range(1, n):
        bar = rows[i]; prev = rows[i-1]
        if bar.get("atr") is None or bar["atr"] == 0: continue
        t_ct = bar["time_ct_minutes"]
        if t_ct < time_start or t_ct >= time_end: continue

        atr  = bar["atr"]
        vm2  = bar["vwap_minus2"]; vp2 = bar["vwap_plus2"]
        vm1  = bar["vwap_minus1"]; vp1 = bar["vwap_plus1"]
        above = bar["close"] >= bar["vwap"]
        vol   = bar.get("volume", 0); avg_vol = bar.get("avg_vol", vol) or vol

        for direction, triggered in [
            ("LONG",  prev["close"] < vm2 and bar["close"] >= vm2),
            ("SHORT", prev["close"] > vp2 and bar["close"] <= vp2),
        ]:
            if not triggered: continue
            if trend_align:
                if direction=="LONG" and not above: continue
                if direction=="SHORT" and above:    continue
            # Volume filter
            if avg_vol > 0 and vol < vol_mult * avg_vol: continue

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

            pnl = ((ep-entry) if direction=="LONG" else (entry-ep))/MES_TICK*TICK_VAL - COMMISSION
            trades.append({"date":str(bar["date"]),"direction":direction,
                            "outcome":outcome,"net_pnl":round(pnl,2),
                            "vol_ratio": round(vol/avg_vol,2) if avg_vol else 0})
    return trades

def main():
    print("\n" + "="*75)
    print("  OPTION 1: SD2 EXTREME FADE + VOLUME SPIKE ANALYSIS")
    print("="*75)

    print("Loading 2yr MES data...")
    df = load_ohlcv(start_date="2023-01-01", end_date="2025-01-01")
    df = add_daily_vwap(df)
    df = add_daily_atr(df)
    df = add_rolling_avg_volume(df, window=20)
    df = df.with_columns((pl.col("close") >= pl.col("vwap")).alias("above_vwap"))
    rows = df.to_dicts()
    print(f"{len(df):,} bars  |  {df['date'].n_unique()} trading days\n")

    # ── Section A: SD2 baseline by RR ────────────────────────────────────────
    print("─"*75)
    print("  A. SD2 SIGNAL — RR SENSITIVITY (no volume filter)")
    print("─"*75)
    for sm, tm in [(0.25,0.25),(0.5,0.5),(0.5,0.75),(0.5,1.0),(0.75,0.75),(1.0,1.0)]:
        t = simulate_sd2_volume(rows, sm, tm, vol_mult=0)
        print(fmt(f"  Stop {sm}×ATR  Target {tm}×ATR  (RR {tm/sm:.1f}:1)", *stats(t)))

    # ── Section B: Volume threshold sensitivity ───────────────────────────────
    print("\n" + "─"*75)
    print("  B. SD2 — VOLUME THRESHOLD TEST (stop=0.5×, target=0.5×)")
    print("─"*75)
    for vm in [0, 0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0]:
        label = f"  Vol >= {vm:.2f}× avg" if vm > 0 else "  No volume filter"
        t = simulate_sd2_volume(rows, 0.5, 0.5, vol_mult=vm)
        print(fmt(label, *stats(t)))

    # ── Section C: Direction breakdown ───────────────────────────────────────
    print("\n" + "─"*75)
    print("  C. SD2 — DIRECTION BREAKDOWN (best RR 1:1, vol >= 1.0×)")
    print("─"*75)
    all_t = simulate_sd2_volume(rows, 0.5, 0.5, vol_mult=1.0)
    longs  = [t for t in all_t if t["direction"]=="LONG"]
    shorts = [t for t in all_t if t["direction"]=="SHORT"]
    print(fmt("  All directions", *stats(all_t)))
    print(fmt("  Longs only",     *stats(longs)))
    print(fmt("  Shorts only",    *stats(shorts)))

    # ── Section D: Trend-aligned ──────────────────────────────────────────────
    print("\n" + "─"*75)
    print("  D. SD2 — TREND ALIGNED (above VWAP=LONG only, below=SHORT only)")
    print("─"*75)
    for vm in [0, 1.0, 1.5, 2.0]:
        for sm, tm in [(0.5, 0.5), (0.5, 0.75)]:
            label = f"  Vol>={vm}×  Stop{sm}  Tgt{tm}"
            t = simulate_sd2_volume(rows, sm, tm, vol_mult=vm, trend_align=True)
            print(fmt(label, *stats(t)))

    # ── Section E: Time of day breakdown ─────────────────────────────────────
    print("\n" + "─"*75)
    print("  E. SD2 — TIME OF DAY BREAKDOWN (1:1 RR, vol >= 1.0×)")
    print("─"*75)
    windows = [
        ("08:30–10:00 CT (open volatility)", 8*60+30, 10*60),
        ("10:00–11:30 CT (mid morning)",     10*60,   11*60+30),
        ("11:30–13:00 CT (lunch)",           11*60+30,13*60),
        ("13:00–14:30 CT (afternoon)",       13*60,   14*60+30),
    ]
    for label, ts, te in windows:
        t = simulate_sd2_volume(rows, 0.5, 0.5, vol_mult=1.0, time_start=ts, time_end=te)
        print(fmt(f"  {label}", *stats(t)))

    # ── Section F: Yearly breakdown ───────────────────────────────────────────
    print("\n" + "─"*75)
    print("  F. SD2 — YEARLY BREAKDOWN (1:1 RR, vol >= 1.0×)")
    print("─"*75)
    all_t = simulate_sd2_volume(rows, 0.5, 0.5, vol_mult=1.0)
    for yr in ["2023", "2024"]:
        yt = [t for t in all_t if t["date"].startswith(yr)]
        print(fmt(f"  {yr}", *stats(yt)))

    # ── Section G: High vol ratio trades ─────────────────────────────────────
    print("\n" + "─"*75)
    print("  G. WIN/LOSS BREAKDOWN BY VOLUME RATIO (all SD2 trades, 1:1 RR)")
    print("─"*75)
    all_t = simulate_sd2_volume(rows, 0.5, 0.5, vol_mult=0)
    buckets = [(0,1,"<1.0× (below avg)"),(1,1.5,"1.0–1.5×"),(1.5,2.5,"1.5–2.5×"),(2.5,99,"2.5×+ (spike)")]
    for lo, hi, label in buckets:
        bt = [t for t in all_t if lo <= t["vol_ratio"] < hi]
        if bt:
            print(fmt(f"  Vol {label}", *stats(bt)))

    print()

if __name__ == "__main__":
    main()
