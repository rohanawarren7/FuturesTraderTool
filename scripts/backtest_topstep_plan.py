"""
TOPSTEP-NATIVE PLAN BACKTEST
=============================
Simulates the restructured strategy designed around Topstep's benchmark day
accumulation system rather than raw expectancy maximisation.

Protocol:
  - Signal:    SHORT ±1SD cross-back (prev_close > vp1, bar_close <= vp1)
  - Filter:    Prior day TRENDING, Tue–Thu, ATR <= 54 pts
  - Window:    10:00–13:00 CT (90–270 min into RTH)
  - Execution: ONE trade per day only — first qualifying signal taken, then DONE
  - Stop:      0.5×ATR above entry
  - Target:    0.5×ATR below entry (1:1 RR)

Phases modelled:
  A — Combine:            3 MES, one trade/day
  B — Funded pre-30-bm:  2 MES, one trade/day (stop at $150 net day = benchmark)
  C — Funded post-30-bm: 3 MES, one trade/day (also tests best-stack filter)
  D — Best-stack filter:  Tue–Thu + prior UP-trend + streak≥2 (69% WR candidate)
  E — DOW expansion:      Add Mon + LONG on prior DOWN-trend (56% WR) for funded phase

Benchmark day = net P&L >= $150 on that calendar day (Topstep definition).
"""
from __future__ import annotations
import sys, math, datetime
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import polars as pl
from data.databento_provider import load_ohlcv, add_daily_vwap
from collections import defaultdict

# ── Constants ────────────────────────────────────────────────────────────────
MES_TICK  = 0.25
TICK_VAL  = 1.25
COMM_RT   = 3.16          # round-trip commission per contract
ATR_GATE  = 54.0          # skip day entirely above this ATR
TIME_CT_START = 10*60     # 10:00 CT = 600 min from midnight CT
TIME_CT_END   = 13*60     # 13:00 CT = 780 min from midnight CT
BENCHMARK_DAY_THRESHOLD = 150.0   # Topstep: $150+ net = benchmark day

# ── Helpers ──────────────────────────────────────────────────────────────────
def pval(wins, n, p0=0.50):
    if n < 2: return 1.0
    mu = n*p0; s = math.sqrt(n*p0*(1-p0))
    z = (wins - 0.5 - mu) / s
    return 0.5 * math.erfc(z / math.sqrt(2))

def add_daily_atr(df):
    daily = (df.group_by("date")
               .agg((pl.col("high").max() - pl.col("low").min()).alias("dr"))
               .sort("date")
               .with_columns(pl.col("dr").rolling_mean(window_size=10, min_samples=1).alias("atr")))
    return df.join(daily.select(["date", "atr"]), on="date", how="left")

def compute_day_metrics(bars):
    if len(bars) < 10: return None
    closes = [b["close"] for b in bars]
    vwaps  = [b["vwap"]  for b in bars]
    vm1s   = [b["vwap_minus1"] for b in bars]
    vp1s   = [b["vwap_plus1"]  for b in bars]
    highs  = [b["high"] for b in bars]
    lows   = [b["low"]  for b in bars]
    n_bars = len(bars)
    dr     = max(highs) - min(lows)
    return {
        "above_pct":      sum(1 for c,v in zip(closes,vwaps) if c>=v) / n_bars,
        "vwap_slope":     abs(vwaps[-1]-vwaps[0]) / dr if dr>0 else 1.0,
        "directionality": abs(closes[-1]-closes[0]) / dr if dr>0 else 1.0,
        "in_sd1":         sum(1 for c,m,p in zip(closes,vm1s,vp1s) if m<=c<=p) / n_bars,
        "net_move":       closes[-1] - closes[0],
        "daily_range":    dr,
    }

def is_trending(m):
    v = 0
    if 0.35 <= m["above_pct"] <= 0.65: v += 1
    if m["vwap_slope"] < 0.25:         v += 1
    if m["directionality"] < 0.40:     v += 1
    if m["in_sd1"] > 0.70:             v += 1
    return v < 3

