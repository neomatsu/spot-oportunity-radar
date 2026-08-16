from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from core.config import load_yaml_config


@dataclass(frozen=True, slots=True)
class BitcoinOpportunityThresholdSignal:
    event_type: str
    signal_date: date
    score: float
    thresholds: tuple[float, ...]
    recommended_pct: float
    action: str


class BitcoinOpportunityAlertsService:
    """Detect the latest configured threshold crossing, including cycle rearming."""

    def __init__(self, config: dict | None = None) -> None:
        self.config = config or load_yaml_config("bitcoin_opportunity.yaml").get(
            "backtesting", {}
        )

    def detect_latest_signal(
        self, history: pd.DataFrame
    ) -> BitcoinOpportunityThresholdSignal | None:
        if history.empty or not {"date", "overall_score"}.issubset(history.columns):
            return None
        frame = history[["date", "overall_score"]].copy()
        frame["date"] = pd.to_datetime(frame["date"])
        frame["overall_score"] = pd.to_numeric(
            frame["overall_score"], errors="coerce"
        )
        frame = (
            frame.dropna()
            .sort_values("date")
            .drop_duplicates("date", keep="last")
            .reset_index(drop=True)
        )
        if len(frame) < 2:
            return None

        buy_thresholds = tuple(float(value) for value in self.config["buy_thresholds"])
        buy_pcts = tuple(float(value) for value in self.config["buy_capital_pcts"])
        sell_thresholds = tuple(float(value) for value in self.config["sell_thresholds"])
        sell_pcts = tuple(float(value) for value in self.config["sell_position_pcts"])
        buy_reset = float(self.config.get("buy_reset_threshold", 60))
        sell_reset = float(self.config.get("sell_reset_threshold", 40))
        used_buys: set[float] = set()
        used_sells: set[float] = set()
        latest_signal: BitcoinOpportunityThresholdSignal | None = None

        for index in range(1, len(frame)):
            previous_score = float(frame.iloc[index - 1]["overall_score"])
            current_score = float(frame.iloc[index]["overall_score"])
            signal_date = pd.Timestamp(frame.iloc[index]["date"]).date()
            if current_score < buy_reset:
                used_buys.clear()
            if current_score > sell_reset:
                used_sells.clear()

            crossed_buys = [
                (threshold, pct)
                for threshold, pct in zip(buy_thresholds, buy_pcts, strict=True)
                if previous_score < threshold <= current_score
                and threshold not in used_buys
                and pct > 0
            ]
            crossed_sells = [
                (threshold, pct)
                for threshold, pct in zip(sell_thresholds, sell_pcts, strict=True)
                if previous_score > threshold >= current_score
                and threshold not in used_sells
                and pct > 0
            ]
            if crossed_buys:
                used_buys.update(threshold for threshold, _ in crossed_buys)
                latest_signal = BitcoinOpportunityThresholdSignal(
                    event_type="bitcoin_opportunity_buy",
                    signal_date=signal_date,
                    score=current_score,
                    thresholds=tuple(threshold for threshold, _ in crossed_buys),
                    recommended_pct=round(
                        min(sum(pct for _, pct in crossed_buys), 1.0) * 100, 6
                    ),
                    action="BUY",
                )
            elif crossed_sells:
                used_sells.update(threshold for threshold, _ in crossed_sells)
                latest_signal = BitcoinOpportunityThresholdSignal(
                    event_type="bitcoin_opportunity_sell",
                    signal_date=signal_date,
                    score=current_score,
                    thresholds=tuple(threshold for threshold, _ in crossed_sells),
                    recommended_pct=round(
                        min(sum(pct for _, pct in crossed_sells), 1.0) * 100, 6
                    ),
                    action="SELL",
                )

        latest_date = pd.Timestamp(frame.iloc[-1]["date"]).date()
        if latest_signal is None or latest_signal.signal_date != latest_date:
            return None
        return latest_signal
