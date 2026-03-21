class MarketStateDetector:
    """
    Detects whether the market is in a balanced or imbalanced state.
    Core logic extracted from pattern-mining VWAP trader video data.
    """

    STATES = {
        "BALANCED":         "Price within SD1, delta flat, DOM symmetric",
        "IMBALANCED_BULL":  "Price above SD1, positive delta, aggressive buying",
        "IMBALANCED_BEAR":  "Price below SD1, negative delta, aggressive selling",
        "VOLATILE_TRANS":   "Price crossing VWAP multiple times, expanding range",
        "LOW_ACTIVITY":     "Small bars, low volume — pre-market or lunch session",
    }

    def detect(
        self,
        vwap_position: str,
        cumulative_delta: float,
        delta_direction: str,           # "POSITIVE", "NEGATIVE", "NEUTRAL"
        atr_ratio: float,               # Current ATR / 20-period average ATR
        volume_ratio: float,            # Current bar volume / 20-bar average volume
        price_crosses_vwap_last_10: int,  # Times price crossed VWAP in last 10 bars
    ) -> str:
        """
        Returns one of the STATES keys based on market conditions.

        Note on delta: In the Pine Script MVP, delta_direction is approximated
        by close vs open (bullish/bearish candle). For accurate order flow,
        replace with real cumulative delta from Rithmic tick data (Phase 2+).
        """
        is_inside_sd1 = vwap_position in ["ABOVE_VWAP", "BELOW_VWAP"]
        is_flat_delta = delta_direction == "NEUTRAL" or abs(cumulative_delta) < 500
        is_normal_atr = 0.7 < atr_ratio < 1.5
        is_normal_vol = 0.5 < volume_ratio < 2.0

        # Low activity — avoid trading
        if volume_ratio < 0.4 or atr_ratio < 0.4:
            return "LOW_ACTIVITY"

        # Volatile transition
        if price_crosses_vwap_last_10 >= 4 and atr_ratio > 1.5:
            return "VOLATILE_TRANS"

        # Imbalanced bullish
        if vwap_position in ["ABOVE_SD1", "ABOVE_SD2"] and delta_direction == "POSITIVE":
            return "IMBALANCED_BULL"

        # Imbalanced bearish
        if vwap_position in ["BELOW_SD1", "BELOW_SD2"] and delta_direction == "NEGATIVE":
            return "IMBALANCED_BEAR"

        # Balanced — price within SD1, delta flat, normal conditions
        if is_inside_sd1 and is_flat_delta and is_normal_atr and is_normal_vol:
            return "BALANCED"

        # Default: treat as balanced if nothing else matches
        return "BALANCED"
