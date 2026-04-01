"""
MES Strategy Explorer — systematic filter analysis on 2yr Databento data.

Starts from the failed baseline (32.2% WR) and applies filters one at a time
to identify where the edge actually lives.

Run: python scripts/strategy_explorer.py
"""
from __future__ import annotations
import sys, math
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import polars as pl
from data.databento_provider import load_ohlcv, add_daily_vwap

# ── Constants ────────────────────────────────────────────────────────────────
MES_TICK       = 0.25
TICK_VAL       = 1.25
COMMISSION     = 3.16
STOP_MULT      = 0.5
TARGET_MULT    = 0.75

# ── Helpers ──────────────────────────────────────────────────────────────────

def pval(wins, n, p0=0.50):
    if n < 2: return 1.0
    mu = n * p0; s = math.sqrt(n * p0 * (1-p0))
    z = (wins - 0.5 - mu) / s
    return 0.5 * math.erfc(z / math.sqrt(2))

def stats(trades):
    w = [t for t in trades if t["outcome"] == "WIN"]
    l = [t for t in trades if t["outcome"] == "LOSS"]
    n = len(w) + len(l)
    wr = len(w)/n if n else 0
    pnl = sum(t["net_pnl"] for t in trades)
    pf = abs(sum(t["net_pnl"] for t in w) / sum(t["net_pnl"] for t in l)) if l and w else 0
    p = pval(len(w), n) if n >= 10 else 1.0
    return {"n": n, "wins": len(w), "wr": wr, "pnl": pnl, "pf": pf, "p": p,
            "timeouts": len(trades)-n}

def fmt(s, label):
    verdict = "✓ GO" if s["wr"] >= 0.55 and s["p"] < 0.05 and s["n"] >= 30 else \
              "~ EDGE" if s["wr"] >= 0.50 and s["n"] >= 20 else "✗"
    return (f"  {label:<45} n={s['n']:>4}  WR={s['wr']:.0%}  "
            f"P&L=${s['pnl']:>8,.0f}  PF={s['pf']:.2f}  p={s['p']:.3f}  {verdict}")

def add_daily_atr(df):
    daily = (df.group_by("date")
               .agg((pl.col("high").max()-pl.col("low").min()).alias("dr"))
               .sort("date")
               .with_columns(pl.col("dr").rolling_mean(window_size=10,min_samples=1).alias("atr")))
    return df.join(daily.select(["date","atr"]), on="date", how="left")

def add_session_vwap_position(df):
    """Add whether close is above/below full-session VWAP."""
    return df.with_columns([
        (pl.col("close") >= pl.col("vwap")).alias("above_vwap"),
    ])

