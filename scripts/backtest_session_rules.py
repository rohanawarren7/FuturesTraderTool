"""
TOPSTEP DAILY SESSION MANAGEMENT TEST
Hypothesis: multi-trade approach (10:30-13:00 CT) + daily stop rules
preserves the 66% WR while mapping to Topstep benchmark structure.

Rules tested:
  RULE A: No stop rule (run all signals 10:30-13:00 CT, baseline)
  RULE B: Stop for day after FIRST WIN (secure the day, don't give back)
  RULE C: Stop for day after FIRST LOSS (one strike = done)
  RULE D: Stop when daily P&L >= +$150 (benchmark secured) OR after first loss
  RULE E: Stop when daily P&L >= +$150 (benchmark secured), no early stop on loss
"""
import sys, math, datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
import polars as pl
from data.databento_provider import load_ohlcv, add_daily_vwap
from collections import defaultdict

MES_TICK=0.25; TICK_VAL=1.25; COMMISSION=3.16
ATR_GATE=54.0

print("Loading data...", flush=True)
df = load_ohlcv(start_date="2023-01-01", end_date="2025-01-01")
df = add_daily_vwap(df)

def add_atr(df):
    daily=(df.group_by("date").agg((pl.col("high").max()-pl.col("low").min()).alias("dr"))
             .sort("date").with_columns(pl.col("dr").rolling_mean(window_size=10,min_samples=1).alias("atr")))
    return df.join(daily.select(["date","atr"]),on="date",how="left")

df = add_atr(df)
rows = df.to_dicts()
bar_index = {id(r): i for i, r in enumerate(rows)}

day_map = defaultdict(list)
for r in rows: day_map[str(r["date"])].append(r)
sorted_dates = sorted(day_map.keys())

def compute_metrics(bars):
    if len(bars)<10: return None
    c=[b["close"] for b in bars]; v=[b["vwap"] for b in bars]
    m=[b["vwap_minus1"] for b in bars]; p=[b["vwap_plus1"] for b in bars]
    h=[b["high"] for b in bars]; lo=[b["low"] for b in bars]; n=len(bars)
    dr=max(h)-min(lo)
    return {"above_pct":sum(1 for x,y in zip(c,v) if x>=y)/n,
            "vwap_slope":abs(v[-1]-v[0])/dr if dr>0 else 1.0,
            "directionality":abs(c[-1]-c[0])/dr if dr>0 else 1.0,
            "in_sd1":sum(1 for x,a,b_ in zip(c,m,p) if a<=x<=b_)/n,
            "net_move":c[-1]-c[0]}

def is_trending(m):
    v=0
    if 0.35<=m["above_pct"]<=0.65: v+=1
    if m["vwap_slope"]<0.25: v+=1
    if m["directionality"]<0.40: v+=1
    if m["in_sd1"]>0.70: v+=1
    return v<3

date_metrics = {d: compute_metrics(day_map[d]) for d in sorted_dates}
date_metrics = {d:m for d,m in date_metrics.items() if m}

def pval(w,n,p0=0.5):
    if n<2: return 1.0
    z=(w-0.5-n*p0)/math.sqrt(n*p0*(1-p0))
    return 0.5*math.erfc(z/math.sqrt(2))

def run_session(time_start=10*60+30, time_end=13*60, contracts=1,
                stop_on_first_win=False, stop_on_first_loss=False,
                stop_at_benchmark=False, benchmark=150.0):
    n=len(rows)
    day_results=[]

    for date_str in sorted_dates:
        bars = day_map.get(date_str,[])
        if len(bars)<10: continue
        try:
            dow=datetime.date.fromisoformat(date_str).weekday()
        except Exception:
            continue
        if dow not in {1,2,3}: continue

        atr_today = bars[0].get("atr")
        if not atr_today or atr_today>ATR_GATE: continue

        try: idx=sorted_dates.index(date_str)
        except ValueError: continue
        if idx<1: continue
        pm=date_metrics.get(sorted_dates[idx-1])
        if pm is None or not is_trending(pm): continue

        day_pnl=0.0; day_trades=[]; done=False
        for bar in bars:
            if done: break
            if bar.get("atr") is None or bar["atr"]==0: continue
            t_ct=bar["time_ct_minutes"]
            if t_ct<time_start or t_ct>=time_end: continue
            gi=bar_index[id(bar)]
            if gi==0: continue
            prev=rows[gi-1]
            if str(prev["date"])!=date_str: continue
            vp1=bar.get("vwap_plus1")
            if vp1 is None: continue
            if not(prev["close"]>vp1 and bar["close"]<=vp1): continue

            entry=bar["close"]; atr=bar["atr"]
            stop=entry+0.5*atr; tgt=entry-0.5*atr
            outcome="TIMEOUT"; ep=entry
            k=gi+1
            while k<n and str(rows[k]["date"])==date_str:
                bk=rows[k]
                if bk["high"]>=stop: outcome="LOSS"; ep=stop; break
                if bk["low"]<=tgt:   outcome="WIN";  ep=tgt;  break
                k+=1
            trade_pnl=(entry-ep)/MES_TICK*TICK_VAL*contracts - COMMISSION*contracts
            day_pnl+=trade_pnl
            day_trades.append({"outcome":outcome,"pnl":round(trade_pnl,2)})

            if stop_on_first_win and outcome=="WIN": done=True
            if stop_on_first_loss and outcome=="LOSS": done=True
            if stop_at_benchmark and day_pnl>=benchmark: done=True

        if day_trades:
            wins=[t for t in day_trades if t["outcome"]=="WIN"]
            losses=[t for t in day_trades if t["outcome"]=="LOSS"]
            day_results.append({
                "date":date_str,
                "n_trades":len(day_trades),
                "wins":len(wins),
                "losses":len(losses),
                "net_pnl":round(day_pnl,2),
                "benchmark":day_pnl>=benchmark,
            })

    return day_results

