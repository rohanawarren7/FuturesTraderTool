"""
Topstep $50K — Edge 1 projection calculator.
Runs from 2yr backtest data to produce exact daily P&L distribution
and combine/funded account milestone projections.
"""
import sys, datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
import polars as pl
from data.databento_provider import load_ohlcv, add_daily_vwap
from collections import defaultdict

MES_TICK = 0.25; TICK_VAL = 1.25; COMMISSION = 3.16

def add_daily_atr(df):
    daily = (df.group_by("date").agg((pl.col("high").max()-pl.col("low").min()).alias("dr"))
               .sort("date").with_columns(pl.col("dr").rolling_mean(window_size=10,min_samples=1).alias("atr")))
    return df.join(daily.select(["date","atr"]), on="date", how="left")

def compute_day_metrics(bars):
    if len(bars) < 10: return None
    closes=[b["close"] for b in bars]; vwaps=[b["vwap"] for b in bars]
    vm1s=[b["vwap_minus1"] for b in bars]; vp1s=[b["vwap_plus1"] for b in bars]
    highs=[b["high"] for b in bars]; lows=[b["low"] for b in bars]; n=len(bars)
    dr=max(highs)-min(lows)
    return {
        "above_pct": sum(1 for c,v in zip(closes,vwaps) if c>=v)/n,
        "vwap_slope": abs(vwaps[-1]-vwaps[0])/dr if dr>0 else 1.0,
        "directionality": abs(closes[-1]-closes[0])/dr if dr>0 else 1.0,
        "in_sd1": sum(1 for c,m,p in zip(closes,vm1s,vp1s) if m<=c<=p)/n,
        "net_move": closes[-1]-closes[0],
    }

def is_trending(m):
    v=0
    if 0.35<=m["above_pct"]<=0.65: v+=1
    if m["vwap_slope"]<0.25: v+=1
    if m["directionality"]<0.40: v+=1
    if m["in_sd1"]>0.70: v+=1
    return v<3

df = load_ohlcv(start_date="2023-01-01", end_date="2025-01-01")
df = add_daily_vwap(df); df = add_daily_atr(df)
rows = df.to_dicts()
day_map = defaultdict(list)
for r in rows: day_map[str(r["date"])].append(r)
sorted_dates = sorted(day_map.keys())
date_metrics = {d: compute_day_metrics(day_map[d]) for d in sorted_dates}
date_metrics = {d:m for d,m in date_metrics.items() if m}

# Simulate: SHORT only, prior TRENDING, Tue-Thu, 10:00-13:00 CT (600-780 min from midnight CT)
trades = []; n=len(rows)
for i in range(1, n):
    bar=rows[i]; prev=rows[i-1]
    if not bar.get("atr") or bar["atr"]==0: continue
    t_ct=bar["time_ct_minutes"]
    if t_ct<600 or t_ct>=780: continue
    date_str=str(bar["date"])
    try: dow=datetime.date.fromisoformat(date_str).weekday()
    except: continue
    if dow not in {1,2,3}: continue
    idx=sorted_dates.index(date_str) if date_str in sorted_dates else -1
    if idx<1: continue
    pm=date_metrics.get(sorted_dates[idx-1])
    if not pm or not is_trending(pm): continue
    vp1=bar["vwap_plus1"]; atr=bar["atr"]
    if not (prev["close"]>vp1 and bar["close"]<=vp1): continue
    entry=bar["close"]; stop=entry+0.5*atr; tgt=entry-0.5*atr
    outcome="TIMEOUT"; ep=entry; j=i+1
    while j<n and rows[j]["date"]==bar["date"]:
        b=rows[j]
        if b["high"]>=stop: outcome="LOSS"; ep=stop; break
        if b["low"]<=tgt:   outcome="WIN";  ep=tgt;  break
        j+=1
    pnl=(entry-ep)/MES_TICK*TICK_VAL-COMMISSION
    trades.append({"date":date_str,"outcome":outcome,"pnl":round(pnl,2),"atr":round(atr,2)})

# Aggregate by day
daily_pnl = defaultdict(float)
for t in trades: daily_pnl[t["date"]] += t["pnl"]
dpnls = sorted(daily_pnl.values())
days_total = len(dpnls)

wins   = [t for t in trades if t["outcome"]=="WIN"]
losses = [t for t in trades if t["outcome"]=="LOSS"]
n_tr   = len(wins)+len(losses)
wr     = len(wins)/n_tr
avg_w  = sum(t["pnl"] for t in wins)/len(wins)
avg_l  = sum(t["pnl"] for t in losses)/len(losses)
exp    = sum(t["pnl"] for t in trades)/n_tr
total  = sum(t["pnl"] for t in trades)

# Max drawdown (1 MES)
eq=0; peak=0; max_dd=0
for t in sorted(trades, key=lambda x: x["date"]):
    eq+=t["pnl"]; peak=max(peak,eq); max_dd=min(max_dd,eq-peak)

# Max consecutive trade losses
sorted_t = sorted(trades, key=lambda x: x["date"])
max_tl=cur_tl=0
for t in sorted_t:
    if t["outcome"]=="LOSS": cur_tl+=1; max_tl=max(max_tl,cur_tl)
    else: cur_tl=0

