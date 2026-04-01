"""
EDGE 1 REFINEMENT: SHORT ±1SD after Prior-Day TRENDING

Builds on confirmed finding: prior-day TRENDING → SHORT ±1SD has 57% WR overall,
rising to 60–75% in specific time windows.

Goal: arrive at a single well-defined, tradeable specification by:
  A. Prior trend direction × time window matrix (the key 2D sweep)
  B. Stacking the two best windows into a single session plan
  C. Adding ATR regime filter (volatility context)
  D. Adding prior-day magnitude filter (how hard did it trend?)
  E. Adding consecutive streak filter (momentum persistence)
  F. Day-of-week interaction with best time windows
  G. Can we add LONG entries on prior DOWN-trend days to increase frequency?
  H. Final candidate specification: WR, PnL, drawdown, expectancy
  I. Monthly P&L on final candidate
  J. Quarterly stability — does edge hold across all 8 quarters?
  K. Trade-by-trade profile: avg win, avg loss, max streak
"""
from __future__ import annotations
import sys, math, datetime
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
    return (f"  {label:<65} n={n:>4}  WR={wr:.0%}  "
            f"P&L=${pnl:>8,.0f}  PF={pf:.2f}  p={p:.3f}  {sig}")

def add_daily_atr(df):
    daily = (df.group_by("date")
               .agg((pl.col("high").max()-pl.col("low").min()).alias("dr"))
               .sort("date")
               .with_columns(pl.col("dr").rolling_mean(window_size=10,min_samples=1).alias("atr")))
    return df.join(daily.select(["date","atr"]), on="date", how="left")

def compute_day_metrics(bars):
    if len(bars) < 10: return None
    closes = [b["close"] for b in bars]
    vwaps  = [b["vwap"]  for b in bars]
    vm1s   = [b["vwap_minus1"] for b in bars]
    vp1s   = [b["vwap_plus1"]  for b in bars]
    highs  = [b["high"] for b in bars]
    lows   = [b["low"]  for b in bars]
    n_bars = len(bars)
    above_pct      = sum(1 for c,v in zip(closes,vwaps) if c>=v)/n_bars
    daily_range    = max(highs)-min(lows)
    vwap_slope     = abs(vwaps[-1]-vwaps[0])/daily_range if daily_range>0 else 1.0
    directionality = abs(closes[-1]-closes[0])/daily_range if daily_range>0 else 1.0
    in_sd1         = sum(1 for c,m1,p1 in zip(closes,vm1s,vp1s) if m1<=c<=p1)/n_bars
    return {
        "above_pct": above_pct, "vwap_slope": vwap_slope,
        "directionality": directionality, "in_sd1": in_sd1,
        "daily_range": daily_range,
        "net_move": closes[-1]-closes[0],
    }

def is_trending(m):
    v = 0
    if 0.35 <= m["above_pct"] <= 0.65: v += 1
    if m["vwap_slope"] < 0.25:         v += 1
    if m["directionality"] < 0.40:     v += 1
    if m["in_sd1"] > 0.70:             v += 1
    return v < 3