def trade_outcome(rows, i, entry, stop, tgt, date_str):
    """Walk forward bars on the same day to resolve the trade."""
    n = len(rows)
    j = i + 1
    while j < n and str(rows[j]["date"]) == date_str:
        b = rows[j]
        if b["high"] >= stop: return "LOSS", stop
        if b["low"]  <= tgt:  return "WIN",  tgt
        j += 1
    return "TIMEOUT", rows[j-1]["close"] if j > i+1 else entry

def pnl_for_trade(entry, exit_p, direction, contracts):
    """Net P&L for a trade (positive = profit)."""
    if direction == "SHORT":
        ticks = (entry - exit_p) / MES_TICK
    else:
        ticks = (exit_p - entry) / MES_TICK
    return ticks * TICK_VAL * contracts - COMM_RT * contracts

# ── Data Load ─────────────────────────────────────────────────────────────────
print("Loading data...", flush=True)
df = load_ohlcv(start_date="2023-01-01", end_date="2025-01-01")
df = add_daily_vwap(df)
df = add_daily_atr(df)
rows = df.to_dicts()

day_map = defaultdict(list)
for r in rows:
    day_map[str(r["date"])].append(r)
sorted_dates = sorted(day_map.keys())

date_metrics = {}
for d in sorted_dates:
    m = compute_day_metrics(day_map[d])
    if m is not None:
        date_metrics[d] = m

# Pre-compute consecutive trending streaks per date
trending_streak = {}
streak = 0
for d in sorted_dates:
    m = date_metrics.get(d)
    if m and is_trending(m):
        streak += 1
    else:
        streak = 0
    trending_streak[d] = streak

print(f"  {len(sorted_dates)} trading days, {len(rows)} bars loaded")

# ── Core simulation: one trade per qualifying day ─────────────────────────────
def simulate_one_per_day(
    rows, day_map, sorted_dates, date_metrics, trending_streak,
    contracts=1,
    dow_filter=None,          # set of weekday ints (0=Mon…4=Fri)
    prior_regime="TRENDING",  # "TRENDING" | "BALANCED" | None
    prior_direction=None,     # "UP" | "DOWN" | None
    min_streak=1,             # minimum consecutive trending streak
    atr_max=ATR_GATE,
    time_start=TIME_CT_START,
    time_end=TIME_CT_END,
    direction="SHORT",        # "SHORT" | "LONG"
):
    """
    For each qualifying day, scan bars in order.
    Take the FIRST signal that fires — then skip the rest of the day.
    Returns list of day-level result dicts.
    """
    day_results = []
    n = len(rows)

    for date_str in sorted_dates:
        bars = day_map.get(date_str, [])
        if len(bars) < 10: continue

        # Day-of-week filter
        try:
            dow = datetime.date.fromisoformat(date_str).weekday()
        except Exception:
            continue
        if dow_filter and dow not in dow_filter: continue

        # ATR gate
        atr_today = bars[0].get("atr") if bars else None
        if not atr_today or atr_today > atr_max: continue

        # Prior day regime check
        try:
            idx = sorted_dates.index(date_str)
        except ValueError:
            continue
        if idx < 1: continue
        prior_date = sorted_dates[idx - 1]
        pm = date_metrics.get(prior_date)
        if pm is None: continue

        if prior_regime == "TRENDING" and not is_trending(pm): continue
        if prior_regime == "BALANCED" and is_trending(pm):     continue
        if prior_direction == "UP"   and pm["net_move"] <= 0:  continue
        if prior_direction == "DOWN" and pm["net_move"] >= 0:  continue

        # Streak filter (applies to SHORT/TRENDING only)
        if prior_regime == "TRENDING":
            streak = trending_streak.get(prior_date, 0)
            if streak < min_streak: continue

        # Scan bars for first signal
        traded = False
        for bar in bars:
            if traded: break
            if bar.get("atr") is None or bar["atr"] == 0: continue
            t_ct = bar["time_ct_minutes"]
            if t_ct < time_start or t_ct >= time_end: continue

            # Find previous bar on same day
            bar_idx = rows.index(bar)   # we'll cache this below
            if bar_idx == 0: continue
            prev = rows[bar_idx - 1]
            if str(prev["date"]) != date_str: continue

            vp1 = bar.get("vwap_plus1")
            vm1 = bar.get("vwap_minus1")
            if vp1 is None or vm1 is None: continue

            signal_fires = False
            if direction == "SHORT":
                signal_fires = (prev["close"] > vp1 and bar["close"] <= vp1)
            else:  # LONG
                signal_fires = (prev["close"] < vm1 and bar["close"] >= vm1)

            if not signal_fires: continue

            entry = bar["close"]
            atr   = bar["atr"]
            if direction == "SHORT":
                stop = entry + 0.5 * atr
                tgt  = entry - 0.5 * atr
            else:
                stop = entry - 0.5 * atr
                tgt  = entry + 0.5 * atr

            outcome, exit_p = trade_outcome(rows, bar_idx, entry, stop, tgt, date_str)
            net = pnl_for_trade(entry, exit_p, direction, contracts)
            is_bm = (net >= BENCHMARK_DAY_THRESHOLD)

            day_results.append({
                "date":         date_str,
                "dow":          dow,
                "outcome":      outcome,
                "net_pnl":      round(net, 2),
                "benchmark":    is_bm,
                "entry":        entry,
                "exit":         exit_p,
                "atr":          round(atr, 2),
                "streak":       trending_streak.get(prior_date, 0),
            })
            traded = True

    return day_results


