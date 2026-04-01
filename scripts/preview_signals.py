#!/usr/bin/env python3
"""
Signal Preview Tool
==================

Shows what trading signals will be generated BEFORE you go live.
Run this to test the fallback strategy with various market conditions.

Usage:
    python scripts/preview_signals.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.signal_generator import SignalGenerator, FALLBACK_SETUPS
from core.risk_manager import RiskManager
from core.position_sizer import PositionSizer
from config.prop_firm_configs import PROP_FIRM_CONFIGS
from config.instrument_specs import INSTRUMENT_SPECS


def print_header(text):
    print("\n" + "="*70)
    print(text.center(70))
    print("="*70 + "\n")


def print_section(text):
    print("\n" + "-"*70)
    print(text)
    print("-"*70)


def test_scenario(name, market_state, vwap_position, delta_direction, delta_flip, 
                  volume_spike, time_minutes, expected_action="HOLD"):
    """Test a specific market scenario."""
    
    gen = SignalGenerator(video_trade_count=0, use_fallback=True)
    
    signal = gen.generate(
        market_state=market_state,
        vwap_position=vwap_position,
        delta_direction=delta_direction,
        delta_flip=delta_flip,
        price_at_vwap_band=True,
        volume_spike=volume_spike,
        session_phase="MID",
        time_in_session_minutes=time_minutes
    )
    
    status = "✓" if signal['action'] != 'HOLD' else "○"
    mode = gen.get_signal_mode()
    
    print(f"{status} {name}")
    print(f"   Market: {market_state} | VWAP: {vwap_position} | Delta: {delta_direction}")
    
    if signal['action'] != 'HOLD':
        print(f"   → SIGNAL: {signal['action']} {signal['setup_type']}")
        print(f"   → Confidence: {signal['confidence']:.0%} ({mode})")
        print(f"   → Target: {signal.get('target', 'N/A')} | Stop: {signal.get('stop', 'N/A')}")
    else:
        print(f"   → HOLD: {signal.get('notes', 'No signal')}")
    
    print()


def main():
    print_header("SIGNAL PREVIEW - FALLBACK STRATEGY")
    print("Testing strategy WITHOUT video data (fallback mode)")
    print("Strategy activates automatically when < 30 labeled trades")
    print()
    
    # Show fallback configurations
    print_section("FALLBACK SETUP CONFIGURATIONS")
    
    for setup_name, config in FALLBACK_SETUPS.items():
        print(f"\n{setup_name}:")
        print(f"  Win Rate: {config['win_rate']:.0%}")
        print(f"  {config['description']}")
        print(f"  Conditions: {config['conditions']}")
    
    # Test different scenarios
    print_section("SCENARIO TESTING - VALID SIGNALS")
    
    test_scenario(
        "MEAN REVERSION LONG (SD1 Support)",
        market_state="BALANCED",
        vwap_position="BELOW_SD1",
        delta_direction="POSITIVE",
        delta_flip=True,
        volume_spike=True,
        time_minutes=90
    )
    
    test_scenario(
        "MEAN REVERSION SHORT (SD1 Resistance)",
        market_state="BALANCED",
        vwap_position="ABOVE_SD1",
        delta_direction="NEGATIVE",
        delta_flip=True,
        volume_spike=True,
        time_minutes=90
    )
    
    test_scenario(
        "SD2 EXTREME FADE LONG",
        market_state="BALANCED",
        vwap_position="BELOW_SD2",
        delta_direction="POSITIVE",
        delta_flip=False,
        volume_spike=True,
        time_minutes=120
    )
    
    test_scenario(
        "SD2 EXTREME FADE SHORT",
        market_state="BALANCED",
        vwap_position="ABOVE_SD2",
        delta_direction="NEGATIVE",
        delta_flip=False,
        volume_spike=True,
        time_minutes=120
    )
    
    test_scenario(
        "VWAP CONTINUATION LONG (Trending)",
        market_state="IMBALANCED_BULL",
        vwap_position="ABOVE_VWAP",
        delta_direction="POSITIVE",
        delta_flip=False,
        volume_spike=True,
        time_minutes=100
    )
    
    test_scenario(
        "VWAP CONTINUATION SHORT (Trending)",
        market_state="IMBALANCED_BEAR",
        vwap_position="BELOW_VWAP",
        delta_direction="NEGATIVE",
        delta_flip=False,
        volume_spike=True,
        time_minutes=100
    )
    
    print_section("SCENARIO TESTING - BLOCKED SIGNALS")
    
    test_scenario(
        "TOO EARLY (First 15 min)",
        market_state="BALANCED",
        vwap_position="BELOW_SD1",
        delta_direction="POSITIVE",
        delta_flip=True,
        volume_spike=True,
        time_minutes=10  # Too early
    )
    
    test_scenario(
        "TOO LATE (Last 15 min)",
        market_state="BALANCED",
        vwap_position="BELOW_SD1",
        delta_direction="POSITIVE",
        delta_flip=True,
        volume_spike=True,
        time_minutes=380  # Too late
    )
    
    test_scenario(
        "LOW ACTIVITY MARKET",
        market_state="LOW_ACTIVITY",
        vwap_position="BELOW_SD1",
        delta_direction="POSITIVE",
        delta_flip=True,
        volume_spike=True,
        time_minutes=90
    )
    
    test_scenario(
        "NO DELTA FLIP",
        market_state="BALANCED",
        vwap_position="BELOW_SD1",
        delta_direction="POSITIVE",
        delta_flip=False,  # No flip
        volume_spike=True,
        time_minutes=90
    )
    
    # Risk management testing
    print_section("RISK MANAGEMENT TESTING")
    
    config = PROP_FIRM_CONFIGS["TOPSTEP_50K"]
    risk_mgr = RiskManager(config)
    
    print("\nPosition Size Calculation:")
    print("-" * 50)
    
    test_cases = [
        (50000, 7.5, 5.0, 0.7, "Normal volatility"),
        (50000, 3.0, 2.0, 0.9, "Low volatility, high confidence"),
        (50000, 15.0, 10.0, 0.5, "High volatility, low confidence"),
    ]
    
    for equity, atr, stop, conf, desc in test_cases:
        contracts = risk_mgr.calculate_position_size(
            account_equity=equity,
            atr=atr,
            stop_distance=stop,
            point_value=5,
            confidence=conf
        )
        print(f"  {desc}:")
        print(f"    → {contracts} contracts (ATR={atr}, Stop={stop}, Conf={conf})")
    
    print("\nRisk Limits:")
    print("-" * 50)
    
    # Test daily trade limit
    risk_mgr.daily_trade_count = 5
    from datetime import datetime
    allowed, reason = risk_mgr.can_trade(
        {"timestamp": datetime.now(), "hour": 10},
        {"equity": 50000, "mll_floor": 48000}
    )
    print(f"  Daily trade limit (5/5 trades): {'BLOCKED' if not allowed else 'ALLOWED'}")
    
    # Test consecutive losses
    risk_mgr.reset_daily_counters(datetime.now().date())
    risk_mgr.consecutive_losses = 3
    allowed, reason = risk_mgr.can_trade(
        {"timestamp": datetime.now(), "hour": 10},
        {"equity": 50000, "mll_floor": 48000}
    )
    print(f"  Consecutive losses (3/3): {'BLOCKED' if not allowed else 'ALLOWED'}")
    
    # Test MLL proximity
    risk_mgr.consecutive_losses = 0
    allowed, reason = risk_mgr.can_trade(
        {"timestamp": datetime.now(), "hour": 10},
        {"equity": 48200, "mll_floor": 48000}  # Close to floor
    )
    print(f"  MLL proximity ($200 from floor): {'BLOCKED' if not allowed else 'ALLOWED'}")
    
    # Summary
    print_header("STRATEGY SUMMARY")
    
    print("FALLBACK MODE ACTIVE:")
    print("  • Conservative win rates (55-65%)")
    print("  • 3 proven VWAP setups")
    print("  • Automatic time filtering")
    print("  • Position sizing with Kelly criterion")
    print("  • 8 circuit breakers for protection")
    print()
    
    print("WHEN TO TRUST SIGNALS:")
    print("  • Confidence >= 60%: Good setup")
    print("  • Confidence >= 70%: Strong setup")
    print("  • Confidence < 60%: Skip or reduce size")
    print()
    
    print("NEXT STEPS:")
    print("  1. Run this preview to understand signals")
    print("  2. Start with 1 contract for first week")
    print("  3. Log all trades with outcomes")
    print("  4. After 30+ trades: Switch to VIDEO_DERIVED")
    print("  5. Update confidence scores with real data")
    print()
    
    print("="*70)
    print("Ready to start paper trading with fallback strategy!")
    print("="*70)


if __name__ == "__main__":
    main()
