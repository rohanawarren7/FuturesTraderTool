"""
Test suite for enhanced SignalGenerator with fallback functionality.
Tests fallback mode and video-derived confidence updates.

Run:
    pytest tests/test_signal_generator_enhanced.py -v
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from core.signal_generator import SignalGenerator, FALLBACK_SETUPS


@pytest.fixture
def gen_no_fallback():
    """SignalGenerator with fallback disabled."""
    return SignalGenerator(video_trade_count=0, use_fallback=False)


@pytest.fixture
def gen_with_fallback():
    """SignalGenerator with fallback enabled (default)."""
    return SignalGenerator(video_trade_count=0, use_fallback=True)


@pytest.fixture
def gen_with_video_data():
    """SignalGenerator with sufficient video data."""
    return SignalGenerator(video_trade_count=50, use_fallback=True)


# ------------------------------------------------------------------
# Fallback Mode Detection Tests
# ------------------------------------------------------------------

def test_should_use_fallback_when_insufficient_data(gen_with_fallback):
    """Should use fallback when video_trade_count < 30."""
    assert gen_with_fallback._should_use_fallback() is True


def test_should_not_use_fallback_with_sufficient_data(gen_with_video_data):
    """Should not use fallback when video_trade_count >= 30."""
    assert gen_with_video_data._should_use_fallback() is False


def test_should_not_use_fallback_when_disabled(gen_no_fallback):
    """Should not use fallback when explicitly disabled."""
    gen_no_fallback.video_trade_count = 10  # Low count
    assert gen_no_fallback._should_use_fallback() is False


# ------------------------------------------------------------------
# Signal Mode Tests
# ------------------------------------------------------------------

def test_get_signal_mode_returns_fallback(gen_with_fallback):
    """Should return FALLBACK when in fallback mode."""
    assert gen_with_fallback.get_signal_mode() == "FALLBACK"


def test_get_signal_mode_returns_video_derived(gen_with_video_data):
    """Should return VIDEO_DERIVED when sufficient data."""
    assert gen_with_video_data.get_signal_mode() == "VIDEO_DERIVED"


# ------------------------------------------------------------------
# Fallback Confidence Tests
# ------------------------------------------------------------------

def test_fallback_reduces_confidence(gen_with_fallback):
    """Fallback should use conservative win rates."""
    signal = gen_with_fallback.generate(
        market_state="BALANCED",
        vwap_position="BELOW_SD1",
        delta_direction="POSITIVE",
        delta_flip=True,
        price_at_vwap_band=True,
        volume_spike=True,
        session_phase="MID",
        time_in_session_minutes=90
    )
    
    # Should have reduced confidence from fallback
    assert signal["confidence"] <= 0.72  # Original is 0.72
    assert "FALLBACK_MODE" in signal.get("notes", "")


def test_fallback_applies_to_mean_reversion(gen_with_fallback):
    """Fallback should apply to mean reversion setups."""
    signal = gen_with_fallback.generate(
        market_state="BALANCED",
        vwap_position="BELOW_SD1",
        delta_direction="POSITIVE",
        delta_flip=True,
        price_at_vwap_band=True,
        volume_spike=True,
        session_phase="MID",
        time_in_session_minutes=90
    )
    
    # Should match fallback win rate for MEAN_REVERSION_SD1
    expected_confidence = FALLBACK_SETUPS["MEAN_REVERSION_SD1"]["win_rate"]
    assert signal["confidence"] == expected_confidence


def test_fallback_applies_to_sd2_fade(gen_with_fallback):
    """Fallback should apply to SD2 fade setups."""
    signal = gen_with_fallback.generate(
        market_state="BALANCED",
        vwap_position="BELOW_SD2",
        delta_direction="POSITIVE",
        delta_flip=False,
        price_at_vwap_band=True,
        volume_spike=True,
        session_phase="MID",
        time_in_session_minutes=90
    )
    
    # Should match fallback win rate for SD2_EXTREME_FADE
    expected_confidence = FALLBACK_SETUPS["SD2_EXTREME_FADE"]["win_rate"]
    assert signal["confidence"] == expected_confidence


def test_no_fallback_when_sufficient_data(gen_with_video_data):
    """Should use original confidence when sufficient video data."""
    signal = gen_with_video_data.generate(
        market_state="BALANCED",
        vwap_position="BELOW_SD1",
        delta_direction="POSITIVE",
        delta_flip=True,
        price_at_vwap_band=True,
        volume_spike=True,
        session_phase="MID",
        time_in_session_minutes=90
    )
    
    # Should use original 0.72 confidence
    assert signal["confidence"] == 0.72
    assert "FALLBACK_MODE" not in signal.get("notes", "")


# ------------------------------------------------------------------
# Video Confidence Update Tests
# ------------------------------------------------------------------

def test_update_confidence_stores_value(gen_with_video_data):
    """Should store updated confidence from video analysis."""
    gen_with_video_data.update_confidence(
        setup_type="MEAN_REVERSION_LONG",
        win_rate=0.75,
        edge_score=20.0  # Above MINIMUM_EDGE_SCORE of 15.0
    )
    
    assert hasattr(gen_with_video_data, "_confidence_MEAN_REVERSION_LONG")
    assert gen_with_video_data._confidence_MEAN_REVERSION_LONG == 0.75


def test_update_confidence_ignored_low_edge_score(gen_with_video_data):
    """Should ignore updates with edge_score below threshold."""
    gen_with_video_data.update_confidence(
        setup_type="MEAN_REVERSION_LONG",
        win_rate=0.80,
        edge_score=10.0  # Below MINIMUM_EDGE_SCORE
    )
    
    assert not hasattr(gen_with_video_data, "_confidence_MEAN_REVERSION_LONG")


def test_update_video_trade_count(gen_with_fallback):
    """Should update video trade count."""
    gen_with_fallback.update_video_trade_count(35)
    
    assert gen_with_fallback.video_trade_count == 35
    assert gen_with_fallback.get_signal_mode() == "VIDEO_DERIVED"


# ------------------------------------------------------------------
# Confidence Cache Tests
# ------------------------------------------------------------------

def test_confidence_cache_updated(gen_with_video_data):
    """Confidence cache should be updated."""
    gen_with_video_data.update_confidence(
        setup_type="MEAN_REVERSION_LONG",
        win_rate=0.75,
        edge_score=20.0
    )
    
    assert "MEAN_REVERSION_LONG" in gen_with_video_data._confidence_cache
    assert gen_with_video_data._confidence_cache["MEAN_REVERSION_LONG"] == 0.75


# ------------------------------------------------------------------
# Signal Generation with Video Confidence Tests
# ------------------------------------------------------------------

def test_uses_cached_confidence_when_available(gen_with_video_data):
    """Should use cached video-derived confidence in signals."""
    # Update confidence first
    gen_with_video_data.update_confidence(
        setup_type="MEAN_REVERSION_LONG",
        win_rate=0.75,
        edge_score=20.0
    )
    
    # Generate signal
    signal = gen_with_video_data.generate(
        market_state="BALANCED",
        vwap_position="BELOW_SD1",
        delta_direction="POSITIVE",
        delta_flip=True,
        price_at_vwap_band=True,
        volume_spike=True,
        session_phase="MID",
        time_in_session_minutes=90
    )
    
    # Should use updated confidence
    assert signal["confidence"] == 0.75
    assert "VIDEO_DERIVED" in signal.get("notes", "")


# ------------------------------------------------------------------
# Fallback Setups Configuration Tests
# ------------------------------------------------------------------

def test_fallback_setups_have_required_fields():
    """All fallback setups should have required configuration."""
    for setup_name, config in FALLBACK_SETUPS.items():
        assert "win_rate" in config
        assert "min_sample_size" in config
        assert "conditions" in config
        assert "description" in config
        
        # Win rate should be between 0 and 1
        assert 0 < config["win_rate"] <= 1
        
        # min_sample_size should be 0 for fallback
        assert config["min_sample_size"] == 0


def test_fallback_setups_have_conservative_win_rates():
    """Fallback win rates should be conservative."""
    for setup_name, config in FALLBACK_SETUPS.items():
        # All fallback rates should be 0.65 or lower (conservative)
        assert config["win_rate"] <= 0.65, f"{setup_name} win rate too high"


# ------------------------------------------------------------------
# Edge Cases and Boundary Tests
# ------------------------------------------------------------------

def test_generate_with_invalid_market_state(gen_with_fallback):
    """Should handle invalid market state gracefully."""
    signal = gen_with_fallback.generate(
        market_state="INVALID_STATE",
        vwap_position="BELOW_SD1",
        delta_direction="POSITIVE",
        delta_flip=True,
        price_at_vwap_band=True,
        volume_spike=True,
        session_phase="MID",
        time_in_session_minutes=90
    )
    
    # Should return HOLD
    assert signal["action"] == "HOLD"


def test_generate_at_time_boundaries(gen_with_fallback):
    """Should correctly handle time boundaries."""
    # First 15 minutes
    signal_early = gen_with_fallback.generate(
        market_state="BALANCED",
        vwap_position="BELOW_SD1",
        delta_direction="POSITIVE",
        delta_flip=True,
        price_at_vwap_band=True,
        volume_spike=True,
        session_phase="OPEN",
        time_in_session_minutes=10  # Within first 15 min
    )
    
    assert signal_early["action"] == "HOLD"
    assert "TIME_FILTER" in signal_early["notes"]
    
    # Last 15 minutes
    signal_late = gen_with_fallback.generate(
        market_state="BALANCED",
        vwap_position="BELOW_SD1",
        delta_direction="POSITIVE",
        delta_flip=True,
        price_at_vwap_band=True,
        volume_spike=True,
        session_phase="CLOSE",
        time_in_session_minutes=380  # After 375 = 6.25 hours
    )
    
    assert signal_late["action"] == "HOLD"


def test_low_activity_blocks_signals(gen_with_fallback):
    """Low activity market state should block all signals."""
    signal = gen_with_fallback.generate(
        market_state="LOW_ACTIVITY",
        vwap_position="BELOW_SD1",
        delta_direction="POSITIVE",
        delta_flip=True,
        price_at_vwap_band=True,
        volume_spike=True,
        session_phase="MID",
        time_in_session_minutes=90
    )
    
    assert signal["action"] == "HOLD"
    assert "LOW_ACTIVITY" in signal["notes"]


def test_volatile_trans_blocks_signals(gen_with_fallback):
    """Volatile transition should block signals."""
    signal = gen_with_fallback.generate(
        market_state="VOLATILE_TRANS",
        vwap_position="BELOW_SD1",
        delta_direction="POSITIVE",
        delta_flip=True,
        price_at_vwap_band=True,
        volume_spike=True,
        session_phase="MID",
        time_in_session_minutes=90
    )
    
    assert signal["action"] == "HOLD"
    assert "VOLATILE_TRANS" in signal["notes"]


# ------------------------------------------------------------------
# Signal Structure Tests
# ------------------------------------------------------------------

def test_signal_has_required_fields(gen_with_fallback):
    """All signals must have required fields."""
    signal = gen_with_fallback.generate(
        market_state="BALANCED",
        vwap_position="BELOW_SD1",
        delta_direction="POSITIVE",
        delta_flip=True,
        price_at_vwap_band=True,
        volume_spike=True,
        session_phase="MID",
        time_in_session_minutes=90
    )
    
    required_fields = ["action", "setup_type", "confidence", "notes", "target", "stop", "rr_ratio"]
    
    if signal["action"] != "HOLD":
        for field in required_fields:
            assert field in signal, f"Missing field: {field}"


def test_confidence_in_valid_range(gen_with_fallback):
    """Confidence must always be between 0 and 1."""
    signal = gen_with_fallback.generate(
        market_state="BALANCED",
        vwap_position="BELOW_SD1",
        delta_direction="POSITIVE",
        delta_flip=True,
        price_at_vwap_band=True,
        volume_spike=True,
        session_phase="MID",
        time_in_session_minutes=90
    )
    
    assert 0.0 <= signal["confidence"] <= 1.0