# Optimise index lookup — build bar index map
bar_index = {id(r): i for i, r in enumerate(rows)}
# Monkey-patch rows.index to use this
_orig_index = list.index
def fast_index(row):
    return bar_index[id(row)]

# Patch simulate_one_per_day to avoid O(n²) list.index
def simulate_fast(
    day_map, sorted_dates, date_metrics, trending_streak,
    contracts=1,
    dow_filter=None,
    prior_regime="TRENDING",
    prior_direction=None,
    min_streak=1,
    atr_max=ATR_GATE,
    time_start=TIME_CT_START,
    time_end=TIME_CT_END,
    direction="SHORT",
):
    """One-trade-per-day simulation using pre-built bar index (O(n) lookup)."""
    # Build date → bar list with global indices
    date_bar_list = {}
    for d, bars in day_map.items():
        date_bar_list[d] = [(fast_index(b), b) for b in bars]

    day_results = []
    n = len(rows)

    for date_str in sorted_dates:
        bar_pairs = date_bar_list.get(date_str, [])
        if len(bar_pairs) < 10: continue

        try:
            dow = datetime.date.fromisoformat(date_str).weekday()
        except Exception:
            continue
        if dow_filter and dow not in dow_filter: continue

        atr_today = bar_pairs[0][1].get("atr")
        if not atr_today or atr_today > atr_max: continue

        try:
            idx = sorted_dates.index(date_str)
        except ValueError:
            continue
        if idx < 1: continue
        prior_date = sorted_dates[idx - 1]
        pm = date_metrics.get(prior_date)
        if pm is None: continue

        if prior_regime == "TRENDING" and not is_trending(pm): continue
        if prior_regime == "BALANCED" and is_trending(pm):     continue
        if prior_direction == "UP"   and pm["net_move"] <= 0:  continue
        if prior_direction == "DOWN" and pm["net_move"] >= 0:  continue

        if prior_regime == "TRENDING":
            streak = trending_streak.get(prior_date, 0)
            if streak < min_streak: continue

        traded = False
        for gi, bar in bar_pairs:
            if traded: break
            if bar.get("atr") is None or bar["atr"] == 0: continue
            t_ct = bar["time_ct_minutes"]
            if t_ct < time_start or t_ct >= time_end: continue
            if gi == 0: continue
            prev = rows[gi - 1]
            if str(prev["date"]) != date_str: continue

            vp1 = bar.get("vwap_plus1")
            vm1 = bar.get("vwap_minus1")
            if vp1 is None or vm1 is None: continue

            if direction == "SHORT":
                signal_fires = (prev["close"] > vp1 and bar["close"] <= vp1)
            else:
                signal_fires = (prev["close"] < vm1 and bar["close"] >= vm1)

            if not signal_fires: continue

            entry = bar["close"]
            atr   = bar["atr"]
            if direction == "SHORT":
                stop = entry + 0.5 * atr
                tgt  = entry - 0.5 * atr
            else:
                stop = entry - 0.5 * atr
                tgt  = entry + 0.5 * atr

            outcome, exit_p = trade_outcome(rows, gi, entry, stop, tgt, date_str)
            net = pnl_for_trade(entry, exit_p, direction, contracts)
            is_bm = (net >= BENCHMARK_DAY_THRESHOLD)

            day_results.append({
                "date":      date_str,
                "dow":       dow,
                "outcome":   outcome,
                "net_pnl":   round(net, 2),
                "benchmark": is_bm,
                "entry":     entry,
                "exit":      exit_p,
                "atr":       round(atr, 2),
                "streak":    trending_streak.get(prior_date, 0),
                "direction": direction,
            })
            traded = True

    return day_results


