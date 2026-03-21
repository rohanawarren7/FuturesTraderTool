"""
AntiDetectionLayer
Thin wrapper around order submission that applies human-like timing and
sizing variation to reduce the risk of prop firm algo-detection flags.

Prop firms (Apex, Topstep) prohibit pure HFT but do not prohibit systematic
strategies. This layer adds natural jitter so order patterns look organic
rather than machine-generated. It does NOT alter signal logic or risk rules.

Usage:
    layer = AntiDetectionLayer(config=PROP_FIRM_CONFIGS["Apex_50k"])
    safe_size = layer.safe_contracts(requested=3)
    layer.pre_order_pause()       # call before every order submission
    layer.post_fill_cooldown()    # call after every fill
"""

import random
import time
import logging
from typing import Optional

logger = logging.getLogger(__name__)


class AntiDetectionLayer:
    """
    Applies human-like variation to order timing and sizing.

    Parameters (all configurable via prop firm config or direct kwargs):
      - min_pause_s / max_pause_s : seconds to wait before submitting an order
      - cooldown_s                : minimum seconds between consecutive fills
      - size_jitter_pct           : randomise contract size ± this fraction
                                    (0.0 = no jitter, stays at requested size)
      - max_trades_per_hour       : hard cap — refuse to signal beyond this rate
      - max_contracts             : pulled from prop firm config if not set

    All randomisation uses a bounded uniform distribution so worst-case
    behaviour remains inside the prop firm's stated rules.
    """

    def __init__(
        self,
        config: Optional[dict] = None,
        min_pause_s: float = 0.5,
        max_pause_s: float = 3.0,
        cooldown_s: float = 5.0,
        size_jitter_pct: float = 0.0,      # 0.0 = disabled by default
        max_trades_per_hour: int = 30,
    ):
        self.min_pause_s       = min_pause_s
        self.max_pause_s       = max_pause_s
        self.cooldown_s        = cooldown_s
        self.size_jitter_pct   = size_jitter_pct
        self.max_trades_per_hour = max_trades_per_hour

        # Pull max_contracts from prop firm config if provided
        self.max_contracts = 10  # sensible default
        if config:
            self.max_contracts = config.get("max_contracts", self.max_contracts)
            # Some firms have stricter timing rules — honour them
            if config.get("firm") in ("Apex", "Apex_50k", "Apex_100k"):
                self.max_pause_s = max(self.max_pause_s, 2.0)

        self._last_fill_time: float = 0.0
        self._fills_this_hour: list[float] = []

    # ──────────────────────────────────────────────────────────
    # Public interface
    # ──────────────────────────────────────────────────────────

    def pre_order_pause(self):
        """
        Waits a random duration before order submission.
        Simulates the natural delay of a human reviewing and clicking.
        """
        pause = random.uniform(self.min_pause_s, self.max_pause_s)
        logger.debug(f"[AntiDetection] Pre-order pause: {pause:.2f}s")
        time.sleep(pause)

    def post_fill_cooldown(self):
        """
        Enforces a minimum gap between fills so trades don't fire
        at a machine-like cadence.
        """
        now  = time.time()
        wait = self.cooldown_s - (now - self._last_fill_time)
        if wait > 0:
            logger.debug(f"[AntiDetection] Post-fill cooldown: {wait:.2f}s")
            time.sleep(wait)
        self._last_fill_time = time.time()
        self._record_fill()

    def safe_contracts(self, requested: int) -> int:
        """
        Returns an allowed contract count after:
          1. Capping at the prop firm's max_contracts limit
          2. Optionally applying ±size_jitter_pct variation
        Always returns at least 1.
        """
        # Hard cap first
        capped = min(requested, self.max_contracts)

        # Optional jitter (disabled by default)
        if self.size_jitter_pct > 0.0:
            delta = int(round(capped * self.size_jitter_pct))
            capped = capped + random.randint(-delta, delta)

        return max(1, min(capped, self.max_contracts))

    def is_rate_limited(self) -> bool:
        """
        Returns True if we've hit max_trades_per_hour.
        Caller should suppress the signal if True.
        """
        self._prune_old_fills()
        return len(self._fills_this_hour) >= self.max_trades_per_hour

    # ──────────────────────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────────────────────

    def _record_fill(self):
        self._fills_this_hour.append(time.time())
        self._prune_old_fills()

    def _prune_old_fills(self):
        cutoff = time.time() - 3600
        self._fills_this_hour = [t for t in self._fills_this_hour if t > cutoff]
