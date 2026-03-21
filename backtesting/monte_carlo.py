"""
Monte Carlo Simulator
Takes a list of trade PnL values from a backtest and simulates N random orderings
to estimate probability of account ruin and the distribution of outcomes.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import pandas as pd
from typing import Optional

from config.prop_firm_configs import PROP_FIRM_CONFIGS
from core.prop_firm_simulator import PropFirmSimulator


class MonteCarloSimulator:
    """
    Shuffles trade sequences N times and runs each through PropFirmSimulator
    to estimate real-world ruin probability and outcome distribution.

    Key insight: a backtest trades in a fixed order, but the future is random.
    Monte Carlo reveals how sensitive results are to trade sequencing.
    """

    def __init__(self, prop_firm_key: str, n_simulations: int = 10_000):
        self.prop_firm_key = prop_firm_key
        self.prop_config = PROP_FIRM_CONFIGS[prop_firm_key]
        self.n_simulations = n_simulations

    def run(
        self,
        trade_pnls: list[float],
        daily_group_size: Optional[int] = None,
        seed: Optional[int] = None,
    ) -> dict:
        """
        Runs the Monte Carlo simulation.

        trade_pnls:       List of net PnL values per trade (in dollars).
        daily_group_size: If provided, shuffles in groups of this size
                          (approximates daily trade batches for more realistic sequences).
        seed:             Random seed for reproducibility.

        Returns a results dict with:
          - ruin_probability:  fraction of runs where account was blown
          - combine_pass_rate: fraction of runs where combine passed
          - percentiles:       5th, 25th, 50th, 75th, 95th of final PnL
          - runs:              N total simulations run
        """
        if not trade_pnls:
            raise ValueError("trade_pnls list is empty — run a backtest first.")

        rng = np.random.default_rng(seed)
        pnls = np.array(trade_pnls)

        blown_count = 0
        passed_count = 0
        final_pnls: list[float] = []

        for _ in range(self.n_simulations):
            shuffled = rng.permutation(pnls)

            # Run shuffled trade sequence through a fresh PropFirmSimulator
            sim = PropFirmSimulator(self.prop_config)

            if daily_group_size:
                # Process in day-sized batches with EOD settlement
                batches = [
                    shuffled[i : i + daily_group_size]
                    for i in range(0, len(shuffled), daily_group_size)
                ]
                for day_idx, batch in enumerate(batches):
                    for trade_pnl in batch:
                        sim.balance += trade_pnl
                        sim.update_intraday(sim.balance)
                        if sim.account_blown:
                            break
                    if sim.account_blown:
                        break
                    sim.close_day(sim.balance, str(day_idx))
            else:
                # Simple: apply each trade directly to balance
                for trade_pnl in shuffled:
                    sim.balance += trade_pnl
                    sim.update_intraday(sim.balance)
                    if sim.account_blown:
                        break
                sim.close_day(sim.balance, "final")

            if sim.account_blown:
                blown_count += 1
            if sim.combine_passed:
                passed_count += 1

            final_pnls.append(sim.total_pnl)

        final_pnls_arr = np.array(final_pnls)
        ruin_prob = blown_count / self.n_simulations
        pass_rate = passed_count / self.n_simulations

        return {
            "runs":                  self.n_simulations,
            "prop_firm":             self.prop_config["firm"],
            "total_trades_per_run":  len(trade_pnls),
            "ruin_probability":      round(ruin_prob, 4),
            "ruin_pct":              round(ruin_prob * 100, 2),
            "combine_pass_rate":     round(pass_rate, 4),
            "combine_pass_pct":      round(pass_rate * 100, 2),
            "median_final_pnl":      round(float(np.median(final_pnls_arr)), 2),
            "percentiles": {
                "p5":  round(float(np.percentile(final_pnls_arr, 5)), 2),
                "p25": round(float(np.percentile(final_pnls_arr, 25)), 2),
                "p50": round(float(np.percentile(final_pnls_arr, 50)), 2),
                "p75": round(float(np.percentile(final_pnls_arr, 75)), 2),
                "p95": round(float(np.percentile(final_pnls_arr, 95)), 2),
            },
            "assessment": self._assess(ruin_prob, pass_rate),
        }

    @staticmethod
    def _assess(ruin_prob: float, pass_rate: float) -> str:
        if ruin_prob < 0.05 and pass_rate > 0.70:
            return "EXCELLENT"
        if ruin_prob < 0.10 and pass_rate > 0.55:
            return "GOOD"
        if ruin_prob < 0.15 and pass_rate > 0.40:
            return "MARGINAL"
        return "FAIL — do not deploy"

    def print_report(self, results: dict):
        print(f"\n{'='*55}")
        print(f"MONTE CARLO RESULTS — {results['prop_firm']}")
        print(f"{'='*55}")
        print(f"Simulations run:      {results['runs']:,}")
        print(f"Trades per sim:       {results['total_trades_per_run']}")
        print(f"Ruin probability:     {results['ruin_pct']:.1f}%")
        print(f"Combine pass rate:    {results['combine_pass_pct']:.1f}%")
        print(f"Median final PnL:     ${results['median_final_pnl']:,.0f}")
        print(f"\nFinal PnL distribution:")
        for label, pct in [("5th", "p5"), ("25th", "p25"), ("50th", "p50"),
                            ("75th", "p75"), ("95th", "p95")]:
            print(f"  {label} percentile:   ${results['percentiles'][pct]:>10,.0f}")
        print(f"\nAssessment: {results['assessment']}")
        print(f"{'='*55}\n")