def print_phase_stats(label, results, contracts, combine_target=3000.0):
    """Print full stats for one phase/configuration."""
    wins     = [r for r in results if r["outcome"] == "WIN"]
    losses   = [r for r in results if r["outcome"] == "LOSS"]
    timeouts = [r for r in results if r["outcome"] == "TIMEOUT"]
    n        = len(wins) + len(losses)
    n_all    = len(results)

    if n_all == 0:
        print(f"  {label}: NO TRADES")
        return

    wr       = len(wins) / n if n > 0 else 0
    total    = sum(r["net_pnl"] for r in results)
    avg_w    = sum(r["net_pnl"] for r in wins) / len(wins)   if wins   else 0
    avg_l    = sum(r["net_pnl"] for r in losses) / len(losses) if losses else 0
    exp      = total / n_all
    pf       = abs(sum(r["net_pnl"] for r in wins) / sum(r["net_pnl"] for r in losses)) if losses and wins else 0
    p        = pval(len(wins), n) if n >= 5 else 1.0

    bm_days  = sum(1 for r in results if r["benchmark"])
    bm_rate  = bm_days / n_all

    # Max drawdown (equity curve)
    eq=0; peak=0; max_dd=0
    for r in sorted(results, key=lambda x: x["date"]):
        eq += r["net_pnl"]; peak = max(peak, eq); max_dd = min(max_dd, eq - peak)

    # Max consecutive losing days
    sorted_r = sorted(results, key=lambda x: x["date"])
    max_cl = cur_cl = 0
    for r in sorted_r:
        if r["net_pnl"] < 0: cur_cl += 1; max_cl = max(max_cl, cur_cl)
        else: cur_cl = 0

    days_to_combine = combine_target / exp if exp > 0 else 999
    days_to_30bm    = 30 / bm_rate if bm_rate > 0 else 999
    annual_pnl      = total / 2   # 2yr data

    print(f"\n{'─'*72}")
    print(f"  {label}")
    print(f"{'─'*72}")
    print(f"  Active days: {n_all}  |  Trades resolved (W+L): {n}  |  Timeouts: {len(timeouts)}")
    print(f"  WR: {wr:.1%}  |  PF: {pf:.2f}  |  p={p:.3f}  |  Expectancy: ${exp:.2f}/day")
    print(f"  Avg win: ${avg_w:.2f}  |  Avg loss: ${avg_l:.2f}")
    print(f"  2yr total: ${total:,.0f}  |  Max drawdown: ${max_dd:,.0f}")
    print(f"  Max consecutive losing days: {max_cl}")
    print(f"  Benchmark days (≥$150): {bm_days}/{n_all} ({bm_rate:.0%})")
    print(f"  Est. active days to $3K combine:  ~{days_to_combine:.0f}")
    print(f"  Est. active days to 30 benchmarks: ~{days_to_30bm:.0f}")
    print(f"  Annual P&L ({contracts} MES, 50% split): ${annual_pnl*0.50:,.0f}")
    print(f"  Annual P&L ({contracts} MES, 90% split): ${annual_pnl*0.90:,.0f}")

    # Quarterly stability
    from collections import defaultdict as dd
    qmap = dd(list)
    for r in results:
        d = datetime.date.fromisoformat(r["date"])
        q = f"{d.year}-Q{(d.month-1)//3+1}"
        qmap[q].append(r)
    print(f"\n  Quarterly P&L:")
    for q in sorted(qmap.keys()):
        qr = qmap[q]
        qw = sum(1 for x in qr if x["outcome"]=="WIN")
        qt = len([x for x in qr if x["outcome"] in ("WIN","LOSS")])
        qpnl = sum(x["net_pnl"] for x in qr)
        qbm  = sum(1 for x in qr if x["benchmark"])
        flag = "✓" if qpnl > 0 else "✗"
        wr_str = f"{qw/qt:.0%}" if qt > 0 else " N/A"
        print(f"    {q}: n={len(qr):>3}  WR={wr_str:>4}  P&L=${qpnl:>7,.0f}  BM={qbm:>2}  {flag}")