# Consecutive losing DAYS
daily_sorted = [(d,v) for d,v in sorted(daily_pnl.items())]
max_dl=cur_dl=0
for d,v in daily_sorted:
    if v<0: cur_dl+=1; max_dl=max(max_dl,cur_dl)
    else: cur_dl=0

print("="*70)
print("  EDGE 1 BACKTEST STATS (1 MES, Tue-Thu, 10:00-13:00 CT, prior TRENDING)")
print("="*70)
print(f"  Trades: {n_tr} over 2yr  |  {n_tr/2:.0f}/yr  |  {n_tr/days_total:.2f} per active day")
print(f"  WR: {wr:.1%}  |  Avg win: ${avg_w:.2f}  |  Avg loss: ${avg_l:.2f}")
print(f"  Expectancy: ${exp:.2f}/trade  |  2yr total: ${total:,.0f}")
print(f"  Max drawdown (1 MES): ${max_dd:,.0f}")
print(f"  Max consecutive losing trades: {max_tl}")
print(f"  Max consecutive losing days:   {max_dl}")

print()
print("="*70)
print("  DAILY P&L DISTRIBUTION (1 MES)")
print("="*70)
print(f"  Active trading days: {days_total}")
print(f"  Losing days: {sum(1 for v in dpnls if v<0)}/{days_total}  ({sum(1 for v in dpnls if v<0)/days_total:.0%})")
print(f"  Break-even days: {sum(1 for v in dpnls if v==0)}")
print(f"  Worst day:  ${min(dpnls):,.0f}  |  Best day: ${max(dpnls):,.0f}")
print(f"  Median day: ${sorted(dpnls)[len(dpnls)//2]:,.0f}")
for threshold, note in [(75,"= $150 on 2 MES"),(50,"= $150 on 3 MES"),(30,"= $150 on 5 MES"),(150,"on 1 MES")]:
    cnt = sum(1 for v in dpnls if v>=threshold)
    print(f"  Days >= ${threshold:>3}: {cnt}/{days_total} ({cnt/days_total:.0%})  [{note}]")

print()
print("="*70)
print("  TOPSTEP $50K COMBINE — PACE PROJECTION")
print("  Profit target: $3,000  |  Max loss: $2,000  |  Max contracts: 5 MES")
print("="*70)
rows_out = [
    ("Contracts", "Daily avg", "Avg to $3K target", "Max DD risk", "Within $2K limit?"),
    ("-"*12,"-"*12,"-"*20,"-"*14,"-"*20),
]
for c in [1, 2, 3, 5]:
    daily_avg = exp * c * (n_tr/days_total)
    days_to_target = 3000/daily_avg if daily_avg>0 else 999
    max_dd_c = max_dd * c
    within = "YES" if abs(max_dd_c) < 2000 else "RISK — could breach"
    rows_out.append((f"{c} MES", f"${daily_avg:,.2f}", f"~{days_to_target:.0f} Tue-Thu days",
                     f"${max_dd_c:,.0f}", within))
for r in rows_out:
    print(f"  {r[0]:<14} {r[1]:<14} {r[2]:<22} {r[3]:<16} {r[4]}")

print()
print("  Recommendation: 2 MES (max DD $" + f"{abs(max_dd)*2:,.0f} < $2,000 buffer")
print("  Expected combine completion: ~" + f"{3000/(exp*2*(n_tr/days_total)):.0f} Tue-Thu trading days")

print()
print("="*70)
print("  TOPSTEP $50K LIVE FUNDED ACCOUNT — MILESTONE PROJECTIONS")
print("  Payout: 50% until 30 Benchmark Days ($150+ profit/day), then 90%")
print("="*70)

# Benchmark days by contract count (days where daily P&L * contracts >= $150)
for c in [2, 3, 5]:
    bm_days = sum(1 for v in dpnls if v*c >= 150)
    bm_rate  = bm_days/days_total
    pct_150  = bm_days / 2   # annualised (2yr data)
    days_to_30 = 30/bm_rate if bm_rate>0 else 999
    annual_pnl = exp * c * n_tr/2
    payout_early = annual_pnl * 0.50
    payout_full  = annual_pnl * 0.90
    print(f"  {c} MES contracts:")
    print(f"    Benchmark days ($150+/day): {bm_days}/2yr ({bm_rate:.0%} of active days)")
    print(f"    Est. trading days to 30 benchmarks: ~{days_to_30:.0f}")
    print(f"    Annual P&L (before payout split):  ${annual_pnl:,.0f}")
    print(f"    Payout at 50% (pre-30 benchmarks): ${payout_early:,.0f}/yr")
    print(f"    Payout at 90% (post-30 benchmarks): ${payout_full:,.0f}/yr")
    max_dd_c = abs(max_dd)*c
    print(f"    Max DD risk: ${max_dd_c:,.0f}")
    print()

print("="*70)
print("  SCALING PLAN (funded account unlock thresholds)")
print("="*70)
milestones = [(0,1500,"2 MES"),(1500,2000,"3 MES"),(2000,3000,"5 MES")]
for lo,hi,contracts in milestones:
    c_num = int(contracts.split()[0])
    daily = exp * c_num * (n_tr/days_total)
    days_in_tier = (hi-lo)/daily if daily>0 else 999
    print(f"  Profit ${lo:>5}–${hi:,}: {contracts}  |  ~{days_in_tier:.0f} days in tier  |  ${daily:.2f}/day expected")
