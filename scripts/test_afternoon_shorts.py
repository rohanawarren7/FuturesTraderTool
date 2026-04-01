"""
TEST: Afternoon SHORT ±1SD on Prior-Trending Days (13:00–14:30 CT)

Origin: test_trending_day_shorts.py Section D found that SHORTs on prior-trending
days during 13:00–14:30 CT produce 75% WR, n=84, PF=3.20, p=0.000.

This is an extraordinary result for n=84 but needs stress-testing before trusting it.

Dissection plan:
  A. Time precision — where exactly in 13:00–14:30 does the edge live?
  B. RR sensitivity — does it survive wider targets?
  C. Prior trend direction (up vs down) — which matters more in afternoon?
  D. Prior trend magnitude — does stronger prior trend = better afternoon short?
  E. Day-of-week breakdown — is this a Monday/Friday effect?
  F. Market regime at time of trade — is price above/below VWAP when signal fires?
  G. Monthly P&L and yearly breakdown — is it consistent or a few outlier months?
  H. Comparison: afternoon LONG on prior-trending days (control)
  I. SD2 shorts at afternoon vs SD1 shorts — which band?
  J. Combined: prior UP-trend + afternoon + SHORT (cleanest candidate)
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
             time_start=13*60, time_end=14*60+30,
             only_direction=None,
             prior_regime=None,       # None / "TRENDING" / "BALANCED"
             prior_direction=None,    # None / "UP" / "DOWN"
             min_prior_dir=0.0,
             max_prior_dir=1.0,
             above_vwap_filter=None,  # None / True (above) / False (below)
             use_sd2=False,           # test ±2SD band instead of ±1SD
             dow_filter=None):        # None or list of weekday ints (0=Mon..4=Fri)

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

        # Day-of-week filter
        if dow_filter is not None:
            try:
                d_obj = datetime.date.fromisoformat(date_str)
                if d_obj.weekday() not in dow_filter: continue
            except Exception:
                pass

        prior_date = sorted_dates[date_idx - 1]
        pm = date_metrics.get(prior_date)
        if pm is None: continue

        # Prior regime
        if prior_regime == "TRENDING" and not is_trending(pm): continue
        if prior_regime == "BALANCED" and is_trending(pm):     continue

        # Prior direction
        if prior_direction == "UP"   and pm["net_move"] <= 0: continue
        if prior_direction == "DOWN" and pm["net_move"] >= 0: continue

        # Prior magnitude
        if pm["directionality"] < min_prior_dir: continue
        if pm["directionality"] > max_prior_dir: continue

        atr = bar["atr"]
        vm1 = bar["vwap_minus1"]; vp1 = bar["vwap_plus1"]
        vm2 = bar["vwap_minus2"]; vp2 = bar["vwap_plus2"]
        above = bar["close"] >= bar["vwap"]

        # Intraday VWAP position filter
        if above_vwap_filter is True  and not above: continue
        if above_vwap_filter is False and above:     continue

        if use_sd2:
            signals = [
                ("LONG",  prev["close"] < vm2 and bar["close"] >= vm2),
                ("SHORT", prev["close"] > vp2 and bar["close"] <= vp2),
            ]
        else:
            signals = [
                ("LONG",  prev["close"] < vm1 and bar["close"] >= vm1),
                ("SHORT", prev["close"] > vp1 and bar["close"] <= vp1),
            ]

        for direction, triggered in signals:
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
                dow_name = ["Mon","Tue","Wed","Thu","Fri"][dow]
            except Exception:
                dow_name = "?"
            trades.append({
                "date": date_str, "direction": direction,
                "outcome": outcome, "net_pnl": round(pnl,2),
                "above_vwap": above,
                "prior_direction": "UP" if pm["net_move"]>0 else "DOWN",
                "prior_dir_val": round(pm["directionality"],3),
                "dow": dow_name,
            })
    return trades


def main():
    print("\n" + "="*80)
    print("  TEST: AFTERNOON SHORT ±1SD — PRIOR TRENDING DAYS (13:00–14:30 CT)")
    print("="*80)
    print("  Origin: 75% WR, n=84, PF=3.20, p=0.000 — stress-testing for robustness.\n")

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

    # ── Section A: Time precision ────────────────────────────────────────────
    print("─"*80)
    print("  A. TIME PRECISION — WHERE IN THE AFTERNOON DOES THE EDGE LIVE?")
    print("     Baseline: SHORT on prior-trending day, stop 0.5×, target 0.5×")
    print("─"*80)
    windows = [
        ("Full session baseline 09:00–14:30",  9*60,     14*60+30),
        ("Pre-afternoon 12:00–13:00",          12*60,    13*60),
        ("Transition 12:30–13:30",             12*60+30, 13*60+30),
        ("Afternoon open 13:00–13:30",         13*60,    13*60+30),
        ("Early afternoon 13:00–13:45",        13*60,    13*60+45),
        ("Core afternoon 13:00–14:00",         13*60,    14*60),
        ("Full afternoon 13:00–14:30",         13*60,    14*60+30),
        ("Late afternoon 13:30–14:30",         13*60+30, 14*60+30),
        ("Close window 14:00–14:30",           14*60,    14*60+30),
    ]
    for label, ts, te in windows:
        t = simulate(rows, day_map, sorted_dates, 0.5, 0.5,
                     time_start=ts, time_end=te,
                     only_direction="SHORT", prior_regime="TRENDING")
        print(fmt(f"  {label}", *stats(t)))

    # ── Section B: RR sensitivity ─────────────────────────────────────────────
    print("\n" + "─"*80)
    print("  B. RR SENSITIVITY — AFTERNOON SHORT, PRIOR TRENDING (13:00–14:30)")
    print("─"*80)
    for sm, tm in [(0.25,0.25),(0.5,0.5),(0.5,0.75),(0.5,1.0),(0.75,0.75),(0.75,1.0),(1.0,1.0),(1.0,1.5)]:
        t = simulate(rows, day_map, sorted_dates, sm, tm,
                     only_direction="SHORT", prior_regime="TRENDING")
        print(fmt(f"  Stop {sm}×ATR  Target {tm}×ATR  (RR {tm/sm:.1f}:1)", *stats(t)))

    # ── Section C: Prior trend direction ─────────────────────────────────────
    print("\n" + "─"*80)
    print("  C. PRIOR TREND DIRECTION — UP vs DOWN (afternoon SHORT, 13:00–14:30)")
    print("─"*80)
    for pd_dir, label in [(None,"Any prior direction"),
                           ("UP","Prior UP-trend only"),
                           ("DOWN","Prior DOWN-trend only")]:
        t = simulate(rows, day_map, sorted_dates, 0.5, 0.5,
                     only_direction="SHORT", prior_regime="TRENDING",
                     prior_direction=pd_dir)
        print(fmt(f"  {label}", *stats(t)))
    # Control: LONG on same conditions
    print()
    for pd_dir, label in [(None,"LONG — any prior direction (control)"),
                           ("UP","LONG — prior UP-trend (control)"),
                           ("DOWN","LONG — prior DOWN-trend (control)")]:
        t = simulate(rows, day_map, sorted_dates, 0.5, 0.5,
                     only_direction="LONG", prior_regime="TRENDING",
                     prior_direction=pd_dir)
        print(fmt(f"  {label}", *stats(t)))

    # ── Section D: Prior magnitude ────────────────────────────────────────────
    print("\n" + "─"*80)
    print("  D. PRIOR-DAY MAGNITUDE — HOW MUCH DID IT TREND? (afternoon SHORT)")
    print("─"*80)
    buckets = [
        (0.0, 0.30, "Weak trend    (dir < 0.30)"),
        (0.30,0.50, "Moderate      (0.30–0.50)"),
        (0.50,0.70, "Strong        (0.50–0.70)"),
        (0.70,1.01, "Very strong   (dir > 0.70)"),
    ]
    for lo, hi, label in buckets:
        t = simulate(rows, day_map, sorted_dates, 0.5, 0.5,
                     only_direction="SHORT", prior_regime="TRENDING",
                     min_prior_dir=lo, max_prior_dir=hi)
        print(fmt(f"  {label}", *stats(t)))

    # ── Section E: Day of week ────────────────────────────────────────────────
    print("\n" + "─"*80)
    print("  E. DAY OF WEEK — AFTERNOON SHORT, PRIOR TRENDING")
    print("─"*80)
    dow_names = ["Monday","Tuesday","Wednesday","Thursday","Friday"]
    for i, name in enumerate(dow_names):
        t = simulate(rows, day_map, sorted_dates, 0.5, 0.5,
                     only_direction="SHORT", prior_regime="TRENDING",
                     dow_filter=[i])
        print(fmt(f"  {name}", *stats(t)))

    # ── Section F: Intraday VWAP position at signal ──────────────────────────
    print("\n" + "─"*80)
    print("  F. INTRADAY VWAP POSITION AT SIGNAL FIRE (afternoon SHORT)")
    print("     (is price above or below session VWAP when the ±1SD cross fires?)")
    print("─"*80)
    for vwap_pos, label in [(None,"Any VWAP position"),
                             (True, "Price ABOVE VWAP at signal"),
                             (False,"Price BELOW VWAP at signal")]:
        t = simulate(rows, day_map, sorted_dates, 0.5, 0.5,
                     only_direction="SHORT", prior_regime="TRENDING",
                     above_vwap_filter=vwap_pos)
        print(fmt(f"  {label}", *stats(t)))

    # ── Section G: SD1 vs SD2 in the afternoon ───────────────────────────────
    print("\n" + "─"*80)
    print("  G. SD1 vs SD2 — AFTERNOON SHORT, PRIOR TRENDING")
    print("─"*80)
    t_sd1 = simulate(rows, day_map, sorted_dates, 0.5, 0.5,
                     only_direction="SHORT", prior_regime="TRENDING", use_sd2=False)
    t_sd2 = simulate(rows, day_map, sorted_dates, 0.5, 0.5,
                     only_direction="SHORT", prior_regime="TRENDING", use_sd2=True)
    print(fmt("  ±1SD cross-back SHORT (prior trending, 13:00–14:30)", *stats(t_sd1)))
    print(fmt("  ±2SD cross-back SHORT (prior trending, 13:00–14:30)", *stats(t_sd2)))

    # ── Section H: Monthly P&L ────────────────────────────────────────────────
    print("\n" + "─"*80)
    print("  H. MONTHLY P&L — AFTERNOON SHORT, PRIOR TRENDING (stop 0.5, tgt 0.5)")
    print("─"*80)
    all_t = simulate(rows, day_map, sorted_dates, 0.5, 0.5,
                     only_direction="SHORT", prior_regime="TRENDING")
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

    # ── Section I: Yearly breakdown ───────────────────────────────────────────
    print("\n" + "─"*80)
    print("  I. YEARLY BREAKDOWN + QUARTERLY (afternoon SHORT, prior trending)")
    print("─"*80)
    all_t = simulate(rows, day_map, sorted_dates, 0.5, 0.5,
                     only_direction="SHORT", prior_regime="TRENDING")
    for yr in ["2023","2024"]:
        yt = [t for t in all_t if t["date"].startswith(yr)]
        print(fmt(f"  {yr}", *stats(yt)))
    print()
    quarters = [("2023-Q1",["2023-01","2023-02","2023-03"]),
                ("2023-Q2",["2023-04","2023-05","2023-06"]),
                ("2023-Q3",["2023-07","2023-08","2023-09"]),
                ("2023-Q4",["2023-10","2023-11","2023-12"]),
                ("2024-Q1",["2024-01","2024-02","2024-03"]),
                ("2024-Q2",["2024-04","2024-05","2024-06"]),
                ("2024-Q3",["2024-07","2024-08","2024-09"]),
                ("2024-Q4",["2024-10","2024-11","2024-12"])]
    for label, months in quarters:
        qt = [t for t in all_t if t["date"][:7] in months]
        print(fmt(f"  {label}", *stats(qt)))

    # ── Section J: Best combination ───────────────────────────────────────────
    print("\n" + "─"*80)
    print("  J. BEST COMBINATION SWEEP (13:00–14:30, stop 0.5, tgt 0.5)")
    print("─"*80)
    combos = [
        ("Baseline: SHORT + prior TRENDING",
         {"only_direction":"SHORT","prior_regime":"TRENDING"}),
        ("SHORT + prior UP-trend",
         {"only_direction":"SHORT","prior_regime":"TRENDING","prior_direction":"UP"}),
        ("SHORT + prior UP-trend + mid-strong (dir 0.30–0.70)",
         {"only_direction":"SHORT","prior_regime":"TRENDING","prior_direction":"UP",
          "min_prior_dir":0.30,"max_prior_dir":0.70}),
        ("SHORT + prior UP-trend + VWAP above at signal",
         {"only_direction":"SHORT","prior_regime":"TRENDING","prior_direction":"UP",
          "above_vwap_filter":True}),
        ("SHORT + prior UP-trend + VWAP below at signal",
         {"only_direction":"SHORT","prior_regime":"TRENDING","prior_direction":"UP",
          "above_vwap_filter":False}),
        ("SHORT + any prior trending + VWAP above",
         {"only_direction":"SHORT","prior_regime":"TRENDING","above_vwap_filter":True}),
        ("SHORT + any prior trending + VWAP below",
         {"only_direction":"SHORT","prior_regime":"TRENDING","above_vwap_filter":False}),
        ("SHORT + prior DOWN-trend + VWAP below",
         {"only_direction":"SHORT","prior_regime":"TRENDING","prior_direction":"DOWN",
          "above_vwap_filter":False}),
    ]
    for label, kwargs in combos:
        t = simulate(rows, day_map, sorted_dates, 0.5, 0.5, **kwargs)
        print(fmt(f"  {label}", *stats(t)))

    # ── Section K: Walk-forward distribution of wins ─────────────────────────
    print("\n" + "─"*80)
    print("  K. WIN STREAK / LOSS STREAK ANALYSIS — afternoon SHORT prior trending")
    print("─"*80)
    all_t_sorted = sorted(
        simulate(rows, day_map, sorted_dates, 0.5, 0.5,
                 only_direction="SHORT", prior_regime="TRENDING"),
        key=lambda x: x["date"]
    )
    max_win_streak = max_loss_streak = 0
    cur_win = cur_loss = 0
    for t in all_t_sorted:
        if t["outcome"] == "WIN":
            cur_win += 1; cur_loss = 0
        elif t["outcome"] == "LOSS":
            cur_loss += 1; cur_win = 0
        max_win_streak  = max(max_win_streak,  cur_win)
        max_loss_streak = max(max_loss_streak, cur_loss)

    wins_only  = [t for t in all_t_sorted if t["outcome"]=="WIN"]
    losses_only= [t for t in all_t_sorted if t["outcome"]=="LOSS"]
    print(f"  Total trades: {len(all_t_sorted)}")
    print(f"  Max consecutive wins:   {max_win_streak}")
    print(f"  Max consecutive losses: {max_loss_streak}")
    if wins_only:
        avg_win  = sum(t["net_pnl"] for t in wins_only)/len(wins_only)
        avg_loss = sum(t["net_pnl"] for t in losses_only)/len(losses_only) if losses_only else 0
        print(f"  Avg win:  ${avg_win:.2f}")
        print(f"  Avg loss: ${avg_loss:.2f}")
        print(f"  Expectancy per trade: ${(avg_win*len(wins_only)+avg_loss*len(losses_only))/len(all_t_sorted):.2f}")

    # Prior-direction breakdown of wins/losses
    print("\n  Win/Loss by prior trend direction:")
    for pd in ["UP","DOWN"]:
        pd_wins  = [t for t in all_t_sorted if t["outcome"]=="WIN"  and t["prior_direction"]==pd]
        pd_loss  = [t for t in all_t_sorted if t["outcome"]=="LOSS" and t["prior_direction"]==pd]
        pd_all   = pd_wins + pd_loss
        if pd_all:
            wr = len(pd_wins)/len(pd_all)
            print(f"    Prior {pd}-trend: {len(pd_all)} trades, {wr:.0%} WR")

    print()


if __name__ == "__main__":
    main()