# ── Run all phases ─────────────────────────────────────────────────────────────
print("\n" + "="*72)
print("  TOPSTEP-NATIVE PLAN BACKTEST — 2yr MES (Jan 2023–Jan 2025)")
print("  Protocol: ONE trade per qualifying day; stop on first signal")
print("="*72)

# Phase A: Combine — 3 MES, Tue-Thu, prior TRENDING, ATR<=54
phase_a = simulate_fast(
    day_map, sorted_dates, date_metrics, trending_streak,
    contracts=3,
    dow_filter={1, 2, 3},
    prior_regime="TRENDING",
    direction="SHORT",
)
print_phase_stats("PHASE A — COMBINE: 3 MES, Tue–Thu, Prior TRENDING", phase_a, 3)

# Phase B: Funded pre-30-bm — 2 MES, Tue-Thu, prior TRENDING
phase_b = simulate_fast(
    day_map, sorted_dates, date_metrics, trending_streak,
    contracts=2,
    dow_filter={1, 2, 3},
    prior_regime="TRENDING",
    direction="SHORT",
)
print_phase_stats("PHASE B — FUNDED PRE-30-BM: 2 MES, Tue–Thu, Prior TRENDING", phase_b, 2)

# Phase C: Funded post-30-bm — 3 MES, Tue-Thu, prior TRENDING
phase_c = simulate_fast(
    day_map, sorted_dates, date_metrics, trending_streak,
    contracts=3,
    dow_filter={1, 2, 3},
    prior_regime="TRENDING",
    direction="SHORT",
)
print_phase_stats("PHASE C — FUNDED POST-30-BM: 3 MES, Tue–Thu, Prior TRENDING", phase_c, 3)

# Phase D: Best-stack filter — Tue-Thu, prior UP-trend, streak>=2, 3 MES
phase_d = simulate_fast(
    day_map, sorted_dates, date_metrics, trending_streak,
    contracts=3,
    dow_filter={1, 2, 3},
    prior_regime="TRENDING",
    prior_direction="UP",
    min_streak=2,
    direction="SHORT",
)
print_phase_stats("PHASE D — BEST-STACK: 3 MES, Tue–Thu, Prior UP+Streak≥2", phase_d, 3)

# Phase E: DOW expansion — add LONG on prior DOWN-trend (Mon-Thu)
# SHORT component (Tue-Thu, prior TRENDING)
phase_e_short = simulate_fast(
    day_map, sorted_dates, date_metrics, trending_streak,
    contracts=3,
    dow_filter={1, 2, 3},
    prior_regime="TRENDING",
    direction="SHORT",
)
# LONG component (Mon-Thu, prior net_move DOWN)
phase_e_long = simulate_fast(
    day_map, sorted_dates, date_metrics, trending_streak,
    contracts=3,
    dow_filter={0, 1, 2, 3},     # Mon-Thu
    prior_regime=None,
    prior_direction="DOWN",
    direction="LONG",
)
# Combine — deduplicate (same day could qualify for both; SHORT takes priority)
short_dates = {r["date"] for r in phase_e_short}
long_only   = [r for r in phase_e_long if r["date"] not in short_dates]
phase_e_combined = sorted(phase_e_short + long_only, key=lambda x: x["date"])
print_phase_stats("PHASE E — EXPANDED: 3 MES, SHORT Tue–Thu + LONG Mon–Thu (prior DOWN)", phase_e_combined, 3)

