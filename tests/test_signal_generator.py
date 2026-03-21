"""
SignalGenerator test suite.

Run:
    pytest tests/test_signal_generator.py -v
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from core.signal_generator import SignalGenerator


@pytest.fixture
def gen():
    return SignalGenerator()


def _call(gen, **overrides):
    defaults = dict(
        market_state="BALANCED",
        vwap_position="ABOVE_VWAP",
        delta_direction="NEUTRAL",
        delta_flip=False,
        price_at_vwap_band=False,
        volume_spike=False,
        session_phase="MID",
        time_in_session_minutes=90,
    )
    defaults.update(overrides)
    return gen.generate(**defaults)


# ------------------------------------------------------------------
# Time filters
# ------------------------------------------------------------------

def test_hold_in_first_15_minutes(gen):
    result = _call(gen, time_in_session_minutes=10)
    assert result["action"] == "HOLD"
    assert "TIME_FILTER" in result["notes"]


def test_hold_in_last_15_minutes(gen):
    result = _call(gen, time_in_session_minutes=380)
    assert result["action"] == "HOLD"
    assert "TIME_FILTER" in result["notes"]


def test_hold_low_activity(gen):
    result = _call(gen, market_state="LOW_ACTIVITY")
    assert result["action"] == "HOLD"


def test_hold_volatile_transition(gen):
    result = _call(gen, market_state="VOLATILE_TRANS")
    assert result["action"] == "HOLD"


# ------------------------------------------------------------------
# Setup 1: Mean Reversion
# ------------------------------------------------------------------

def test_mean_reversion_long(gen):
    """Price at SD1 support, delta flipping positive, balanced — expect BUY."""
    result = _call(gen,
                   market_state="BALANCED",
                   vwap_position="BELOW_SD1",
                   delta_direction="POSITIVE",
                   delta_flip=True)
    assert result["action"] == "BUY"
    assert result["setup_type"] == "MEAN_REVERSION_LONG"
    assert result["confidence"] > 0.5


def test_mean_reversion_short(gen):
    """Price at SD1 resistance, delta flipping negative, balanced — expect SELL."""
    result = _call(gen,
                   market_state="BALANCED",
                   vwap_position="ABOVE_SD1",
                   delta_direction="NEGATIVE",
                   delta_flip=True)
    assert result["action"] == "SELL"
    assert result["setup_type"] == "MEAN_REVERSION_SHORT"
    assert result["confidence"] > 0.5


def test_mean_reversion_requires_delta_flip(gen):
    """Without delta_flip, mean reversion should not fire."""
    result = _call(gen,
                   market_state="BALANCED",
                   vwap_position="BELOW_SD1",
                   delta_direction="POSITIVE",
                   delta_flip=False)
    assert result["action"] == "HOLD"


# ------------------------------------------------------------------
# Setup 2: VWAP Continuation
# ------------------------------------------------------------------

def test_vwap_continuation_long(gen):
    result = _call(gen,
                   market_state="IMBALANCED_BULL",
                   vwap_position="ABOVE_VWAP",
                   delta_direction="POSITIVE",
                   volume_spike=True)
    assert result["action"] == "BUY"
    assert result["setup_type"] == "VWAP_CONTINUATION_LONG"


def test_vwap_continuation_short(gen):
    result = _call(gen,
                   market_state="IMBALANCED_BEAR",
                   vwap_position="BELOW_VWAP",
                   delta_direction="NEGATIVE",
                   volume_spike=True)
    assert result["action"] == "SELL"
    assert result["setup_type"] == "VWAP_CONTINUATION_SHORT"


def test_vwap_continuation_requires_volume_spike(gen):
    """Continuation without volume spike should not fire."""
    result = _call(gen,
                   market_state="IMBALANCED_BULL",
                   vwap_position="ABOVE_VWAP",
                   delta_direction="POSITIVE",
                   volume_spike=False)
    assert result["action"] == "HOLD"


# ------------------------------------------------------------------
# Setup 3: SD2 Extreme Fade
# ------------------------------------------------------------------

def test_sd2_fade_short(gen):
    result = _call(gen,
                   vwap_position="ABOVE_SD2",
                   delta_direction="NEGATIVE",
                   volume_spike=True,
                   market_state="BALANCED")
    assert result["action"] == "SELL"
    assert result["setup_type"] == "SD2_EXTREME_FADE_SHORT"


def test_sd2_fade_long(gen):
    result = _call(gen,
                   vwap_position="BELOW_SD2",
                   delta_direction="POSITIVE",
                   volume_spike=True,
                   market_state="BALANCED")
    assert result["action"] == "BUY"
    assert result["setup_type"] == "SD2_EXTREME_FADE_LONG"


def test_sd2_fade_requires_volume_spike(gen):
    result = _call(gen,
                   vwap_position="ABOVE_SD2",
                   delta_direction="NEGATIVE",
                   volume_spike=False)
    assert result["action"] == "HOLD"


# ------------------------------------------------------------------
# Signal dict structure
# ------------------------------------------------------------------

def test_signal_has_required_keys(gen):
    """All signals must include action, setup_type, confidence, notes."""
    result = _call(gen)
    assert "action" in result
    assert "setup_type" in result
    assert "confidence" in result
    assert "notes" in result


def test_confidence_in_range(gen):
    """Confidence must always be between 0 and 1."""
    result = _call(gen,
                   market_state="BALANCED",
                   vwap_position="BELOW_SD1",
                   delta_direction="POSITIVE",
                   delta_flip=True)
    assert 0.0 <= result["confidence"] <= 1.0
