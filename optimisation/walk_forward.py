"""
Walk-Forward Optimiser
Rolls a 4-month in-sample / 1-month out-of-sample window across history.
Optimises parameters on IS data with Optuna, validates on OOS.
Computes Walk-Forward Efficiency (WFE) = avg_OOS_score / avg_IS_score.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import optuna
import pandas as pd
import numpy as np
from datetime import datetime, timedelta
from typing import Optional
import json

from backtesting.backtest_runner import BacktestRunner

# Suppress Optuna's verbose logging by default
optuna.logging.set_verbosity(optuna.logging.WARNING)


class WalkForwardOptimiser:
    """
    Rolls a training window across history and validates each set of
    optimised parameters on unseen out-of-sample data.
    """

    IS_MONTHS = 4
    OOS_MONTHS = 1
    N_TRIALS = 100

    PARAM_SPACE = {
        "sd_mult_entry":    (0.75, 2.0),
        "sd_mult_stop":     (1.0, 2.5),
        "rr_ratio":         (1.5, 3.5),
        "delta_threshold":  (100, 800),
        "volume_threshold": (1.5, 3.0),
        "contracts":        (1, 3),
    }

    def __init__(self, prop_firm_key: str, instrument: str = "MES"):
        self.prop_firm_key = prop_firm_key
        self.instrument = instrument

    # ------------------------------------------------------------------
    # Single window optimisation
    # ------------------------------------------------------------------

    def optimise_window(
        self, df_is: pd.DataFrame, n_trials: Optional[int] = None
    ) -> dict:
        """
        Runs Optuna Bayesian optimisation on in-sample data.
        Returns best params dict.

        Bug 5 fix: n_jobs goes on study.optimize(), not create_study().
        """
        n_trials = n_trials or self.N_TRIALS

        def objective(trial: optuna.Trial) -> float:
            params = {
                "sd_mult_entry":    trial.suggest_float(
                    "sd_mult_entry", *self.PARAM_SPACE["sd_mult_entry"]),
                "sd_mult_stop":     trial.suggest_float(
                    "sd_mult_stop", *self.PARAM_SPACE["sd_mult_stop"]),
                "rr_ratio":         trial.suggest_float(
                    "rr_ratio", *self.PARAM_SPACE["rr_ratio"]),
                "delta_threshold":  trial.suggest_int(
                    "delta_threshold", *self.PARAM_SPACE["delta_threshold"]),
                "volume_threshold": trial.suggest_float(
                    "volume_threshold", *self.PARAM_SPACE["volume_threshold"]),
                "contracts":        trial.suggest_int(
                    "contracts", *self.PARAM_SPACE["contracts"]),
            }

            runner = BacktestRunner(self.prop_firm_key, self.instrument)
            result = runner.run(df_is, params)

            if result.get("account_blown") or result.get("error") or result.get("total_trades", 0) < 5:
                return -999.0

            # Composite score: balance rule adherence + profitability + consistency
            score = (
                result.get("profit_factor", 0) * 0.30
                + result.get("win_rate", 0) * 0.30
                + result.get("sharpe_ratio", 0) * 0.20
                + (1 - abs(result.get("max_drawdown", 1))) * 0.20
            )
            return float(score)

        study = optuna.create_study(direction="maximize")
        # Bug 5 fix: n_jobs belongs on optimize(), not create_study()
        study.optimize(objective, n_trials=n_trials, n_jobs=1,
                       show_progress_bar=False)

        return study.best_params

    # ------------------------------------------------------------------
    # Full WFO
    # ------------------------------------------------------------------

    def run_full_wfo(self, df: pd.DataFrame) -> dict:
        """
        Rolls IS/OOS windows across the full dataset.
        Returns composite OOS stats and WFE score.
        """
        df = df.copy()
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        start = df["timestamp"].min()
        end = df["timestamp"].max()

        # Build windows
        windows = []
        cursor = start
        window_days = 30 * (self.IS_MONTHS + self.OOS_MONTHS)
        while cursor + timedelta(days=window_days) <= end:
            is_end = cursor + timedelta(days=30 * self.IS_MONTHS)
            oos_end = is_end + timedelta(days=30 * self.OOS_MONTHS)
            windows.append({
                "is_start":  cursor,
                "is_end":    is_end,
                "oos_start": is_end,
                "oos_end":   oos_end,
            })
            cursor = is_end  # Non-overlapping: move IS forward by IS_MONTHS

        if not windows:
            return {"error": "Insufficient data for WFO — need at least "
                    f"{self.IS_MONTHS + self.OOS_MONTHS} months of data."}

        print(f"[WFO] {len(windows)} windows to test")

        is_scores: list[float] = []
        oos_scores: list[float] = []
        window_results: list[dict] = []

        for idx, w in enumerate(windows):
            df_is = df[(df["timestamp"] >= w["is_start"]) &
                       (df["timestamp"] < w["is_end"])]
            df_oos = df[(df["timestamp"] >= w["oos_start"]) &
                        (df["timestamp"] < w["oos_end"])]

            if len(df_is) < 1_000 or len(df_oos) < 200:
                print(f"[WFO] Window {idx+1}: insufficient bars, skipping.")
                continue

            print(f"[WFO] Window {idx+1}/{len(windows)}: "
                  f"IS {w['is_start'].date()} → {w['is_end'].date()} | "
                  f"OOS {w['oos_start'].date()} → {w['oos_end'].date()}")

            best_params = self.optimise_window(df_is)

            is_runner = BacktestRunner(self.prop_firm_key, self.instrument)
            is_result = is_runner.run(df_is, best_params)
            is_pf = is_result.get("profit_factor", 0)
            is_scores.append(is_pf)

            oos_runner = BacktestRunner(self.prop_firm_key, self.instrument)
            oos_result = oos_runner.run(df_oos, best_params)
            oos_pf = oos_result.get("profit_factor", 0)
            oos_scores.append(oos_pf)

            print(f"  IS PF: {is_pf:.2f} | OOS PF: {oos_pf:.2f} | "
                  f"Params: {best_params}")

            window_results.append({
                "window":      idx + 1,
                "is_start":    str(w["is_start"].date()),
                "oos_end":     str(w["oos_end"].date()),
                "is_pf":       round(is_pf, 3),
                "oos_pf":      round(oos_pf, 3),
                "best_params": best_params,
                "oos_result":  {k: v for k, v in oos_result.items()
                                if k not in ("equity_curve",)},
            })

        avg_is = float(np.mean(is_scores)) if is_scores else 0.0
        avg_oos = float(np.mean(oos_scores)) if oos_scores else 0.0
        wfe = avg_oos / avg_is if avg_is > 0 else 0.0

        assessment = (
            "EXCELLENT" if wfe > 0.70
            else ("GOOD" if wfe > 0.50
                  else ("MARGINAL — reduce free params" if wfe > 0.35
                        else "FAIL — strategy over-fitting"))
        )

        # Best params = from the window with the highest OOS profit factor
        best_window = max(window_results, key=lambda w: w["oos_pf"],
                          default={}) if window_results else {}

        return {
            "windows_tested":       len(window_results),
            "avg_is_profit_factor": round(avg_is, 3),
            "avg_oos_profit_factor": round(avg_oos, 3),
            "wfe_score":            round(wfe, 3),
            "assessment":           assessment,
            "best_params":          best_window.get("best_params", {}),
            "window_results":       window_results,
        }