# ── Benchmark day accumulation comparison table ────────────────────────────────
print("\n" + "="*72)
print("  BENCHMARK DAY ACCUMULATION COMPARISON")
print("  (30 benchmark days required for 90% payout split)")
print("="*72)
print(f"  {'Configuration':<55} {'BM Rate':>8}  {'Days→30 BM':>12}  {'Wks→30 BM':>10}")
print(f"  {'-'*55} {'-'*8}  {'-'*12}  {'-'*10}")

def bm_summary(label, results):
    n = len(results)
    if n == 0:
        print(f"  {label:<55} {'N/A':>8}  {'N/A':>12}  {'N/A':>10}")
        return
    bm = sum(1 for r in results if r["benchmark"])
    rate = bm / n
    d30 = 30 / rate if rate > 0 else 999
    w30 = d30 / 3   # ~3 active days per week
    print(f"  {label:<55} {rate:>7.0%}  {d30:>11.0f}d  {w30:>9.0f}w")

bm_summary("Phase B: 2 MES, Tue-Thu only", phase_b)
bm_summary("Phase C: 3 MES, Tue-Thu only", phase_c)
bm_summary("Phase D: 3 MES, best-stack (UP+streak≥2, fewer days)", phase_d)
bm_summary("Phase E: 3 MES, expanded (SHORT+LONG, Mon-Thu)", phase_e_combined)

# ── Combine pace comparison ────────────────────────────────────────────────────
print(f"\n  {'Configuration':<55} {'Avg/day':>8}  {'Days→$3K':>10}  {'Wks→$3K':>9}")
print(f"  {'-'*55} {'-'*8}  {'-'*10}  {'-'*9}")

def combine_summary(label, results):
    n = len(results)
    if n == 0:
        print(f"  {label:<55} {'N/A':>8}  {'N/A':>10}  {'N/A':>9}")
        return
    total = sum(r["net_pnl"] for r in results)
    avg   = total / n
    d3k   = 3000 / avg if avg > 0 else 999
    w3k   = d3k / 3
    print(f"  {label:<55} ${avg:>6,.2f}  {d3k:>9.0f}d  {w3k:>8.0f}w")

combine_summary("Phase A: 3 MES, Tue-Thu, prior TRENDING", phase_a)
combine_summary("Phase D: 3 MES, best-stack", phase_d)
combine_summary("Phase E: 3 MES, expanded (Mon-Thu)", phase_e_combined)

# ── Daily loss limit analysis ──────────────────────────────────────────────────
print(f"\n  DAILY LOSS LIMIT ANALYSIS (Topstep limit: $1,000/day)")
print(f"  {'Configuration':<55} {'Max day loss':>13}  {'Breaches $1K?':>14}")
print(f"  {'-'*55} {'-'*13}  {'-'*14}")

def loss_limit_summary(label, results):
    if not results:
        print(f"  {label:<55} {'N/A':>13}  {'N/A':>14}")
        return
    worst = min(r["net_pnl"] for r in results)
    breach = "YES — BREACH" if worst < -1000 else "Safe"
    print(f"  {label:<55} ${worst:>11,.0f}  {breach:>14}")

loss_limit_summary("Phase A: 3 MES", phase_a)
loss_limit_summary("Phase B: 2 MES", phase_b)
loss_limit_summary("Phase C/D: 3 MES", phase_c)
loss_limit_summary("Phase E: 3 MES expanded", phase_e_combined)

# ── Annual income projections ──────────────────────────────────────────────────
print(f"\n  ANNUAL INCOME PROJECTIONS")
print("="*72)