def simulate(rows, day_map, sorted_dates, stop_m, target_m,
             time_start=9*60, time_end=14*60+30,
             only_direction=None,
             prior_regime=None,
             prior_direction=None,
             min_prior_dir=0.0, max_prior_dir=1.0,
             min_atr=0.0, max_atr=9999.0,
             streak_min=1,
             dow_filter=None,
             exclude_windows=None):   # list of (ts, te) to exclude
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
        atr = bar["atr"]
        if atr < min_atr or atr > max_atr: continue

        t_ct = bar["time_ct_minutes"]
        if t_ct < time_start or t_ct >= time_end: continue
        if exclude_windows:
            skip = False
            for ets, ete in exclude_windows:
                if ets <= t_ct < ete: skip = True; break
            if skip: continue

        date_str = str(bar["date"])
        date_idx = sorted_dates.index(date_str) if date_str in sorted_dates else -1
        if date_idx < 1: continue

        if dow_filter is not None:
            try:
                if datetime.date.fromisoformat(date_str).weekday() not in dow_filter: continue
            except Exception: pass

        prior_date = sorted_dates[date_idx - 1]
        pm = date_metrics.get(prior_date)
        if pm is None: continue

        if prior_regime == "TRENDING" and not is_trending(pm): continue
        if prior_regime == "BALANCED" and is_trending(pm):     continue
        if prior_direction == "UP"   and pm["net_move"] <= 0: continue
        if prior_direction == "DOWN" and pm["net_move"] >= 0: continue
        if pm["directionality"] < min_prior_dir: continue
        if pm["directionality"] > max_prior_dir: continue

        if streak_min > 1:
            streak = 0
            j = date_idx - 1
            while j >= 0:
                mj = date_metrics.get(sorted_dates[j])
                if mj and is_trending(mj): streak += 1; j -= 1
                else: break
            if streak < streak_min: continue

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
            try:
                dow = datetime.date.fromisoformat(date_str).weekday()
            except Exception:
                dow = -1
            trades.append({
                "date": date_str, "direction": direction,
                "outcome": outcome, "net_pnl": round(pnl,2),
                "prior_direction": "UP" if pm["net_move"]>0 else "DOWN",
                "prior_dir_val": round(pm["directionality"],3),
                "atr": round(atr,2), "dow": dow,
                "t_ct": t_ct,
            })
    return trades

def trade_profile(trades, label=""):
    """Print detailed trade profile: drawdown, streaks, expectancy."""
    if not trades: return
    w = [t for t in trades if t["outcome"]=="WIN"]
    l = [t for t in trades if t["outcome"]=="LOSS"]
    n = len(w)+len(l)
    if n == 0: return
    avg_win  = sum(t["net_pnl"] for t in w)/len(w) if w else 0
    avg_loss = sum(t["net_pnl"] for t in l)/len(l) if l else 0
    expectancy = (len(w)/n*avg_win + len(l)/n*avg_loss) if n else 0

    # Max drawdown
    sorted_t = sorted(trades, key=lambda x: x["date"])
    equity = 0; peak = 0; max_dd = 0
    for t in sorted_t:
        equity += t["net_pnl"]
        peak = max(peak, equity)
        max_dd = min(max_dd, equity - peak)

    # Streaks
    max_win_s = max_loss_s = cur_w = cur_l = 0
    for t in sorted_t:
        if t["outcome"]=="WIN":   cur_w+=1; cur_l=0
        elif t["outcome"]=="LOSS": cur_l+=1; cur_w=0
        max_win_s  = max(max_win_s,  cur_w)
        max_loss_s = max(max_loss_s, cur_l)

    if label: print(f"\n  {label}")
    print(f"    Trades: {n}  |  WR: {len(w)/n:.0%}  |  Net P&L: ${sum(t['net_pnl'] for t in trades):,.0f}")
    print(f"    Avg win: ${avg_win:.2f}  |  Avg loss: ${avg_loss:.2f}  |  Expectancy: ${expectancy:.2f}/trade")
    print(f"    Max drawdown: ${max_dd:,.0f}  |  Max win streak: {max_win_s}  |  Max loss streak: {max_loss_s}")
    print(f"    Annual trades: ~{n/2:.0f}/yr  |  Est. annual P&L (1 MES): ${sum(t['net_pnl'] for t in trades)/2:,.0f}/yr")


