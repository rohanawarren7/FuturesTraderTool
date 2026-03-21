"""
PropFirmSimulator test suite.
Tests all 5 scenarios from the accelerated plan's Instruction 2.2,
plus additional coverage of the Bug 1 and Bug 2 fixes.

Run:
    pytest tests/test_prop_firm_simulator.py -v
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from core.prop_firm_simulator import PropFirmSimulator
from config.prop_firm_configs import PROP_FIRM_CONFIGS


# ------------------------------------------------------------------
# Test 1: Topstep EOD trailing — MLL floor after profit
# ------------------------------------------------------------------

def test_topstep_mll_floor_after_profit():
    """
    TOPSTEP_50K: after $1,500 profit (EOD balance = $51,500),
    MLL floor should be peak_eod_balance - max_loss_limit = $51,500 - $2,000 = $49,500.
    """
    sim = PropFirmSimulator(PROP_FIRM_CONFIGS["TOPSTEP_50K"])
    sim.close_day(51_500, "day1")
    floor = sim.get_mll_floor()
    assert floor == pytest.approx(49_500), f"Expected $49,500, got ${floor:,.2f}"


# ------------------------------------------------------------------
# Test 2: MLL breach triggered correctly
# ------------------------------------------------------------------

def test_topstep_mll_breach():
    """
    After profit pushes floor to $49,500, equity dropping to $49,400
    should trigger account_blown = True.
    """
    sim = PropFirmSimulator(PROP_FIRM_CONFIGS["TOPSTEP_50K"])
    sim.close_day(51_500, "day1")

    # Equity drops to $49,400 — below the $49,500 floor
    sim.update_intraday(49_400)
    assert sim.account_blown is True
    assert "MLL_BREACH" in (sim.breach_reason or "")


def test_topstep_no_breach_above_floor():
    """Equity at exactly the MLL floor should NOT trigger a breach."""
    sim = PropFirmSimulator(PROP_FIRM_CONFIGS["TOPSTEP_50K"])
    sim.close_day(51_500, "day1")

    sim.update_intraday(49_500)  # exactly at floor
    assert sim.account_blown is False


# ------------------------------------------------------------------
# Test 3: Payout eligibility after 5 winning days
# ------------------------------------------------------------------

def test_topstep_payout_eligible_after_5_winning_days():
    """
    Topstep: 5 consecutive winning days each >= $200 net
    should make check_payout_eligible() return True with amount > 0.
    """
    sim = PropFirmSimulator(PROP_FIRM_CONFIGS["TOPSTEP_50K"])
    balance = 50_000

    for i in range(5):
        balance += 250  # $250 net profit each day
        sim.close_day(balance, f"day{i+1}")

    eligible, amount = sim.check_payout_eligible()
    assert eligible is True, "Should be payout eligible after 5 qualifying days"
    assert amount > 0, f"Payout amount should be > 0, got {amount}"


def test_topstep_not_eligible_before_5_days():
    """Only 4 winning days — should NOT be eligible."""
    sim = PropFirmSimulator(PROP_FIRM_CONFIGS["TOPSTEP_50K"])
    balance = 50_000
    for i in range(4):
        balance += 250
        sim.close_day(balance, f"day{i+1}")

    eligible, _ = sim.check_payout_eligible()
    assert eligible is False


# ------------------------------------------------------------------
# Test 4: Apex intraday trailing — locks at threshold
# ------------------------------------------------------------------

def test_apex_trailing_locks_at_threshold():
    """
    APEX_50K: trailing_stops_at_profit = $2,500.
    Once profit >= $2,500, MLL floor should lock at account_size - max_loss_limit
    = $50,000 - $2,500 = $47,500 (not trail higher).
    """
    sim = PropFirmSimulator(PROP_FIRM_CONFIGS["APEX_50K"])

    # Simulate profit of $2,600 (exceeds $2,500 threshold)
    sim.balance = 52_600
    sim.peak_intraday_equity = 52_600
    sim.update_intraday(52_600)

    floor = sim.get_mll_floor()
    expected_locked_floor = 50_000 - 2_500  # = 47,500
    assert floor == pytest.approx(expected_locked_floor), (
        f"Floor should be locked at ${expected_locked_floor:,}, got ${floor:,.2f}"
    )


def test_apex_trailing_before_threshold():
    """Before the trailing lock, floor should trail peak intraday equity."""
    sim = PropFirmSimulator(PROP_FIRM_CONFIGS["APEX_50K"])

    # Peak intraday equity at $51,000 (profit = $1,000, below $2,500 threshold)
    sim.peak_intraday_equity = 51_000
    floor = sim.get_mll_floor()
    expected = 51_000 - 2_500  # = 48,500
    assert floor == pytest.approx(expected)


# ------------------------------------------------------------------
# Test 5: FTMO daily loss breach
# ------------------------------------------------------------------

def test_ftmo_daily_loss_breach():
    """
    FTMO_100K: daily_loss_limit = $5,000.
    If equity drops $5,100 below opening balance, breach should fire.
    """
    sim = PropFirmSimulator(PROP_FIRM_CONFIGS["FTMO_100K"])
    # opening_balance_today defaults to account_size = $100,000
    # Equity drops to $94,900 — $5,100 below opening
    sim.update_intraday(94_900)
    assert sim.account_blown is True
    assert "DAILY_LOSS" in (sim.breach_reason or "")


def test_ftmo_no_breach_within_daily_limit():
    """FTMO: $4,999 loss should NOT trigger breach."""
    sim = PropFirmSimulator(PROP_FIRM_CONFIGS["FTMO_100K"])
    sim.update_intraday(95_001)  # $4,999 below opening $100,000
    assert sim.account_blown is False


# ------------------------------------------------------------------
# Bug 1 fix: daily_pnl accuracy after multiple days
# ------------------------------------------------------------------

def test_daily_pnl_tracks_opening_balance():
    """
    Bug 1 regression: daily_pnl must be computed against each day's opening
    balance, not always against account_size.
    """
    sim = PropFirmSimulator(PROP_FIRM_CONFIGS["TOPSTEP_50K"])

    # Day 1: $200 profit
    sim.close_day(50_200, "day1")
    assert sim.daily_pnl == pytest.approx(200), "Day 1 PnL should be $200"

    # Day 2: $150 loss from $50,200 opening
    sim.close_day(50_050, "day2")
    assert sim.daily_pnl == pytest.approx(-150), (
        f"Day 2 PnL should be -$150, got {sim.daily_pnl}"
    )


# ------------------------------------------------------------------
# Bug 2 fix: Apex consistency rule fires intraday
# ------------------------------------------------------------------

def test_apex_consistency_rule_fires_intraday():
    """
    Bug 2 regression: Apex consistency rule (no single day > 30% of total profit)
    must fire during the session via update_intraday(), not only at EOD.
    """
    sim = PropFirmSimulator(PROP_FIRM_CONFIGS["APEX_50K"])

    # Accumulate total profit over several days
    sim.close_day(50_500, "day1")  # $500 profit
    sim.close_day(51_000, "day2")  # $500 profit
    sim.close_day(51_500, "day3")  # $500 profit
    # Total PnL = $1,500

    # Intraday: today gaining $600 = 40% of $1,500 total — exceeds 30% rule
    sim.update_intraday(52_100)  # $600 intraday gain from $51,500 opening

    assert sim.account_blown is True
    assert "CONSISTENCY" in (sim.breach_reason or ""), (
        f"Expected CONSISTENCY breach, got: {sim.breach_reason}"
    )


# ------------------------------------------------------------------
# Combine pass detection
# ------------------------------------------------------------------

def test_combine_pass_detected():
    """Account should transition to FUNDED once profit_target is reached."""
    sim = PropFirmSimulator(PROP_FIRM_CONFIGS["TOPSTEP_50K"])
    sim.close_day(53_000, "day_final")  # $3,000 profit — exactly at target
    assert sim.combine_passed is True
    assert sim.status == "FUNDED"


# ------------------------------------------------------------------
# Contract limit
# ------------------------------------------------------------------

def test_contract_limit_enforced():
    """Requesting more than max_contracts should be capped."""
    sim = PropFirmSimulator(PROP_FIRM_CONFIGS["TOPSTEP_50K"])
    assert sim.check_contract_limit(10) == 5   # max = 5
    assert sim.check_contract_limit(2) == 2    # below max
    assert sim.check_contract_limit(5) == 5    # exactly at max


def test_ftmo_no_contract_limit():
    """FTMO has no contract limit — should return requested amount."""
    sim = PropFirmSimulator(PROP_FIRM_CONFIGS["FTMO_100K"])
    assert sim.check_contract_limit(50) == 50