def income_proj(label, results, contracts):
    if not results: return
    total_2yr = sum(r["net_pnl"] for r in results)
    annual = total_2yr / 2
    print(f"  {label}")
    print(f"    Annual gross P&L ({contracts} MES): ${annual:,.0f}")
    print(f"    At 50% split (pre-30-bm):  ${annual*0.50:,.0f}/yr")
    print(f"    At 90% split (post-30-bm): ${annual*0.90:,.0f}/yr")

income_proj("Phase B: 2 MES, pre-30-bm funded", phase_b, 2)
income_proj("Phase C: 3 MES, post-30-bm funded", phase_c, 3)
income_proj("Phase E: 3 MES, expanded (post-30-bm)", phase_e_combined, 3)

print("\n" + "="*72)
print("  RECOMMENDATION SUMMARY")
print("="*72)
# Determine best path
bm_b = sum(1 for r in phase_b if r["benchmark"]) / len(phase_b) if phase_b else 0
bm_e = sum(1 for r in phase_e_combined if r["benchmark"]) / len(phase_e_combined) if phase_e_combined else 0
avg_a = sum(r["net_pnl"] for r in phase_a) / len(phase_a) if phase_a else 0
d3k_a = 3000 / avg_a if avg_a > 0 else 999
d30_b = 30 / bm_b if bm_b > 0 else 999
d30_e = 30 / bm_e if bm_e > 0 else 999

print(f"  Combine (3 MES, 1 trade/day):    ~{d3k_a:.0f} active Tue-Thu days to $3K target")
print(f"  Funded pre-30-bm (2 MES):        ~{d30_b:.0f} active days to 30 benchmarks")
print(f"  Funded expanded (3 MES, Mon-Thu): ~{d30_e:.0f} active days to 30 benchmarks")
print()
print("  PHASE SEQUENCE:")
print("    1. COMBINE:       3 MES, SHORT only, Tue-Thu, prior TRENDING, 1 trade/day")
print("    2. FUNDED PRE-BM: 2 MES, SHORT only, Tue-Thu, prior TRENDING, stop at $150")
print("    3. FUNDED POST-BM:3 MES, SHORT Tue-Thu + LONG Mon-Thu (prior DOWN), 1 trade/day")
print()

# ── DIAGNOSTIC: Why did WR drop from 66% → 57.5%? ────────────────────────────
# Hypothesis: the first signal in the window has lower WR than the average.
# The multi-trade simulation included later signals (better quality) which lifted WR.
# Test: WR by time-of-first-signal bucket on qualifying days.
print("\n" + "="*72)
print("  DIAGNOSTIC — WR BY TIME OF FIRST SIGNAL (Tue-Thu, prior TRENDING)")
print("  This identifies whether taking the first signal is the right approach.")
print("="*72)

# Re-run but record time-of-first-signal
def simulate_with_time_info(day_map, sorted_dates, date_metrics, trending_streak,
                             contracts=1, dow_filter=None, atr_max=ATR_GATE,
                             time_start=TIME_CT_START, time_end=TIME_CT_END):
    date_bar_list = {d: [(fast_index(b), b) for b in bars] for d, bars in day_map.items()}
    results = []
    n = len(rows)
    for date_str in sorted_dates:
        bar_pairs = date_bar_list.get(date_str, [])
        if len(bar_pairs) < 10: continue
        try:
            dow = datetime.date.fromisoformat(date_str).weekday()
        except Exception:
            continue
        if dow_filter and dow not in dow_filter: continue
        atr_today = bar_pairs[0][1].get("atr")
        if not atr_today or atr_today > atr_max: continue
        try:
            idx = sorted_dates.index(date_str)
        except ValueError:
            continue
        if idx < 1: continue
        prior_date = sorted_dates[idx - 1]
        pm = date_metrics.get(prior_date)
        if pm is None: continue
        if not is_trending(pm): continue

        # Collect ALL signals on the day, not just the first
        for gi, bar in bar_pairs:
            if bar.get("atr") is None or bar["atr"] == 0: continue
            t_ct = bar["time_ct_minutes"]
            if t_ct < time_start or t_ct >= time_end: continue
            if gi == 0: continue
            prev = rows[gi - 1]
            if str(prev["date"]) != date_str: continue
            vp1 = bar.get("vwap_plus1")
            if vp1 is None: continue
            if not (prev["close"] > vp1 and bar["close"] <= vp1): continue

            entry = bar["close"]; atr = bar["atr"]
            stop = entry + 0.5*atr; tgt = entry - 0.5*atr
            outcome, exit_p = trade_outcome(rows, gi, entry, stop, tgt, date_str)
            net = pnl_for_trade(entry, exit_p, "SHORT", contracts)
            results.append({
                "date": date_str, "t_ct": t_ct, "outcome": outcome,
                "net_pnl": round(net, 2),
            })
            break  # still only first signal per day — but record the time

    return results