def simulate(rows, stop_m, target_m, filters=None):
    """Walk-forward simulation with optional filter dict."""
    filters = filters or {}
    trades = []
    n = len(rows)

    for i in range(1, n):
        bar  = rows[i]
        prev = rows[i-1]
        if bar.get("atr") is None or bar["atr"] == 0: continue

        t_ct = bar["time_ct_minutes"]
        atr  = bar["atr"]

        # ── Time filter ──────────────────────────────────────────────────
        t_start = filters.get("time_start", 9*60)
        t_end   = filters.get("time_end",  14*60+30)
        if t_ct < t_start or t_ct >= t_end: continue

        # ── Min ATR filter ───────────────────────────────────────────────
        min_atr = filters.get("min_atr", 0)
        if atr < min_atr: continue

        vm1 = bar["vwap_minus1"]; vp1 = bar["vwap_plus1"]
        vm2 = bar["vwap_minus2"]; vp2 = bar["vwap_plus2"]
        above = bar.get("above_vwap", True)

        for direction, triggered in [
            ("LONG",  prev["close"] < vm1 and bar["close"] >= vm1),
            ("SHORT", prev["close"] > vp1 and bar["close"] <= vp1),
        ]:
            if not triggered: continue

            # ── Trend alignment ──────────────────────────────────────────
            if filters.get("trend_align"):
                if direction == "LONG"  and not above: continue
                if direction == "SHORT" and above:     continue

            # ── Direction filter (long-only or short-only) ───────────────
            only = filters.get("only_direction")
            if only and direction != only: continue

            # ── SD2 extreme only ─────────────────────────────────────────
            if filters.get("sd2_only"):
                if direction == "LONG"  and not (prev["close"] < vm2 and bar["close"] >= vm2): continue
                if direction == "SHORT" and not (prev["close"] > vp2 and bar["close"] <= vp2): continue

            # ── Require price to TOUCH the band (wick) ───────────────────
            if filters.get("require_touch"):
                if direction == "LONG"  and bar["low"]  > vm1: continue
                if direction == "SHORT" and bar["high"] < vp1: continue

            entry = bar["close"]
            stop  = entry - stop_m*atr if direction=="LONG" else entry + stop_m*atr
            tgt   = entry + target_m*atr if direction=="LONG" else entry - target_m*atr

            outcome = "TIMEOUT"; ep = entry
            j = i+1
            while j < n and rows[j]["date"] == bar["date"]:
                b = rows[j]
                if direction == "LONG":
                    if b["low"]  <= stop: outcome="LOSS"; ep=stop; break
                    if b["high"] >= tgt:  outcome="WIN";  ep=tgt;  break
                else:
                    if b["high"] >= stop: outcome="LOSS"; ep=stop; break
                    if b["low"]  <= tgt:  outcome="WIN";  ep=tgt;  break
                j += 1

            pnl = ((ep-entry) if direction=="LONG" else (entry-ep)) / MES_TICK * TICK_VAL - COMMISSION
            trades.append({"date": bar["date"], "direction": direction,
                            "outcome": outcome, "net_pnl": round(pnl,2),
                            "hour": t_ct // 60})
    return trades


# ── Main analysis ─────────────────────────────────────────────────────────────

def main():
    print("\nLoading 2yr MES data...")
    df = load_ohlcv(start_date="2023-01-01", end_date="2025-01-01")
    df = add_daily_vwap(df)
    df = add_daily_atr(df)
    df = add_session_vwap_position(df)
    rows = df.to_dicts()
    print(f"{len(df):,} RTH bars  |  {df['date'].n_unique()} trading days\n")

    # ═══════════════════════════════════════════════════════════════════════════
    print("=" * 75)
    print("  SECTION 1: BASELINE DIAGNOSTIC")
    print("=" * 75)

    base = simulate(rows, STOP_MULT, TARGET_MULT)
    print(fmt(stats(base), "Baseline (no filters)"))

    # By direction
    longs  = [t for t in base if t["direction"]=="LONG"]
    shorts = [t for t in base if t["direction"]=="SHORT"]
    print(fmt(stats(longs),  "  → Longs only"))
    print(fmt(stats(shorts), "  → Shorts only"))

    # By hour (CT)
    print("\n  Win rate by hour (CT):")
    for h in range(8, 16):
        ht = [t for t in base if t["hour"]==h]
        if len(ht) < 10: continue
        s = stats(ht)
        bar = "█" * int(s["wr"] * 20)
        print(f"    {h:02d}:xx CT   n={s['n']:>4}  WR={s['wr']:.0%}  {bar}")

    # By year
    print("\n  Win rate by year:")
    for yr in ["2023", "2024"]:
        yt = [t for t in base if str(t["date"]).startswith(yr)]
        print(fmt(stats(yt), f"  → {yr}"))

    # ═══════════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 75)
    print("  SECTION 2: SINGLE FILTER IMPACT")
    print("=" * 75)

    tests = [
        ("Trend-aligned only (LONG above VWAP, SHORT below)",
         {"trend_align": True}),
        ("Long-only (VWAP mean reversion longs)",
         {"only_direction": "LONG"}),
        ("Short-only (VWAP mean reversion shorts)",
         {"only_direction": "SHORT"}),
        ("Time: open session only (08:30–11:00 CT)",
         {"time_start": 8*60+30, "time_end": 11*60}),
        ("Time: afternoon only (13:00–14:30 CT)",
         {"time_start": 13*60, "time_end": 14*60+30}),
        ("Time: avoid lunch (skip 11:00–13:00 CT)",
         {"time_start": 9*60, "time_end": 11*60}),
        ("Min ATR > 15 pts (volatile days only)",
         {"min_atr": 15}),
        ("Min ATR > 25 pts (high volatility only)",
         {"min_atr": 25}),
        ("Require wick touch of SD1 band",
         {"require_touch": True}),
        ("SD2 extreme fades only (±2SD cross-back)",
         {"sd2_only": True}),
    ]

    for label, filt in tests:
        t = simulate(rows, STOP_MULT, TARGET_MULT, filt)
        print(fmt(stats(t), label))

    # ═══════════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 75)
    print("  SECTION 3: COMBINED FILTER STACKING")
    print("=" * 75)

    combos = [
        ("Trend-align + open session",
         {"trend_align": True, "time_start": 8*60+30, "time_end": 11*60}),
        ("Trend-align + afternoon",
         {"trend_align": True, "time_start": 13*60, "time_end": 14*60+30}),
        ("Trend-align + min ATR 15 + open",
         {"trend_align": True, "min_atr": 15, "time_start": 8*60+30, "time_end": 11*60}),
        ("Trend-align + wick touch + open",
         {"trend_align": True, "require_touch": True, "time_start": 8*60+30, "time_end": 11*60}),
        ("Trend-align + wick + ATR15 + open",
         {"trend_align": True, "require_touch": True, "min_atr": 15,
          "time_start": 8*60+30, "time_end": 11*60}),
        ("Trend-align + wick + ATR15 + full day",
         {"trend_align": True, "require_touch": True, "min_atr": 15}),
        ("Long-only + trend-align + wick + ATR15 + open",
         {"only_direction": "LONG", "trend_align": True, "require_touch": True,
          "min_atr": 15, "time_start": 8*60+30, "time_end": 11*60}),
        ("Short-only + trend-align + wick + ATR15 + open",
         {"only_direction": "SHORT", "trend_align": True, "require_touch": True,
          "min_atr": 15, "time_start": 8*60+30, "time_end": 11*60}),
        ("SD2 + trend-align + wick",
         {"sd2_only": True, "trend_align": True, "require_touch": True}),
        ("SD2 + trend-align + wick + ATR15",
         {"sd2_only": True, "trend_align": True, "require_touch": True, "min_atr": 15}),
    ]

    for label, filt in combos:
        t = simulate(rows, STOP_MULT, TARGET_MULT, filt)
        print(fmt(stats(t), label))

    # ═══════════════════════════════════════════════════════════════════════════
    print("\n" + "=" * 75)
    print("  SECTION 4: REWARD:RISK SENSITIVITY (best filter combo)")
    print("=" * 75)

    best_filt = {"trend_align": True, "require_touch": True, "min_atr": 15}
    for sm, tm in [(0.5, 0.5), (0.5, 0.75), (0.5, 1.0), (0.5, 1.5),
                   (0.75, 0.75), (0.75, 1.0), (0.75, 1.5), (1.0, 1.0), (1.0, 2.0)]:
        t = simulate(rows, sm, tm, best_filt)
        rr = tm/sm
        print(fmt(stats(t), f"Stop {sm}×ATR  Target {tm}×ATR  (RR {rr:.1f}:1)"))

    print()


if __name__ == "__main__":
    main()
