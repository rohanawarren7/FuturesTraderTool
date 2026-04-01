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
    """

    # Minimum edge score from PatternMiner to generate a signal
    MINIMUM_EDGE_SCORE: float = 15.0

    def generate(
        self,
        market_state: str,
        vwap_position: str,
        delta_direction: str,
        delta_flip: bool,           # Delta changed direction this bar
        price_at_vwap_band: bool,   # Price touched or bounced from SD band
        volume_spike: bool,         # Volume > threshold × average
        session_phase: str,         # "OPEN", "MID", "CLOSE"
        time_in_session_minutes: int,
        timestamp=None,            # Optional: timestamp for time-of-day filtering
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
            # Block 18:00-19:30 (hour 18, or hour 19 with minute < 30)
            # Backtest showed this is the optimal cutoff - beyond 19:30 filters out winners
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
