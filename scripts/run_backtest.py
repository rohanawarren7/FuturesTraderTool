#!/usr/bin/env python3
"""
Backtest Runner CLI

Usage:
    python scripts/run_backtest.py --firm TOPSTEP_50K --instrument MES
    python scripts/run_backtest.py --firm APEX_50K --params '{"rr_ratio": 2.5}'
    python scripts/run_backtest.py --all-firms   # runs all three prop firms
"""

import argparse
import json
import os
import sys
import uuid
from pathlib import Path
from datetime import datetime

sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
load_dotenv()

from data.tradovate_data_provider import TradovateDataProvider, get_current_front_month
from backtesting.backtest_runner import BacktestRunner
from backtesting.monte_carlo import MonteCarloSimulator
from database.db_manager import DBManager

RESULTS_DIR = Path("backtesting/results")
RESULTS_DIR.mkdir(exist_ok=True)

DEFAULT_PARAMS = {
    "sd_mult_entry":    1.0,
    "sd_mult_stop":     1.5,
    "rr_ratio":         2.0,
    "delta_threshold":  300,
    "volume_threshold": 1.8,
    "contracts":        1,
}


def run_single(firm_key: str, instrument: str, df, params: dict,
               db: DBManager, run_monte_carlo: bool = True) -> dict:
    print(f"\n{'='*60}")
    print(f"BACKTEST: {firm_key} | {instrument} | {len(df)} bars")
    print(f"Params: {params}")
    print(f"{'='*60}")

    runner = BacktestRunner(firm_key, instrument)
    result = runner.run(df, params)

    if result.get("error"):
        print(f"[ERROR] {result['error']}")
        return result

    # Monte Carlo
    mc_result = None
    if run_monte_carlo and runner.trades:
        pnls = [t["net_pnl"] for t in runner.trades]
        mc = MonteCarloSimulator(firm_key, n_simulations=5_000)
        mc_result = mc.run(pnls, daily_group_size=5)
        mc.print_report(mc_result)
        result["monte_carlo_ruin_pct"] = mc_result["ruin_pct"]
        result["monte_carlo_pass_pct"] = mc_result["combine_pass_pct"]

    # Print results
    print(f"\nRESULTS:")
    print(f"  Total trades:    {result.get('total_trades', 0)}")
    print(f"  Win rate:        {result.get('win_rate', 0)*100:.1f}%")
    print(f"  Profit factor:   {result.get('profit_factor', 0):.2f}")
    print(f"  Sharpe ratio:    {result.get('sharpe_ratio', 0):.2f}")
    print(f"  Max drawdown:    {result.get('max_drawdown', 0)*100:.1f}%")
    print(f"  Final PnL:       ${result.get('total_pnl', 0):,.0f}")
    print(f"  Combine passed:  {'YES' if result.get('combine_passed') else 'NO'}")
    print(f"  Account blown:   {'YES' if result.get('account_blown') else 'NO'}")
    if result.get("breach_reason"):
        print(f"  Breach reason:   {result['breach_reason']}")
    if mc_result:
        print(f"  MC Ruin prob:    {mc_result['ruin_pct']:.1f}%")
        print(f"  MC Pass rate:    {mc_result['combine_pass_pct']:.1f}%")

    # GO/NO-GO
    go = (
        result.get("win_rate", 0) >= 0.52
        and result.get("profit_factor", 0) >= 1.3
        and not result.get("account_blown")
        and (not mc_result or mc_result["ruin_pct"] < 15)
    )
    print(f"\n  GO / NO-GO:      {'✓ GO' if go else '✗ NO-GO'}")

    # Save to DB
    run_id = str(uuid.uuid4())[:8]
    db.insert_backtest_result({
        "run_id":             run_id,
        "prop_firm":          firm_key,
        "account_size":       result.get("account_size"),
        "strategy_version":   "1.0",
        "start_date":         str(df["timestamp"].min())[:10],
        "end_date":           str(df["timestamp"].max())[:10],
        "instrument":         instrument,
        "timeframe":          "5m",
        "total_trades":       result.get("total_trades", 0),
        "winning_trades":     result.get("winning_trades", 0),
        "losing_trades":      result.get("losing_trades", 0),
        "win_rate":           result.get("win_rate", 0),
        "profit_factor":      result.get("profit_factor", 0),
        "sharpe_ratio":       result.get("sharpe_ratio", 0),
        "max_drawdown":       result.get("max_drawdown", 0),
        "final_pnl":          result.get("total_pnl", 0),
        "combine_passed":     result.get("combine_passed", False),
        "account_blown":      result.get("account_blown", False),
        "breach_reason":      result.get("breach_reason"),
        "wfe_score":          None,
        "monte_carlo_ruin_pct": result.get("monte_carlo_ruin_pct"),
        "params":             params,
    })
    print(f"  Saved to DB: run_id={run_id}")

    # Save JSON
    out_path = RESULTS_DIR / f"{run_id}_{firm_key}_{instrument}.json"
    out_path.write_text(json.dumps({**result, "params": params}, indent=2, default=str))
    print(f"  Saved to file: {out_path}")

    return result


def main():
    parser = argparse.ArgumentParser(description="Run VWAP strategy backtest")
    parser.add_argument("--firm", default="TOPSTEP_50K",
                        help="Prop firm key (default: TOPSTEP_50K)")
    parser.add_argument("--instrument", default="MES",
                        help="Instrument key (default: MES)")
    parser.add_argument("--data", default=None,
                        help="Path to CSV file (default: fetch from Tradovate)")
    parser.add_argument("--params", default=None,
                        help="JSON string of strategy params (optional)")
    parser.add_argument("--all-firms", action="store_true",
                        help="Run all supported prop firms")
    parser.add_argument("--no-monte-carlo", action="store_true",
                        help="Skip Monte Carlo simulation (faster)")
    args = parser.parse_args()

    db_path = os.getenv("DB_PATH", "./database/trading_analysis.db")
    db = DBManager(db_path)

    params = DEFAULT_PARAMS.copy()
    if args.params:
        params.update(json.loads(args.params))

    # Load data
    if args.data:
        import pandas as pd
        df = pd.read_csv(args.data, parse_dates=["timestamp"])
        print(f"[Backtest] Loaded {len(df)} bars from {args.data}")
    else:
        print("[Backtest] Fetching data from Tradovate...")
        provider = TradovateDataProvider.from_env()
        symbol = get_current_front_month(args.instrument)
        df = provider.get_12_months_5m(symbol)

    firms = ["TOPSTEP_50K", "APEX_50K", "FTMO_100K"] if args.all_firms else [args.firm]

    for firm in firms:
        run_single(firm, args.instrument, df, params, db,
                   run_monte_carlo=not args.no_monte_carlo)


if __name__ == "__main__":
    main()