def summarise(label, results, contracts):
    if not results:
        print(f"  {label}: no results"); return
    n=len(results)
    total=sum(r["net_pnl"] for r in results)
    pos=[r for r in results if r["net_pnl"]>0]
    neg=[r for r in results if r["net_pnl"]<0]
    bm=sum(1 for r in results if r["benchmark"])
    avg_trades=sum(r["n_trades"] for r in results)/n
    avg_day=total/n
    bm_rate=bm/n if n>0 else 0
    d30=30/bm_rate if bm_rate>0 else 999
    eq=0; peak=0; mdd=0
    for r in sorted(results,key=lambda x:x["date"]):
        eq+=r["net_pnl"]; peak=max(peak,eq); mdd=min(mdd,eq-peak)
    worst=min(r["net_pnl"] for r in results)
    annual=total/2
    print(f"  {label}")
    print(f"    Active days: {n}  |  Avg trades/day: {avg_trades:.1f}")
    print(f"    Positive days: {len(pos)}/{n} ({len(pos)/n:.0%})")
    print(f"    Benchmark days (>=150): {bm}/{n} ({bm_rate:.0%})  |  ~{d30:.0f} active days to 30 benchmarks")
    print(f"    Avg P&L/day: ${avg_day:.2f}  |  2yr total: ${total:,.0f}  |  Max DD: ${mdd:,.0f}")
    print(f"    Worst day: ${worst:,.0f}  |  Breaches $1K daily limit? {'YES' if worst<-1000 else 'No'}")
    print(f"    Annual at {contracts} MES, 90% split: ${annual*0.90:,.0f}/yr")
    print()

print("="*70)
print("  SESSION MANAGEMENT RULES BACKTEST")
print("  2yr MES (Tue-Thu, prior TRENDING, ATR<=54, 10:30-13:00 CT)")
print("  1:1 RR: stop 0.5xATR, target 0.5xATR")
print("="*70)
print()

a=run_session(time_start=630, stop_on_first_win=False, stop_on_first_loss=False, stop_at_benchmark=False, contracts=2)
summarise("RULE A -- No stop rule: run ALL signals 10:30-13:00 (2 MES)", a, 2)

b=run_session(time_start=630, stop_on_first_win=True, contracts=2)
summarise("RULE B -- Stop after FIRST WIN of day (2 MES)", b, 2)

c=run_session(time_start=630, stop_on_first_loss=True, contracts=2)
summarise("RULE C -- Stop after FIRST LOSS of day (2 MES)", c, 2)

d=run_session(time_start=630, stop_on_first_loss=True, stop_at_benchmark=True, contracts=2)
summarise("RULE D -- Stop at $150 benchmark OR after first loss (2 MES)", d, 2)

e=run_session(time_start=630, stop_at_benchmark=True, contracts=2)
summarise("RULE E -- Stop at $150 benchmark, ride out losses (2 MES)", e, 2)

d3=run_session(time_start=630, stop_on_first_loss=True, stop_at_benchmark=True, contracts=3)
summarise("RULE D -- Stop at $150 benchmark OR after first loss (3 MES)", d3, 3)

# Combine pace at 3 MES, rule D
d3_combine=run_session(time_start=630, stop_on_first_loss=True, stop_at_benchmark=True, contracts=3, benchmark=3000.0)
print("  COMBINE PACE TEST (3 MES, Rule D, target $3000):")
n=len(d3_combine); total=sum(r["net_pnl"] for r in d3_combine)
avg=total/n if n>0 else 0
print(f"    Active days: {n}  |  Avg/day: ${avg:.2f}  |  Days to $3K: ~{3000/avg:.0f}" if avg>0 else "    Negative expectancy")
print()

print("-"*70)
print("  QUARTERLY BREAKDOWN -- Rule D (2 MES)")
from collections import defaultdict as dd
qmap=dd(list)
for r in d:
    dt=datetime.date.fromisoformat(r["date"])
    q=f"{dt.year}-Q{(dt.month-1)//3+1}"
    qmap[q].append(r)
print(f"  {'Quarter':<12} {'Days':>5}  {'BM':>4}  {'BM%':>5}  {'P&L':>10}  {'Worst day':>10}")
for q in sorted(qmap.keys()):
    qr=qmap[q]; qpnl=sum(x["net_pnl"] for x in qr)
    qbm=sum(1 for x in qr if x["benchmark"])
    worst=min(x["net_pnl"] for x in qr) if qr else 0
    flag="+" if qpnl>0 else "-"
    print(f"  {q:<12} {len(qr):>5}  {qbm:>4}  {qbm/len(qr):>4.0%}  ${qpnl:>8,.0f}  ${worst:>8,.0f}  {flag}")
