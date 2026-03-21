"""
Config validation tests.

Run:
    pytest tests/test_configs.py -v
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pytest
from config.prop_firm_configs import PROP_FIRM_CONFIGS
from config.bot_risk_params import BOT_RISK_PARAMS, COMBINE_RISK_PARAMS
from config.instrument_specs import INSTRUMENT_SPECS, get_front_month_symbol


def test_all_prop_firms_have_required_keys():
    required = ["firm", "account_size", "profit_target", "max_loss_limit",
                "trailing_type", "profit_split"]
    for key, cfg in PROP_FIRM_CONFIGS.items():
        for field in required:
            assert field in cfg, f"{key} missing field: {field}"


def test_profit_target_less_than_account_size():
    for key, cfg in PROP_FIRM_CONFIGS.items():
        assert cfg["profit_target"] < cfg["account_size"], (
            f"{key}: profit_target ({cfg['profit_target']}) should be < "
            f"account_size ({cfg['account_size']})"
        )


def test_max_loss_limit_less_than_account_size():
    for key, cfg in PROP_FIRM_CONFIGS.items():
        assert cfg["max_loss_limit"] < cfg["account_size"]


def test_profit_split_between_zero_and_one():
    for key, cfg in PROP_FIRM_CONFIGS.items():
        assert 0 < cfg["profit_split"] <= 1.0, f"{key}: profit_split out of range"


def test_trailing_type_valid():
    valid = {"EOD", "INTRADAY"}
    for key, cfg in PROP_FIRM_CONFIGS.items():
        assert cfg["trailing_type"] in valid, (
            f"{key}: invalid trailing_type '{cfg['trailing_type']}'"
        )


def test_bot_risk_params_circuit_breaker_in_range():
    pct = BOT_RISK_PARAMS["daily_stop_loss_pct_of_mll"]
    assert 0 < pct < 1.0


def test_combine_params_more_conservative_than_default():
    assert COMBINE_RISK_PARAMS["daily_profit_target_usd"] <= BOT_RISK_PARAMS["daily_profit_target_usd"]
    assert COMBINE_RISK_PARAMS["max_contracts_in_use"] <= BOT_RISK_PARAMS["max_contracts_in_use"]


def test_instrument_specs_tick_values():
    for name, spec in INSTRUMENT_SPECS.items():
        assert spec["tick_value"] > 0, f"{name}: tick_value must be positive"
        assert spec["point_value"] == spec["tick_value"] / spec["tick_size"], (
            f"{name}: point_value should equal tick_value / tick_size"
        )


def test_get_front_month_symbol():
    assert get_front_month_symbol("MES", 25, "M") == "MESM5"
    assert get_front_month_symbol("MNQ", 25, "U") == "MNQU5"
