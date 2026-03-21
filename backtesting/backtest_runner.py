"""
BacktestRunner
Runs the VWAP strategy against historical OHLCV data with full prop firm constraints.
Every signal check and trade execution passes through PropFirmSimulator in real time.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import numpy as np
from datetime import datetime

from config.prop_firm_configs import PROP_FIRM_CONFIGS
from config.instrument_specs import INSTRUMENT_SPECS
from core.prop_firm_simulator import PropFirmSimulator
from core.vwap_calculator import VWAPCalculator
from core.market_state_detector import MarketStateDetector
from core.signal_generator import SignalGenerator


class BacktestRunner:
    """
    Runs a full backtest of the VWAP strategy with prop firm constraints.

    Helper methods are fully implemented (not stubs):
      - Delta direction: derived from close vs open (bullish/bearish candle proxy)
      - Session phase:   derived from ET hour of bar timestamp
      - ATR and volume:  rolling 14/20-period calculations from OHLCV data
    """

    def __init__(
        self,
        prop_firm_key: str,
        instrument: str = "MES",
        timeframe: str = "5m",
    ):
        self.prop_config = PROP_FIRM_CONFIGS[prop_firm_key]
        self.prop_firm_key = prop_firm_key
        self.instrument_key = instrument
        self.instrument = INSTRUMENT_SPECS[instrument]
        self.timeframe = timeframe
        self.vwap_calc = VWAPCalculator()
        self.state_detector = MarketStateDetector()
        self.signal_gen = SignalGenerator()
        self.trades: list[dict] = []
        self.equity_curve: list[dict] = []

    def reset(self):
        self.trades = []
        self.equity_curve = []

    # ------------------------------------------------------------------
    # Main run
    # ------------------------------------------------------------------

    def run(self, df: pd.DataFrame, params: dict) -> dict:
        """
        Runs the backtest on df (5-minute OHLCV data).
        params keys: sd_mult_entry, sd_mult_stop, rr_ratio, volume_threshold, contracts
        Returns a results dict.
        """
        self.reset()
        prop_sim = PropFirmSimulator(self.prop_config)

        # Add VWAP bands + rolling indicators
        df = self.vwap_calc.calculate_session_vwap(df)
        df = self._add_indicators(df)

        daily_pnl = 0.0
        open_trade = None

        for i in range(50, len(df)):
            if prop_sim.account_blown:
                break

            row = df.iloc[i]
            open_pnl = self._calc_open_pnl(open_trade, row) if open_trade else 0.0
            current_equity = prop_sim.balance + open_pnl

            # Day boundary
            if i > 50 and row["date"] != df.iloc[i - 1]["date"]:
                # Close any open trade at prior bar's close (end-of-day flatten)
                if open_trade:
                    close_row = df.iloc[i - 1]
                    pnl = self._close_trade(open_trade, close_row["close"], "EOD_FLATTEN")
                    net = pnl - self._commission(open_trade["contracts"])
                    daily_pnl += net
                    prop_sim.balance += net
                    self.trades.append(self._trade_record(open_trade, close_row, net))
                    open_trade = None

                prop_sim.close_day(prop_sim.balance, str(df.iloc[i - 1]["date"]))
                daily_pnl = 0.0

            # Update prop firm intraday state
            prop_sim.update_intraday(current_equity)
            if prop_sim.account_blown:
                break

            # Daily circuit breaker
            safe_budget = prop_sim.get_safe_daily_loss_budget()
            if daily_pnl <= -safe_budget and open_trade is None:
                continue

            # Manage open trade
            if open_trade:
                result = self._manage_open_trade(open_trade, row)
                if result["closed"]:
                    net = result["pnl"] - self._commission(open_trade["contracts"])
                    daily_pnl += net
                    prop_sim.balance += net
                    self.trades.append(self._trade_record(open_trade, row, net,
                                                           r=result.get("r_multiple")))
                    open_trade = None
                else:
                    open_trade["unrealised_pnl"] = result["unrealised_pnl"]
                continue

            # Generate signal
            vwap_pos = VWAPCalculator.get_vwap_position(
                row["close"], row["vwap"],
                row["vwap_sd1_upper"], row["vwap_sd1_lower"],
                row["vwap_sd2_upper"], row["vwap_sd2_lower"],
            )
            market_state = self.state_detector.detect(
                vwap_position=vwap_pos,
                cumulative_delta=row["delta_proxy"],
                delta_direction=row["delta_direction"],
                atr_ratio=row["atr_ratio"],
                volume_ratio=row["volume_ratio"],
                price_crosses_vwap_last_10=int(row["vwap_crosses_10"]),
            )
            signal = self.signal_gen.generate(
                market_state=market_state,
                vwap_position=vwap_pos,
                delta_direction=row["delta_direction"],
                delta_flip=bool(row["delta_flip"]),
                price_at_vwap_band=(vwap_pos in ("ABOVE_SD1", "BELOW_SD1",
                                                   "ABOVE_SD2", "BELOW_SD2")),
                volume_spike=bool(row["volume_spike"]),
                session_phase=row["session_phase"],
                time_in_session_minutes=int(row["minutes_since_open"]),
            )

            if signal["action"] in ("BUY", "SELL"):
                contracts = prop_sim.check_contract_limit(
                    min(params.get("contracts", 1),
                        self.prop_config.get("max_contracts", 5))
                )
                sd_stop = params.get("sd_mult_stop", 1.5)
                sd_entry = params.get("sd_mult_entry", 1.0)
                stop_dist = row["vwap_std"] * sd_stop
                target_dist = stop_dist * params.get("rr_ratio", 2.0)

                if stop_dist <= 0:
                    continue

                direction = signal["action"]
                entry = row["close"]
                stop = entry - stop_dist if direction == "BUY" else entry + stop_dist
                target = entry + target_dist if direction == "BUY" else entry - target_dist

                open_trade = {
                    "direction":       direction,
                    "entry_price":     entry,
                    "entry_time":      str(row["timestamp"]),
                    "stop_price":      stop,
                    "target_price":    target,
                    "contracts":       contracts,
                    "setup_type":      signal["setup_type"],
                    "unrealised_pnl":  0.0,
                }

            self.equity_curve.append({
                "timestamp":  str(row["timestamp"]),
                "equity":     current_equity,
                "mll_floor":  prop_sim.get_mll_floor(),
            })

        # Final day close
        prop_sim.close_day(prop_sim.balance, "final")
        return self._generate_report(prop_sim)

    # ------------------------------------------------------------------
    # Trade management
    # ------------------------------------------------------------------

    def _manage_open_trade(self, trade: dict, row: pd.Series) -> dict:
        """Checks if stop or target was hit on this bar. Returns result dict."""
        hi, lo = row["high"], row["low"]
        direction = trade["direction"]
        stop = trade["stop_price"]
        target = trade["target_price"]
        entry = trade["entry_price"]
        pv = self.instrument["point_value"]
        contracts = trade["contracts"]

        if direction == "BUY":
            if lo <= stop:
                pnl = (stop - entry) * pv * contracts
                r = (stop - entry) / (target - entry) if target != entry else -1
                return {"closed": True, "pnl": pnl, "r_multiple": r}
            if hi >= target:
                pnl = (target - entry) * pv * contracts
                r = 1.0
                return {"closed": True, "pnl": pnl, "r_multiple": r}
        else:  # SELL
            if hi >= stop:
                pnl = (entry - stop) * pv * contracts
                r = (entry - stop) / (entry - target) if entry != target else -1
                return {"closed": True, "pnl": pnl, "r_multiple": r}
            if lo <= target:
                pnl = (entry - target) * pv * contracts
                r = 1.0
                return {"closed": True, "pnl": pnl, "r_multiple": r}

        # Still open — update unrealised PnL
        mid = (hi + lo) / 2
        unrealised = (mid - entry) * pv * contracts if direction == "BUY" \
                     else (entry - mid) * pv * contracts
        return {"closed": False, "unrealised_pnl": unrealised}

    def _close_trade(self, trade: dict, price: float, reason: str) -> float:
        pv = self.instrument["point_value"]
        if trade["direction"] == "BUY":
            return (price - trade["entry_price"]) * pv * trade["contracts"]
        return (trade["entry_price"] - price) * pv * trade["contracts"]

    def _commission(self, contracts: int) -> float:
        return self.instrument["commission_per_side"] * 2 * contracts

    def _calc_open_pnl(self, trade: dict, row: pd.Series) -> float:
        if trade is None:
            return 0.0
        mid = (row["high"] + row["low"]) / 2
        pv = self.instrument["point_value"]
        if trade["direction"] == "BUY":
            return (mid - trade["entry_price"]) * pv * trade["contracts"]
        return (trade["entry_price"] - mid) * pv * trade["contracts"]

    def _trade_record(self, trade: dict, row: pd.Series, net_pnl: float,
                      r: float = None) -> dict:
        return {
            "entry_time":  trade["entry_time"],
            "exit_time":   str(row["timestamp"]),
            "direction":   trade["direction"],
            "net_pnl":     net_pnl,
            "r_multiple":  r,
            "setup_type":  trade["setup_type"],
        }

    # ------------------------------------------------------------------
    # Indicator helpers (no stubs — all implemented from OHLCV)
    # ------------------------------------------------------------------

    def _add_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["timestamp"] = pd.to_datetime(df["timestamp"])

        # ATR (14-period)
        hl = df["high"] - df["low"]
        hc = (df["high"] - df["close"].shift(1)).abs()
        lc = (df["low"] - df["close"].shift(1)).abs()
        df["tr"] = pd.concat([hl, hc, lc], axis=1).max(axis=1)
        df["atr"] = df["tr"].rolling(14).mean()
        df["atr_avg"] = df["atr"].rolling(20).mean()
        df["atr_ratio"] = (df["atr"] / df["atr_avg"]).fillna(1.0)

        # Volume ratio
        df["vol_avg"] = df["volume"].rolling(20).mean()
        df["volume_ratio"] = (df["volume"] / df["vol_avg"]).fillna(1.0)
        df["volume_spike"] = df["volume_ratio"] > 1.8

        # Delta proxy: close vs open (bullish = POSITIVE, bearish = NEGATIVE)
        # NOTE: This is an OHLCV approximation — not real order-flow delta.
        # Replace with Rithmic tick data in Sierra Chart phase.
        df["delta_proxy"] = df["close"] - df["open"]
        df["delta_direction"] = df["delta_proxy"].apply(
            lambda x: "POSITIVE" if x > 0 else ("NEGATIVE" if x < 0 else "NEUTRAL")
        )
        df["prev_delta_direction"] = df["delta_direction"].shift(1)
        df["delta_flip"] = df["delta_direction"] != df["prev_delta_direction"]

        # VWAP crossings in last 10 bars
        df["above_vwap"] = (df["close"] > df["vwap"]).astype(int)
        df["vwap_crosses_10"] = (
            df["above_vwap"].rolling(10).apply(
                lambda x: (x.diff().abs().sum()), raw=True
            ).fillna(0)
        )

        # Session phase (ET hours — UTC-5 or UTC-4)
        df["et_hour"] = (df["timestamp"].dt.hour - 5) % 24  # rough ET
        df["session_phase"] = df["et_hour"].apply(
            lambda h: "OPEN" if h < 10 else ("MID" if h < 13 else "CLOSE")
        )

        # Minutes since RTH open (9:30 ET)
        df["date"] = df["timestamp"].dt.date
        rth_open = df.groupby("date")["timestamp"].transform("min")
        df["minutes_since_open"] = (
            (df["timestamp"] - rth_open).dt.total_seconds() / 60
        ).clip(lower=0)

        return df

    # ------------------------------------------------------------------
    # Report
    # ------------------------------------------------------------------

    def _generate_report(self, prop_sim: PropFirmSimulator) -> dict:
        if not self.trades:
            return {**prop_sim.get_report(), "error": "No trades generated",
                    "total_trades": 0}

        td = pd.DataFrame(self.trades)
        wins = td[td["net_pnl"] > 0]
        losses = td[td["net_pnl"] <= 0]
        gross_profit = wins["net_pnl"].sum() if not wins.empty else 0
        gross_loss = abs(losses["net_pnl"].sum()) if not losses.empty else 0

        return {
            **prop_sim.get_report(),
            "total_trades":    len(td),
            "winning_trades":  len(wins),
            "losing_trades":   len(losses),
            "win_rate":        len(wins) / len(td),
            "profit_factor":   gross_profit / gross_loss if gross_loss > 0 else float("inf"),
            "avg_r_multiple":  td["r_multiple"].dropna().mean(),
            "sharpe_ratio":    self._calc_sharpe(td),
            "max_drawdown":    self._calc_max_drawdown(),
        }

    def _calc_sharpe(self, td: pd.DataFrame) -> float:
        if len(td) < 2:
            return 0.0
        daily = td.groupby(td["exit_time"].str[:10])["net_pnl"].sum()
        std = daily.std()
        return float((daily.mean() / std) * np.sqrt(252)) if std > 0 else 0.0

    def _calc_max_drawdown(self) -> float:
        if not self.equity_curve:
            return 0.0
        equity = pd.Series([e["equity"] for e in self.equity_curve])
        rolling_max = equity.cummax()
        drawdowns = (equity - rolling_max) / rolling_max.replace(0, np.nan)
        return float(drawdowns.min())