first_signal_results = simulate_with_time_info(
    day_map, sorted_dates, date_metrics, trending_streak,
    contracts=1, dow_filter={1,2,3}
)

# Bucket by time of first signal
buckets = [
    (600, 630, "10:00–10:30 CT"),
    (630, 660, "10:30–11:00 CT"),
    (660, 690, "11:00–11:30 CT"),
    (690, 720, "11:30–12:00 CT"),
    (720, 750, "12:00–12:30 CT"),
    (750, 780, "12:30–13:00 CT"),
]
print(f"  {'Time bucket':<22} {'n':>5}  {'Wins':>5}  {'WR':>6}  {'P&L (1MES)':>12}  {'sig':>6}")
print(f"  {'-'*22} {'-'*5}  {'-'*5}  {'-'*6}  {'-'*12}  {'-'*6}")
for ts, te, label in buckets:
    sub = [r for r in first_signal_results if ts <= r["t_ct"] < te]
    if not sub:
        print(f"  {label:<22} {'0':>5}")
        continue
    w = sum(1 for r in sub if r["outcome"]=="WIN")
    l = sum(1 for r in sub if r["outcome"]=="LOSS")
    n_wl = w + l
    wr_s = f"{w/n_wl:.0%}" if n_wl > 0 else "N/A"
    pnl = sum(r["net_pnl"] for r in sub)
    p = pval(w, n_wl) if n_wl >= 5 else 1.0
    if n_wl == 0:
        sig = "?"
    else:
        sig = "✓" if w/n_wl >= 0.55 and p < 0.05 and n_wl >= 20 else "~" if n_wl >= 5 else "?"
    print(f"  {label:<22} {len(sub):>5}  {w:>5}  {wr_s:>6}  ${pnl:>10,.0f}  {sig:>6}")

# Now test: what if we wait until LATER in window before taking first signal?
print(f"\n  DELAYED ENTRY — effect of waiting longer before taking first signal")
print(f"  {'Window start':<25} {'n':>5}  {'WR':>6}  {'Timeouts':>9}  {'P&L/day':>10}")
print(f"  {'-'*25} {'-'*5}  {'-'*6}  {'-'*9}  {'-'*10}")
for delay_start, label in [(600,"10:00 CT (current)"), (630,"10:30 CT"), (660,"11:00 CT"),
                            (690,"11:30 CT"), (720,"12:00 CT")]:
    sub = simulate_fast(
        day_map, sorted_dates, date_metrics, trending_streak,
        contracts=1, dow_filter={1,2,3}, prior_regime="TRENDING",
        direction="SHORT", time_start=delay_start, time_end=TIME_CT_END
    )
    if not sub:
        print(f"  {label:<25} {'0':>5}")
        continue
    w = sum(1 for r in sub if r["outcome"]=="WIN")
    l_c = sum(1 for r in sub if r["outcome"]=="LOSS")
    to = sum(1 for r in sub if r["outcome"]=="TIMEOUT")
    n_wl = w + l_c
    wr_s = f"{w/n_wl:.0%}" if n_wl > 0 else "N/A"
    avg_day = sum(r["net_pnl"] for r in sub) / len(sub)
    print(f"  {label:<25} {len(sub):>5}  {wr_s:>6}  {to:>9}  ${avg_day:>8,.2f}")
