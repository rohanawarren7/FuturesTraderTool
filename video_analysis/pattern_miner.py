"""
Pattern Miner
Mines raw_video_trades to identify high-probability entry conditions.
Output feeds directly into strategy_params and signal_generator.py.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
from database.db_manager import DBManager


class PatternMiner:
    """
    Analyses video-extracted trade records to rank entry condition combinations
    by edge score (win_rate × sample_size × avg_R).

    Requires minimum 30 labelled trades (outcome WIN/LOSS) above the confidence
    threshold before producing reliable output.
    """

    MIN_TRADES_TOTAL = 30
    MIN_TRADES_PER_CONDITION = 10

    CONDITION_COMBOS = [
        ["vwap_position", "market_state"],
        ["vwap_position", "market_state", "delta_direction"],
        ["vwap_position", "market_state", "delta_direction", "volume_spike"],
        ["entry_trigger", "market_state"],
        ["entry_trigger", "session_phase"],
        ["entry_trigger", "market_state", "delta_direction"],
    ]

    def __init__(self, db: DBManager):
        self.db = db

    # ------------------------------------------------------------------
    # Data loading
    # ------------------------------------------------------------------

    def get_labelled_trades(self, min_confidence: float = 0.65) -> pd.DataFrame:
        """
        Returns trades with known outcomes above the confidence threshold.
        Excludes UNKNOWN outcomes — only WIN and LOSS are useful for mining.
        """
        all_trades = self.db.get_all_video_trades(min_confidence=min_confidence)
        df = pd.DataFrame(all_trades)

        if df.empty:
            return df

        df = df[df["outcome"].isin(["WIN", "LOSS"])].copy()
        df["win"] = (df["outcome"] == "WIN").astype(int)
        df["volume_spike"] = df["volume_spike"].astype(bool)
        df["delta_flip"] = df["delta_flip"].astype(bool)
        return df

    # ------------------------------------------------------------------
    # Analysis
    # ------------------------------------------------------------------

    def analyse_conditions(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Groups by every defined condition combination and computes:
          - trades:    sample size
          - win_rate:  proportion of winning trades
          - avg_r:     average R-multiple (if available)
          - edge_score: win_rate × trades × max(avg_r, 1.0)

        Returns a DataFrame sorted by edge_score descending,
        filtered to combos with at least MIN_TRADES_PER_CONDITION samples.
        """
        results = []

        for combo in self.CONDITION_COMBOS:
            # Skip combos with columns missing from the data
            if not all(c in df.columns for c in combo):
                continue

            grouped = (
                df.groupby(combo)
                .agg(
                    trades=("win", "count"),
                    wins=("win", "sum"),
                    avg_r=("r_multiple", "mean"),
                )
                .reset_index()
            )

            grouped = grouped[grouped["trades"] >= self.MIN_TRADES_PER_CONDITION].copy()
            if grouped.empty:
                continue

            grouped["win_rate"] = grouped["wins"] / grouped["trades"]
            grouped["avg_r"] = grouped["avg_r"].fillna(1.0).clip(lower=0.1)
            grouped["edge_score"] = (
                grouped["win_rate"] * grouped["trades"] * grouped["avg_r"]
            )
            grouped["combo_columns"] = str(combo)
            grouped["conditions"] = grouped[combo].apply(
                lambda row: dict(zip(combo, row)), axis=1
            ).astype(str)

            results.append(grouped)

        if not results:
            return pd.DataFrame()

        return (
            pd.concat(results, ignore_index=True)
            .sort_values("edge_score", ascending=False)
            .reset_index(drop=True)
        )

    # ------------------------------------------------------------------
    # Top conditions export
    # ------------------------------------------------------------------

    def export_top_conditions(
        self, n: int = 10, min_confidence: float = 0.65
    ) -> list[dict]:
        """
        Returns the top N trading conditions by edge score.
        Raises ValueError if insufficient labelled data.
        """
        df = self.get_labelled_trades(min_confidence)

        if len(df) < self.MIN_TRADES_TOTAL:
            raise ValueError(
                f"Insufficient labelled data: {len(df)} trades "
                f"(need {self.MIN_TRADES_TOTAL}). "
                f"Run more videos through the pipeline or lower min_confidence."
            )

        analysis = self.analyse_conditions(df)
        if analysis.empty:
            raise ValueError(
                "No condition combinations met the minimum sample size. "
                "Need more diverse trade data."
            )

        return analysis.head(n).to_dict("records")

    def print_top_conditions(self, n: int = 10, min_confidence: float = 0.65):
        """Pretty-prints the top N conditions for inspection."""
        try:
            top = self.export_top_conditions(n, min_confidence)
        except ValueError as e:
            print(f"[PatternMiner] {e}")
            return

        print(f"\n{'='*80}")
        print(f"TOP {n} TRADING CONDITIONS (edge score = win_rate × trades × avg_R)")
        print(f"{'='*80}")
        print(f"{'Rank':<5} {'Conditions':<55} {'N':>5} {'Win%':>6} {'AvgR':>6} {'Edge':>7}")
        print(f"{'-'*80}")

        for i, row in enumerate(top, 1):
            cond = str(row.get("conditions", ""))[:54]
            print(
                f"{i:<5} {cond:<55} "
                f"{int(row['trades']):>5} "
                f"{row['win_rate']*100:>5.1f}% "
                f"{row['avg_r']:>6.2f} "
                f"{row['edge_score']:>7.1f}"
            )

        print(f"{'='*80}\n")

    # ------------------------------------------------------------------
    # Win rate by session phase
    # ------------------------------------------------------------------

    def win_rate_by_hour(self, min_confidence: float = 0.65) -> pd.DataFrame:
        """Returns win rate grouped by session_phase for time-of-day analysis."""
        df = self.get_labelled_trades(min_confidence)
        if df.empty or "session_phase" not in df.columns:
            return pd.DataFrame()

        grouped = (
            df.groupby("session_phase")
            .agg(trades=("win", "count"), wins=("win", "sum"))
            .reset_index()
        )
        grouped["win_rate"] = grouped["wins"] / grouped["trades"]
        return grouped.sort_values("win_rate", ascending=False)

    # ------------------------------------------------------------------
    # Recommended strategy summary
    # ------------------------------------------------------------------

    def recommend_strategy(self, min_confidence: float = 0.65) -> str:
        """
        Prints a human-readable recommended strategy spec based on the top conditions.
        """
        df = self.get_labelled_trades(min_confidence)
        if len(df) < self.MIN_TRADES_TOTAL:
            return f"Insufficient data: {len(df)} trades. Need {self.MIN_TRADES_TOTAL}."

        top = self.export_top_conditions(n=5, min_confidence=min_confidence)

        lines = [
            "\nRECOMMENDED STRATEGY SPEC",
            "=" * 60,
            f"Based on {len(df)} labelled trades "
            f"(visual_confidence >= {min_confidence})",
            "",
        ]

        for i, cond in enumerate(top[:2], 1):
            win_pct = cond["win_rate"] * 100
            lines.append(f"Setup {i}: {cond.get('conditions', '')}")
            lines.append(f"  Sample: {int(cond['trades'])} trades | "
                         f"Win rate: {win_pct:.1f}% | Avg R: {cond['avg_r']:.2f}")
            lines.append(f"  Edge score: {cond['edge_score']:.1f}")
            lines.append("")

        return "\n".join(lines)
