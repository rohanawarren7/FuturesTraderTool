INSTRUMENT_SPECS = {
    "MES": {
        "name": "Micro E-mini S&P 500",
        "exchange": "CME",
        "tick_size": 0.25,
        "tick_value": 1.25,
        "point_value": 5.00,
        "margin_intraday": 40,
        "typical_spread_ticks": 1,
        "typical_daily_range_points": 50,
        "commission_per_side": 1.58,  # $3.16 round-turn (Tradovate + CME + NFA fees)
        "data_source": "Tradovate API",
        "roll_months": ["H", "M", "U", "Z"],  # Mar, Jun, Sep, Dec
    },
    "MNQ": {
        "name": "Micro E-mini Nasdaq 100",
        "exchange": "CME",
        "tick_size": 0.25,
        "tick_value": 0.50,
        "point_value": 2.00,
        "margin_intraday": 40,
        "typical_spread_ticks": 1,
        "typical_daily_range_points": 200,
        "commission_per_side": 1.58,  # $3.16 round-turn (Tradovate + CME + NFA fees)
        "data_source": "Tradovate API",
        "roll_months": ["H", "M", "U", "Z"],
    },
    "ES": {
        "name": "E-mini S&P 500",
        "exchange": "CME",
        "tick_size": 0.25,
        "tick_value": 12.50,
        "point_value": 50.00,
        "margin_intraday": 500,
        "typical_spread_ticks": 1,
        "typical_daily_range_points": 50,
        "commission_per_side": 1.50,
        "data_source": "Tradovate API",
        "roll_months": ["H", "M", "U", "Z"],
    },
    "NQ": {
        "name": "E-mini Nasdaq 100",
        "exchange": "CME",
        "tick_size": 0.25,
        "tick_value": 5.00,
        "point_value": 20.00,
        "margin_intraday": 500,
        "typical_spread_ticks": 1,
        "typical_daily_range_points": 200,
        "commission_per_side": 1.50,
        "data_source": "Tradovate API",
        "roll_months": ["H", "M", "U", "Z"],
    },
}

# Tradovate front-month symbol suffixes by expiry month code
ROLL_SUFFIX = {
    "H": "H",   # March
    "M": "M",   # June
    "U": "U",   # September
    "Z": "Z",   # December
}


def get_front_month_symbol(base: str, year_2digit: int, month_code: str) -> str:
    """
    Returns the Tradovate symbol for the front-month contract.
    e.g. get_front_month_symbol('MES', 25, 'M') -> 'MESM5'
    """
    return f"{base}{month_code}{year_2digit % 10}"