def main():
    print("\n" + "="*80)
    print("  EDGE 1 REFINEMENT: SHORT ±1SD AFTER PRIOR-DAY TRENDING")
    print("="*80)
    print("  Goal: define a single tradeable specification from the confirmed edge.\n")

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

    # ── Section A: Prior trend direction × time window matrix ─────────────────
    print("─"*80)
    print("  A. PRIOR TREND DIRECTION × TIME WINDOW MATRIX (stop 0.5×, tgt 0.5×, SHORT only)")
    print("─"*80)
    print(f"  {'Window':<30} {'All trending':>22} {'Prior UP-trend':>22} {'Prior DOWN-trend':>22}")
    print(f"  {'-'*28} {'-'*22} {'-'*22} {'-'*22}")
    windows = [
        ("09:00–10:00 (first hour)",   9*60,     10*60),
        ("10:00–11:00",               10*60,     11*60),
        ("10:00–11:30",               10*60,     11*60+30),
        ("10:00–12:00",               10*60,     12*60),
        ("11:00–13:00",               11*60,     13*60),
        ("11:30–13:00 (lunch)",       11*60+30,  13*60),
        ("13:00–14:30 (afternoon)",   13*60,     14*60+30),
        ("09:00–14:30 (full day)",     9*60,     14*60+30),
    ]
    for label, ts, te in windows:
        t_all  = simulate(rows, day_map, sorted_dates, 0.5, 0.5, ts, te,
                          only_direction="SHORT", prior_regime="TRENDING")
        t_up   = simulate(rows, day_map, sorted_dates, 0.5, 0.5, ts, te,
                          only_direction="SHORT", prior_regime="TRENDING", prior_direction="UP")
        t_down = simulate(rows, day_map, sorted_dates, 0.5, 0.5, ts, te,
                          only_direction="SHORT", prior_regime="TRENDING", prior_direction="DOWN")
        def cell(t):
            sa = stats(t); return f"n={sa[0]:>3} WR={sa[2]:.0%} p={sa[5]:.3f} {sa[6]}"
        print(f"  {label:<30} {cell(t_all):>22} {cell(t_up):>22} {cell(t_down):>22}")

    # ── Section B: Two-window session plan ────────────────────────────────────
    print("\n" + "─"*80)
    print("  B. STACKED SESSION PLAN — COMBINING BEST WINDOWS")
    print("     Idea: trade 10:00–13:00 only (skipping open volatility + post-3pm)")
    print("─"*80)
    windows_b = [
        ("10:00–13:00 (combined best)",          10*60,   13*60),
        ("10:00–13:00 prior UP only",             10*60,   13*60),
        ("10:00–13:00 prior DOWN only",           10*60,   13*60),
        ("10:00–14:30 skip 09:00-10:00",         10*60,   14*60+30),
        ("09:00–14:30 skip lunch (no 11-13)",    None,    None),   # use exclude
    ]
    t = simulate(rows, day_map, sorted_dates, 0.5, 0.5, 10*60, 13*60,
                 only_direction="SHORT", prior_regime="TRENDING")
    print(fmt("  10:00–13:00 (combined best window)", *stats(t)))
    t = simulate(rows, day_map, sorted_dates, 0.5, 0.5, 10*60, 13*60,
                 only_direction="SHORT", prior_regime="TRENDING", prior_direction="UP")
    print(fmt("  10:00–13:00 prior UP-trend only", *stats(t)))
    t = simulate(rows, day_map, sorted_dates, 0.5, 0.5, 10*60, 13*60,
                 only_direction="SHORT", prior_regime="TRENDING", prior_direction="DOWN")
    print(fmt("  10:00–13:00 prior DOWN-trend only", *stats(t)))
    t = simulate(rows, day_map, sorted_dates, 0.5, 0.5, 10*60, 14*60+30,
                 only_direction="SHORT", prior_regime="TRENDING")
    print(fmt("  10:00–14:30 (full day minus open)", *stats(t)))
    t = simulate(rows, day_map, sorted_dates, 0.5, 0.5, 9*60, 14*60+30,
                 only_direction="SHORT", prior_regime="TRENDING",
                 exclude_windows=[(9*60, 10*60)])
    print(fmt("  Skip 09:00-10:00 only", *stats(t)))

    # ── Section C: ATR regime filter ─────────────────────────────────────────
    print("\n" + "─"*80)
    print("  C. ATR REGIME — DOES VOLATILITY LEVEL MATTER? (10:00–13:00, SHORT, prior trending)")
    print("     10-day rolling ATR of daily range. Higher ATR = more volatile session.")
    print("─"*80)
    # Compute ATR percentiles first
    all_atrs = [r["atr"] for r in rows if r.get("atr") and r["atr"]>0]
    all_atrs.sort()
    p25 = all_atrs[len(all_atrs)//4]
    p50 = all_atrs[len(all_atrs)//2]
    p75 = all_atrs[3*len(all_atrs)//4]
    print(f"  ATR distribution: p25={p25:.1f}  p50={p50:.1f}  p75={p75:.1f} (points)")
    print()
    buckets = [
        (0,    p25,  f"Low vol   (ATR < {p25:.0f} pts)"),
        (p25,  p50,  f"Mid-low   ({p25:.0f}–{p50:.0f} pts)"),
        (p50,  p75,  f"Mid-high  ({p50:.0f}–{p75:.0f} pts)"),
        (p75,  9999, f"High vol  (ATR > {p75:.0f} pts)"),
    ]
    for lo, hi, label in buckets:
        t = simulate(rows, day_map, sorted_dates, 0.5, 0.5, 10*60, 13*60,
                     only_direction="SHORT", prior_regime="TRENDING",
                     min_atr=lo, max_atr=hi)
        print(fmt(f"  {label}", *stats(t)))

    # ── Section D: Prior magnitude stacked on best window ────────────────────
    print("\n" + "─"*80)
    print("  D. PRIOR TREND MAGNITUDE × WINDOW (10:00–13:00, SHORT, prior trending)")
    print("─"*80)
    buckets_d = [
        (0.0,  0.30, "Weak prior trend    (dir < 0.30)"),
        (0.30, 0.50, "Moderate            (0.30–0.50)"),
        (0.50, 0.70, "Strong              (0.50–0.70)"),
        (0.70, 1.01, "Very strong         (dir > 0.70)"),
        (0.25, 0.65, "Mid-range only      (0.25–0.65) ← sweet spot?"),
    ]
    for lo, hi, label in buckets_d:
        t = simulate(rows, day_map, sorted_dates, 0.5, 0.5, 10*60, 13*60,
                     only_direction="SHORT", prior_regime="TRENDING",
                     min_prior_dir=lo, max_prior_dir=hi)
        print(fmt(f"  {label}", *stats(t)))

    # ── Section E: Streak filter ──────────────────────────────────────────────
    print("\n" + "─"*80)
    print("  E. CONSECUTIVE TRENDING STREAK FILTER (10:00–13:00, SHORT)")
    print("─"*80)
    for streak, label in [(1,"Any prior trending (streak ≥ 1)"),
                           (2,"≥ 2 consecutive trending days"),
                           (3,"≥ 3 consecutive trending days"),
                           (4,"≥ 4 consecutive trending days")]:
        t = simulate(rows, day_map, sorted_dates, 0.5, 0.5, 10*60, 13*60,
                     only_direction="SHORT", prior_regime="TRENDING",
                     streak_min=streak)
        print(fmt(f"  {label}", *stats(t)))

    # ── Section F: Day-of-week on best window ─────────────────────────────────
    print("\n" + "─"*80)
    print("  F. DAY OF WEEK (10:00–13:00, SHORT, prior trending)")
    print("─"*80)
    dow_names = ["Monday","Tuesday","Wednesday","Thursday","Friday"]
    for i, name in enumerate(dow_names):
        t = simulate(rows, day_map, sorted_dates, 0.5, 0.5, 10*60, 13*60,
                     only_direction="SHORT", prior_regime="TRENDING",
                     dow_filter=[i])
        print(fmt(f"  {name}", *stats(t)))
    print()
    # Best combos
    t = simulate(rows, day_map, sorted_dates, 0.5, 0.5, 10*60, 13*60,
                 only_direction="SHORT", prior_regime="TRENDING",
                 dow_filter=[0,1,2,3,4])  # all
    print(fmt("  All days", *stats(t)))
    t = simulate(rows, day_map, sorted_dates, 0.5, 0.5, 10*60, 13*60,
                 only_direction="SHORT", prior_regime="TRENDING",
                 dow_filter=[1,2,3])  # Tue-Thu
    print(fmt("  Tue–Thu only", *stats(t)))

    # ── Section G: Adding LONG from prior DOWN-trend days ────────────────────
    print("\n" + "─"*80)
    print("  G. ADDING LONG SIDE: prior DOWN-trend → LONG (10:00–13:00)")
    print("     Can we trade both directions to increase frequency without hurting WR?")
    print("─"*80)
    t_short_up   = simulate(rows, day_map, sorted_dates, 0.5, 0.5, 10*60, 13*60,
                             only_direction="SHORT", prior_regime="TRENDING", prior_direction="UP")
    t_long_down  = simulate(rows, day_map, sorted_dates, 0.5, 0.5, 10*60, 13*60,
                             only_direction="LONG",  prior_regime="TRENDING", prior_direction="DOWN")
    t_short_any  = simulate(rows, day_map, sorted_dates, 0.5, 0.5, 10*60, 13*60,
                             only_direction="SHORT", prior_regime="TRENDING")
    t_long_any   = simulate(rows, day_map, sorted_dates, 0.5, 0.5, 10*60, 13*60,
                             only_direction="LONG",  prior_regime="TRENDING")
    t_combined   = t_short_up + t_long_down
    t_combined_all = t_short_any + t_long_any
    print(fmt("  SHORT only (prior UP-trend)",        *stats(t_short_up)))
    print(fmt("  LONG only  (prior DOWN-trend)",      *stats(t_long_down)))
    print(fmt("  Combined: SHORT(up) + LONG(down)",   *stats(t_combined)))
    print()
    print(fmt("  SHORT only (any prior trending)",    *stats(t_short_any)))
    print(fmt("  LONG only  (any prior trending)",    *stats(t_long_any)))
    print(fmt("  Combined: SHORT + LONG (any trend)", *stats(t_combined_all)))

    # ── Section H: RR on the best defined candidate ───────────────────────────
    print("\n" + "─"*80)
    print("  H. RR SENSITIVITY — BEST CANDIDATE (10:00–13:00, SHORT, prior trending)")
    print("─"*80)
    for sm, tm in [(0.25,0.25),(0.5,0.5),(0.5,0.75),(0.75,0.75),(1.0,1.0)]:
        t = simulate(rows, day_map, sorted_dates, sm, tm, 10*60, 13*60,
                     only_direction="SHORT", prior_regime="TRENDING")
        print(fmt(f"  Stop {sm}×ATR  Target {tm}×ATR  (RR {tm/sm:.1f}:1)", *stats(t)))

    # ── Section I: Monthly P&L — final candidate ──────────────────────────────
    print("\n" + "─"*80)
    print("  I. MONTHLY P&L — FINAL CANDIDATE: SHORT 10:00–13:00, PRIOR TRENDING")
    print("─"*80)
    final = simulate(rows, day_map, sorted_dates, 0.5, 0.5, 10*60, 13*60,
                     only_direction="SHORT", prior_regime="TRENDING")
    monthly = {}
    for t in final:
        m = t["date"][:7]
        monthly[m] = monthly.get(m, 0) + t["net_pnl"]
    pos = sum(1 for v in monthly.values() if v >= 0)
    print(f"  Profitable months: {pos}/{len(monthly)}\n")
    for m in sorted(monthly):
        bar_str = "█" * max(0, int(abs(monthly[m])/30))
        sign = "+" if monthly[m] >= 0 else " "
        print(f"    {m}  {sign}${monthly[m]:6.0f}  {bar_str}")

    # ── Section J: Quarterly stability ────────────────────────────────────────
    print("\n" + "─"*80)
    print("  J. QUARTERLY STABILITY — FINAL CANDIDATE")
    print("─"*80)
    quarters = [
        ("2023-Q1", ["2023-01","2023-02","2023-03"]),
        ("2023-Q2", ["2023-04","2023-05","2023-06"]),
        ("2023-Q3", ["2023-07","2023-08","2023-09"]),
        ("2023-Q4", ["2023-10","2023-11","2023-12"]),
        ("2024-Q1", ["2024-01","2024-02","2024-03"]),
        ("2024-Q2", ["2024-04","2024-05","2024-06"]),
        ("2024-Q3", ["2024-07","2024-08","2024-09"]),
        ("2024-Q4", ["2024-10","2024-11","2024-12"]),
    ]
    for label, months in quarters:
        qt = [t for t in final if t["date"][:7] in months]
        print(fmt(f"  {label}", *stats(qt)))

    # ── Section K: Full trade profile ─────────────────────────────────────────
    print("\n" + "─"*80)
    print("  K. TRADE PROFILE — FINAL CANDIDATE")
    print("─"*80)
    trade_profile(final, "SHORT 10:00–13:00 on prior-trending days (1 MES)")

    # Also show UP-trend version
    final_up = simulate(rows, day_map, sorted_dates, 0.5, 0.5, 10*60, 13*60,
                        only_direction="SHORT", prior_regime="TRENDING",
                        prior_direction="UP")
    trade_profile(final_up, "SHORT 10:00–13:00, prior UP-trend only (1 MES)")

    # ── Section L: Comprehensive filter stack — best candidate overall ─────────
    print("\n" + "─"*80)
    print("  L. FINAL FILTER STACK SWEEP — searching for highest WR with n≥50")
    print("─"*80)
    stacks = [
        ("Baseline: SHORT + prior trending + 10-13",
         {"only_direction":"SHORT","prior_regime":"TRENDING",
          "time_start":10*60,"time_end":13*60}),
        ("+ prior UP-trend only",
         {"only_direction":"SHORT","prior_regime":"TRENDING","prior_direction":"UP",
          "time_start":10*60,"time_end":13*60}),
        ("+ prior DOWN-trend only",
         {"only_direction":"SHORT","prior_regime":"TRENDING","prior_direction":"DOWN",
          "time_start":10*60,"time_end":13*60}),
        ("+ streak ≥2",
         {"only_direction":"SHORT","prior_regime":"TRENDING","streak_min":2,
          "time_start":10*60,"time_end":13*60}),
        ("+ streak ≥3",
         {"only_direction":"SHORT","prior_regime":"TRENDING","streak_min":3,
          "time_start":10*60,"time_end":13*60}),
        ("+ mid ATR (p25–p75)",
         {"only_direction":"SHORT","prior_regime":"TRENDING",
          "time_start":10*60,"time_end":13*60,"min_atr":float(p25),"max_atr":float(p75)}),
        ("+ prior dir 0.25–0.65",
         {"only_direction":"SHORT","prior_regime":"TRENDING",
          "time_start":10*60,"time_end":13*60,"min_prior_dir":0.25,"max_prior_dir":0.65}),
        ("+ streak≥2 + mid ATR",
         {"only_direction":"SHORT","prior_regime":"TRENDING","streak_min":2,
          "time_start":10*60,"time_end":13*60,"min_atr":float(p25),"max_atr":float(p75)}),
        ("+ prior UP + streak≥2",
         {"only_direction":"SHORT","prior_regime":"TRENDING","prior_direction":"UP",
          "streak_min":2,"time_start":10*60,"time_end":13*60}),
        ("+ prior UP + streak≥2 + mid ATR",
         {"only_direction":"SHORT","prior_regime":"TRENDING","prior_direction":"UP",
          "streak_min":2,"time_start":10*60,"time_end":13*60,
          "min_atr":float(p25),"max_atr":float(p75)}),
        ("Tue–Thu + prior trending + 10-13",
         {"only_direction":"SHORT","prior_regime":"TRENDING",
          "time_start":10*60,"time_end":13*60,"dow_filter":[1,2,3]}),
        ("Tue–Thu + prior UP + streak≥2",
         {"only_direction":"SHORT","prior_regime":"TRENDING","prior_direction":"UP",
          "streak_min":2,"time_start":10*60,"time_end":13*60,"dow_filter":[1,2,3]}),
    ]
    for label, kwargs in stacks:
        ts = kwargs.pop("time_start", 10*60)
        te = kwargs.pop("time_end",   13*60)
        t = simulate(rows, day_map, sorted_dates, 0.5, 0.5, ts, te, **kwargs)
        print(fmt(f"  {label}", *stats(t)))

    print()


if __name__ == "__main__":
    main()
