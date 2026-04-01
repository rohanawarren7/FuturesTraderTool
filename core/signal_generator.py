import pandas as pd

# Hard stop-trading conditions — ALL must pass before any signal is evaluated.
# These are circuit breakers, not tunable parameters. Do not relax without data.
HARD_FILTERS = {
    # Time-based
    "no_trade_first_15min":   True,   # Avoid open auction chaos (0:00–0:15 ET)
    "no_trade_last_15min":    True,   # Avoid close auction volatility (3:45–4:00 ET)
    "no_trade_lunch":         False,  # Optionally skip 11:30–13:00 ET low-activity window

    # News/event-based (caller must supply these flags from an economic calendar)
    "block_on_fomc":          True,   # No new entries 30 min before / after FOMC
    "block_on_cpi":           True,   # No new entries 30 min before / after CPI
    "block_on_nfp":           True,   # No new entries 30 min before / after NFP

    # Market structure
    "block_low_activity":     True,   # MarketState == LOW_ACTIVITY → no trades
    "block_volatile_trans":   True,   # MarketState == VOLATILE_TRANS → no trades

    # Prop firm account protection
    "block_at_daily_loss_80pct": True,  # Stop trading if intraday loss > 80% of daily limit
    "block_at_mll_90pct":     True,   # Stop trading if equity within 10% of MLL floor
}

# ── Edge 1: Prior-Day Trending Fade — empirically derived from 2yr MES backtest ──
# Source: scripts/test_edge1_refined.py — 508 trading days, Jan 2023–Jan 2025
# Signal: SHORT ±1SD cross-back on days following a TRENDING prior day
# Full spec: 10:00–13:00 CT, Tue–Thu, stop 0.5×ATR, target 0.5×ATR (1:1 RR)
PRIOR_TREND_FADE_PARAMS = {
    # Session window in minutes from RTH open (9:30 ET)
    # 10:00 CT = 11:00 ET = 90 min in; 13:00 CT = 14:00 ET = 270 min in
    "window_start_min": 90,   # 11:00 ET (10:00 CT)
    "window_end_min":   270,  # 14:00 ET (13:00 CT)

    # Day-of-week filter: 1=Tue, 2=Wed, 3=Thu (0=Mon, 4=Fri excluded)
    # Mon: marginal 55% WR; Fri: 48% WR actively drags — skip both
    "allowed_weekdays": {1, 2, 3},

    # ATR filter: edge strongest when session ATR < 44 pts (p50)
    # Above 54 pts (p75) the edge weakens to 52% WR — reduce size or skip
    "atr_preferred_max": 44.0,   # full size below this
    "atr_reduced_max":   54.0,   # half size between preferred and reduced
    "atr_avoid_above":   54.0,   # skip signal above this (configurable)

    # Consecutive trending streak: WR improves with longer streaks
    # streak ≥ 4: WR=66%, PF=1.72 vs baseline 61%
    "streak_confidence_boost": {1: 0.61, 2: 0.61, 3: 0.63, 4: 0.66},

    # Backtest-derived statistics (1 MES, Tue–Thu, 10:00–13:00 CT)
    "empirical_wr":          0.66,   # Tue-Thu filter
    "empirical_pf":          1.72,
    "empirical_expectancy":  17.57,  # $/trade on 1 MES
    "empirical_n":           352,    # trades over 2yr sample
    "empirical_max_dd":     -2703,   # max drawdown over 2yr sample
}


class SignalGenerator:
    """
    Generates BUY / SELL / HOLD signals based on VWAP position,
    market state, and order flow conditions.

    Confidence values are seeded from the spec defaults and updated
    by run_pattern_mining.py once video data is available.

    DELTA PROXY NOTE: In the Pine Script MVP, delta_direction is derived
    from close vs open (bullish/bearish candle). This is a structural
    approximation. Real order-flow delta (Sierra Chart + Rithmic) should
    replace this in Phase 2. Signals should be treated with this limitation
    in mind until the Sierra Chart upgrade.

    SETUP PRIORITY ORDER (highest confidence first):
      1. PRIOR_TREND_FADE — empirically derived, 61–69% WR, 2yr backtest
      2. MEAN_REVERSION_SHORT/LONG — balanced market SD1 fade
      3. VWAP_CONTINUATION — imbalanced market trend continuation
    """

    # Minimum edge score from PatternMiner to generate a signal
    MINIMUM_EDGE_SCORE: float = 15.0

    def generate(
        self,
        market_state: str,
        vwap_position: str,
        delta_direction: str,
        delta_flip: bool,               # Delta changed direction this bar
        price_at_vwap_band: bool,       # Price touched or bounced from SD band
        volume_spike: bool,             # Volume > threshold × average
        session_phase: str,             # "OPEN", "MID", "CLOSE"
        time_in_session_minutes: int,
        timestamp=None,                 # Optional: for time-of-day and DOW filtering
        # ── Edge 1: Prior-Day Trending Fade inputs ──────────────────────────
        prior_day_regime: str = None,   # "TRENDING" | "BALANCED" | None
        prior_day_direction: str = None,# "UP" | "DOWN" | None (prior day net move)
        trending_streak: int = 0,       # consecutive prior trending days
        current_atr: float = 0.0,       # 10-day rolling ATR of daily range (points)
    ) -> dict:
        """Returns a signal dict with action, setup_type, confidence, and notes."""

        hold = {"action": "HOLD", "setup_type": None, "confidence": 0.0, "notes": ""}

        # Time filter: no trades in first 15 min or last 15 min of RTH
        if time_in_session_minutes < 15 or time_in_session_minutes > 375:
            hold["notes"] = "TIME_FILTER: Outside preferred trading window"
            return hold

        # Filter: No trades during Globex open (18:00-19:30 ET) - backtest showed losses
        if timestamp is not None:
            from datetime import datetime
            if isinstance(timestamp, str):
                timestamp = pd.to_datetime(timestamp)
            hour = timestamp.hour
            minute = timestamp.minute
            if hour == 18 or (hour == 19 and minute < 30):
                hold["notes"] = "TIME_FILTER: Avoiding Globex open (18:00-19:30)"
                return hold

        if market_state == "LOW_ACTIVITY":
            hold["notes"] = "LOW_ACTIVITY: No signal"
            return hold

        if market_state == "VOLATILE_TRANS":
            hold["notes"] = "VOLATILE_TRANS: Avoiding choppy conditions"
            return hold

        signal = dict(hold)

        # ==================================================================
        # SETUP 0: PRIOR-DAY TRENDING FADE  ← highest-priority empirical edge
        #
        # Specification from 2yr MES backtest (test_edge1_refined.py):
        #   - Prior day classified TRENDING (regime_votes < 3 of 4 metrics)
        #   - SHORT only: price crosses back inside +1SD from above
        #   - Window: 10:00–13:00 CT (90–270 min into RTH session)
        #   - Best days: Tue–Thu (Mon 55% WR, Fri 48% — skip both)
        #   - ATR: strongest in low vol (<44 pts); weakens in high vol (>54 pts)
        #   - Streak: WR improves with consecutive trending days (≥4: 66%)
        #   - RR: 1:1 ONLY — stop 0.5×ATR, target 0.5×ATR
        #          DO NOT widen target; edge collapses at 1.5:1 RR
        #
        # Expected: 66% WR (Tue–Thu), +$17.57 expectancy/trade (1 MES)
        # ==================================================================
        p = PRIOR_TREND_FADE_PARAMS
        _in_ptf_window = (p["window_start_min"] <= time_in_session_minutes < p["window_end_min"])

        # Weekday check
        _ptf_day_ok = True
        if timestamp is not None:
            try:
                ts = pd.to_datetime(timestamp) if isinstance(timestamp, str) else timestamp
                _ptf_day_ok = ts.weekday() in p["allowed_weekdays"]
            except Exception:
                _ptf_day_ok = True  # can't determine DOW, allow

        # ATR regime: skip entirely if above hard ceiling
        _ptf_atr_ok = True
        _atr_note = ""
        if current_atr > 0:
            if current_atr > p["atr_avoid_above"]:
                _ptf_atr_ok = False
                _atr_note = f" [HIGH_VOL ATR={current_atr:.0f}>{p['atr_avoid_above']:.0f} — skipped]"

        if (prior_day_regime == "TRENDING"
                and vwap_position == "ABOVE_SD1"
                and _in_ptf_window
                and _ptf_day_ok
                and _ptf_atr_ok):

            # Confidence scales with streak and prior direction
            streak_key = min(trending_streak, 4) if trending_streak >= 1 else 1
            base_conf = p["streak_confidence_boost"].get(streak_key, p["empirical_wr"])

            # Prior UP-trend is the stronger sub-signal (+1pp)
            if prior_day_direction == "UP":
                base_conf = min(base_conf + 0.01, 0.95)

            # ATR note for reduced-size regime
            size_note = ""
            if current_atr > p["atr_preferred_max"]:
                size_note = f" [MID_VOL: consider half size]"

            streak_note = f", streak={trending_streak}d" if trending_streak > 1 else ""
            signal = {
                "action": "SELL",
                "setup_type": "PRIOR_TREND_FADE_SHORT",
                "confidence": round(base_conf, 3),
                "target": "SD1_CROSS_BACK",   # target = 0.5×ATR below entry
                "stop":   "ATR_HALF_ABOVE",    # stop   = 0.5×ATR above entry
                "rr_ratio": 1.0,
                "notes": (
                    f"Edge1: prior-day TRENDING({prior_day_direction or '?'}){streak_note}, "
                    f"SD1 cross-back SHORT, 10-13CT window{size_note}{_atr_note}"
                ),
            }
            return signal  # highest priority — return immediately if triggered

        # ------------------------------------------------------------------
        # SETUP 1: BALANCED MEAN-REVERSION (target 65-75% win rate)
        # Price tags SD1 from inside, delta flips, volume spike → fade to VWAP
        # ------------------------------------------------------------------
        if market_state == "BALANCED":
            if (vwap_position == "ABOVE_SD1"
                    and delta_direction == "NEGATIVE"
                    and delta_flip):
                signal = {
                    "action": "SELL",
                    "setup_type": "MEAN_REVERSION_SHORT",
                    "confidence": 0.72,
                    "target": "VWAP",
                    "stop": "SD2_UPPER",
                    "rr_ratio": 2.0,
                    "notes": "Price at SD1 resistance, delta flipping negative, balanced market",
                }
            elif (vwap_position == "BELOW_SD1"
                  and delta_direction == "POSITIVE"
                  and delta_flip):
                signal = {
                    "action": "BUY",
                    "setup_type": "MEAN_REVERSION_LONG",
                    "confidence": 0.72,
                    "target": "VWAP",
                    "stop": "SD2_LOWER",
                    "rr_ratio": 2.0,
                    "notes": "Price at SD1 support, delta flipping positive, balanced market",
                }

        # ------------------------------------------------------------------
        # SETUP 1b: IMBALANCED MEAN-REVERSION (target 55-60% win rate)
        # SD1 fade during trending markets with delta divergence
        # Less reliable than balanced mean-reversion but more common
        # ------------------------------------------------------------------
        if market_state == "IMBALANCED_BULL":
            if (vwap_position == "ABOVE_SD1"
                    and delta_direction == "NEGATIVE"
                    and delta_flip):
                signal = {
                    "action": "SELL",
                    "setup_type": "MEAN_REVERSION_SHORT",
                    "confidence": 0.58,
                    "target": "VWAP",
                    "stop": "SD2_UPPER",
                    "rr_ratio": 1.8,
                    "notes": "SD1 fade in bull trend, delta flipping negative",
                }
        elif market_state == "IMBALANCED_BEAR":
            if (vwap_position == "BELOW_SD1"
                  and delta_direction == "POSITIVE"
                  and delta_flip):
                signal = {
                    "action": "BUY",
                    "setup_type": "MEAN_REVERSION_LONG",
                    "confidence": 0.58,
                    "target": "VWAP",
                    "stop": "SD2_LOWER",
                    "rr_ratio": 1.8,
                    "notes": "SD1 fade in bear trend, delta flipping positive",
                }

        # ------------------------------------------------------------------
        # SETUP 2: VWAP RECLAIM CONTINUATION (target 55-65% win rate)
        # Price holds above/below VWAP in imbalanced market + volume confirms
        # ------------------------------------------------------------------
        elif market_state in ("IMBALANCED_BULL", "IMBALANCED_BEAR"):
            if (market_state == "IMBALANCED_BULL"
                    and vwap_position == "ABOVE_VWAP"
                    and delta_direction == "POSITIVE"
                    and volume_spike):
                signal = {
                    "action": "BUY",
                    "setup_type": "VWAP_CONTINUATION_LONG",
                    "confidence": 0.60,
                    "target": "SD1_UPPER",
                    "stop": "VWAP",
                    "rr_ratio": 1.5,
                    "notes": "VWAP reclaim long, imbalanced bull, delta confirms",
                }
            elif (market_state == "IMBALANCED_BEAR"
                  and vwap_position == "BELOW_VWAP"
                  and delta_direction == "NEGATIVE"
                  and volume_spike):
                signal = {
                    "action": "SELL",
                    "setup_type": "VWAP_CONTINUATION_SHORT",
                    "confidence": 0.60,
                    "target": "SD1_LOWER",
                    "stop": "VWAP",
                    "rr_ratio": 1.5,
                    "notes": "VWAP reclaim short, imbalanced bear, delta confirms",
                }

        # ------------------------------------------------------------------
        # SETUP 3: SD2 EXTREME FADE - DISABLED
        # Backtest showed 0% win rate on MES data over 60 days
        # Keeping code commented for reference, but do not use until
        # video data can validate this edge
        # ------------------------------------------------------------------
        # if vwap_position == "ABOVE_SD2" and delta_direction == "NEGATIVE":
        #     signal = {
        #         "action": "SELL",
        #         "setup_type": "SD2_EXTREME_FADE_SHORT",
        #         "confidence": 0.55,
        #         "target": "VWAP",
        #         "stop": "SD2_UPPER",
        #         "rr_ratio": 2.0,
        #         "notes": "SD2 extreme extension, negative delta",
        #     }
        # elif vwap_position == "BELOW_SD2" and delta_direction == "POSITIVE":
        #     signal = {
        #         "action": "BUY",
        #         "setup_type": "SD2_EXTREME_FADE_LONG",
        #         "confidence": 0.55,
        #         "target": "VWAP",
        #         "stop": "SD2_LOWER",
        #         "rr_ratio": 2.0,
        #         "notes": "SD2 extreme extension, positive delta",
        #     }

        return signal

    def update_confidence(self, setup_type: str, win_rate: float, edge_score: float):
        """
        Called by run_pattern_mining.py after video data is available.
        Updates the confidence for a given setup based on empirical win rate.
        Only applies if edge_score >= MINIMUM_EDGE_SCORE.
        """
        if edge_score < self.MINIMUM_EDGE_SCORE:
            return
        # Store updated confidence — subclasses or callers can override
        # the default values in generate() by calling this first.
        setattr(self, f"_confidence_{setup_type}", win_rate)
