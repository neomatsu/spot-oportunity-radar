from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from backtesting.models import RSICycleRules, default_rsi_cycle_rules
from core.config import load_yaml_config
from services.technical_service import TechnicalService


@dataclass(slots=True)
class RSICycleAlertSignal:
    event_type: str
    signal_date: object
    rsi14: float
    price: float
    severity: str
    title: str
    message: str
    alert_group: str = "rsi_cycle"


class RSICycleAlertsService:
    def __init__(self) -> None:
        config = load_yaml_config("backtesting.yaml").get("rsi_cycle_strategy", {})
        defaults = default_rsi_cycle_rules()
        self.rules = RSICycleRules(
            oversold_threshold=float(config.get("oversold_threshold", defaults.oversold_threshold)),
            deep_oversold_threshold_1=float(
                config.get("deep_oversold_threshold_1", defaults.deep_oversold_threshold_1)
            ),
            deep_oversold_threshold_2=float(
                config.get("deep_oversold_threshold_2", defaults.deep_oversold_threshold_2)
            ),
            overbought_threshold=float(
                config.get("overbought_threshold", defaults.overbought_threshold)
            ),
            overbought_threshold_1=float(
                config.get("overbought_threshold_1", defaults.overbought_threshold_1)
            ),
            overbought_threshold_2=float(
                config.get("overbought_threshold_2", defaults.overbought_threshold_2)
            ),
            buy_pct_bullish_divergence=float(
                config.get(
                    "buy_pct_bullish_divergence",
                    defaults.buy_pct_bullish_divergence,
                )
            ),
            buy_pct_rsi_25=float(config.get("buy_pct_rsi_25", defaults.buy_pct_rsi_25)),
            buy_pct_rsi_20=float(config.get("buy_pct_rsi_20", defaults.buy_pct_rsi_20)),
            sell_pct_bearish_divergence=float(
                config.get(
                    "sell_pct_bearish_divergence",
                    defaults.sell_pct_bearish_divergence,
                )
            ),
            sell_pct_rsi_75=float(config.get("sell_pct_rsi_75", defaults.sell_pct_rsi_75)),
            sell_pct_rsi_80=float(config.get("sell_pct_rsi_80", defaults.sell_pct_rsi_80)),
            min_bars_between_pivots=int(
                config.get("min_bars_between_pivots", defaults.min_bars_between_pivots)
            ),
            max_bars_between_pivots=int(
                config.get("max_bars_between_pivots", defaults.max_bars_between_pivots)
            ),
            pivot_price_source=str(
                config.get("pivot_price_source", defaults.pivot_price_source)
            ),
            require_confirmation_cross=bool(
                config.get("require_confirmation_cross", defaults.require_confirmation_cross)
            ),
            max_one_divergence_per_cycle=bool(
                config.get("max_one_divergence_per_cycle", defaults.max_one_divergence_per_cycle)
            ),
        )
        self.technical_service = TechnicalService()

    def detect_latest_signals(self, frame: pd.DataFrame) -> list[RSICycleAlertSignal]:
        if frame.empty or len(frame) < 20:
            return []
        enriched = self.technical_service.compute_indicators(frame).reset_index(drop=True)
        latest_index = len(enriched) - 1
        state = self._init_state()
        latest_events: list[str] = []
        for index in range(len(enriched)):
            events = self._events_for_bar(enriched, index, state)
            if index == latest_index:
                latest_events = events
        if not latest_events:
            return []
        chosen = []
        buy_event = self._select_event(latest_events, side="buy")
        sell_event = self._select_event(latest_events, side="sell")
        latest_row = enriched.iloc[latest_index]
        signal_date = pd.Timestamp(latest_row["date"]).date()
        rsi14 = float(latest_row["rsi14"])
        price = float(latest_row["close"])
        if buy_event is not None:
            chosen.append(self._build_signal(buy_event, signal_date, rsi14, price))
        if sell_event is not None:
            chosen.append(self._build_signal(sell_event, signal_date, rsi14, price))
        return chosen

    def _build_signal(
        self,
        event_type: str,
        signal_date: object,
        rsi14: float,
        price: float,
    ) -> RSICycleAlertSignal:
        metadata = {
            "BUY_RSI_25": ("high", "Compra RSI<=25", "RSI en sobreventa profunda de primer nivel."),
            "BUY_RSI_20": (
                "critical",
                "Compra RSI<=20",
                "RSI en sobreventa extrema de segundo nivel.",
            ),
            "BUY_BULLISH_DIVERGENCE": (
                "high",
                "Divergencia alcista confirmada",
                "Divergencia RSI alcista confirmada por reentrada del ciclo.",
            ),
            "SELL_RSI_75": (
                "warning",
                "Venta RSI>=75",
                "RSI en sobrecompra avanzada de primer nivel.",
            ),
            "SELL_RSI_80": (
                "high",
                "Venta RSI>=80",
                "RSI en sobrecompra extrema de segundo nivel.",
            ),
            "SELL_BEARISH_DIVERGENCE": (
                "high",
                "Divergencia bajista confirmada",
                "Divergencia RSI bajista confirmada por reentrada del ciclo.",
            ),
        }
        severity, title, message = metadata[event_type]
        return RSICycleAlertSignal(
            event_type=event_type.lower(),
            signal_date=signal_date,
            rsi14=rsi14,
            price=price,
            severity=severity,
            title=title,
            message=message,
        )

    @staticmethod
    def _init_state() -> dict[str, Any]:
        return {
            "oversold_active": False,
            "overbought_active": False,
            "buy_rsi_25_used": False,
            "buy_rsi_20_used": False,
            "buy_div_used": False,
            "sell_rsi_75_used": False,
            "sell_rsi_80_used": False,
            "sell_div_used": False,
            "oversold_pivots": [],
            "overbought_pivots": [],
            "bullish_divergence_pending": False,
            "bearish_divergence_pending": False,
        }

    def _events_for_bar(
        self,
        frame: pd.DataFrame,
        index: int,
        state: dict[str, Any],
    ) -> list[str]:
        rules = self.rules
        row = frame.iloc[index]
        rsi = self._optional_float(row.get("rsi14"))
        if rsi is None:
            return []

        prev_rsi = None
        if index > 0:
            prev_rsi = self._optional_float(frame.iloc[index - 1].get("rsi14"))

        events: list[str] = []
        if rsi < rules.oversold_threshold and not state["oversold_active"]:
            state["oversold_active"] = True
            state["buy_rsi_25_used"] = False
            state["buy_rsi_20_used"] = False
            state["buy_div_used"] = False
            state["oversold_pivots"] = []
            state["bullish_divergence_pending"] = False

        if rsi > rules.overbought_threshold and not state["overbought_active"]:
            state["overbought_active"] = True
            state["sell_rsi_75_used"] = False
            state["sell_rsi_80_used"] = False
            state["sell_div_used"] = False
            state["overbought_pivots"] = []
            state["bearish_divergence_pending"] = False

        if state["oversold_active"]:
            if not state["buy_rsi_25_used"] and rsi <= rules.deep_oversold_threshold_1:
                events.append("BUY_RSI_25")
                state["buy_rsi_25_used"] = True
            if not state["buy_rsi_20_used"] and rsi <= rules.deep_oversold_threshold_2:
                events.append("BUY_RSI_20")
                state["buy_rsi_20_used"] = True
            pivot = self._confirm_pivot_low(frame, index)
            if pivot is not None:
                state["oversold_pivots"].append(pivot)
                state["oversold_pivots"] = state["oversold_pivots"][-5:]
                if self._has_bullish_divergence(state["oversold_pivots"]):
                    state["bullish_divergence_pending"] = True
            bull_confirmed = (
                state["bullish_divergence_pending"]
                and not state["buy_div_used"]
                and (
                    (not rules.require_confirmation_cross and rsi > rules.oversold_threshold)
                    or (
                        rules.require_confirmation_cross
                        and prev_rsi is not None
                        and prev_rsi <= rules.oversold_threshold
                        and rsi > rules.oversold_threshold
                    )
                )
            )
            if bull_confirmed:
                events.append("BUY_BULLISH_DIVERGENCE")
                state["buy_div_used"] = True
                if rules.max_one_divergence_per_cycle:
                    state["bullish_divergence_pending"] = False
            if rsi > rules.oversold_threshold:
                state["oversold_active"] = False
                state["oversold_pivots"] = []
                state["bullish_divergence_pending"] = False

        if state["overbought_active"]:
            if not state["sell_rsi_75_used"] and rsi >= rules.overbought_threshold_1:
                events.append("SELL_RSI_75")
                state["sell_rsi_75_used"] = True
            if not state["sell_rsi_80_used"] and rsi >= rules.overbought_threshold_2:
                events.append("SELL_RSI_80")
                state["sell_rsi_80_used"] = True
            pivot = self._confirm_pivot_high(frame, index)
            if pivot is not None:
                state["overbought_pivots"].append(pivot)
                state["overbought_pivots"] = state["overbought_pivots"][-5:]
                if self._has_bearish_divergence(state["overbought_pivots"]):
                    state["bearish_divergence_pending"] = True
            bear_confirmed = (
                state["bearish_divergence_pending"]
                and not state["sell_div_used"]
                and (
                    (not rules.require_confirmation_cross and rsi < rules.overbought_threshold)
                    or (
                        rules.require_confirmation_cross
                        and prev_rsi is not None
                        and prev_rsi >= rules.overbought_threshold
                        and rsi < rules.overbought_threshold
                    )
                )
            )
            if bear_confirmed:
                events.append("SELL_BEARISH_DIVERGENCE")
                state["sell_div_used"] = True
                if rules.max_one_divergence_per_cycle:
                    state["bearish_divergence_pending"] = False
            if rsi < rules.overbought_threshold:
                state["overbought_active"] = False
                state["overbought_pivots"] = []
                state["bearish_divergence_pending"] = False
        return events

    def _confirm_pivot_low(self, frame: pd.DataFrame, index: int) -> dict[str, Any] | None:
        if index < 2:
            return None
        rsi_prev2 = self._optional_float(frame.iloc[index - 2].get("rsi14"))
        rsi_prev1 = self._optional_float(frame.iloc[index - 1].get("rsi14"))
        rsi_now = self._optional_float(frame.iloc[index].get("rsi14"))
        if rsi_prev2 is None or rsi_prev1 is None or rsi_now is None:
            return None
        if not (
            rsi_prev2 > rsi_prev1 <= rsi_now
            and rsi_prev1 <= self.rules.oversold_threshold
        ):
            return None
        pivot_index = index - 1
        pivot_row = frame.iloc[pivot_index]
        price_field = "low" if self.rules.pivot_price_source == "extremes" else "close"
        return {
            "index": pivot_index,
            "rsi": float(rsi_prev1),
            "price": float(pivot_row[price_field]),
        }

    def _confirm_pivot_high(self, frame: pd.DataFrame, index: int) -> dict[str, Any] | None:
        if index < 2:
            return None
        rsi_prev2 = self._optional_float(frame.iloc[index - 2].get("rsi14"))
        rsi_prev1 = self._optional_float(frame.iloc[index - 1].get("rsi14"))
        rsi_now = self._optional_float(frame.iloc[index].get("rsi14"))
        if rsi_prev2 is None or rsi_prev1 is None or rsi_now is None:
            return None
        if not (
            rsi_prev2 < rsi_prev1 >= rsi_now
            and rsi_prev1 >= self.rules.overbought_threshold
        ):
            return None
        pivot_index = index - 1
        pivot_row = frame.iloc[pivot_index]
        price_field = "high" if self.rules.pivot_price_source == "extremes" else "close"
        return {
            "index": pivot_index,
            "rsi": float(rsi_prev1),
            "price": float(pivot_row[price_field]),
        }

    def _has_bullish_divergence(self, pivots: list[dict[str, Any]]) -> bool:
        if len(pivots) < 2:
            return False
        first, second = pivots[-2], pivots[-1]
        gap = second["index"] - first["index"]
        return (
            self.rules.min_bars_between_pivots <= gap <= self.rules.max_bars_between_pivots
            and second["price"] < first["price"]
            and second["rsi"] > first["rsi"]
        )

    def _has_bearish_divergence(self, pivots: list[dict[str, Any]]) -> bool:
        if len(pivots) < 2:
            return False
        first, second = pivots[-2], pivots[-1]
        gap = second["index"] - first["index"]
        return (
            self.rules.min_bars_between_pivots <= gap <= self.rules.max_bars_between_pivots
            and second["price"] > first["price"]
            and second["rsi"] < first["rsi"]
        )

    @staticmethod
    def _select_event(events: list[str], *, side: str) -> str | None:
        filtered = [
            event
            for event in events
            if event.startswith("BUY_" if side == "buy" else "SELL_")
        ]
        if not filtered:
            return None
        if side == "buy":
            priority = {
                "BUY_RSI_20": 0,
                "BUY_BULLISH_DIVERGENCE": 1,
                "BUY_RSI_25": 2,
            }
        else:
            priority = {
                "SELL_RSI_80": 0,
                "SELL_BEARISH_DIVERGENCE": 1,
                "SELL_RSI_75": 2,
            }
        return min(filtered, key=lambda event: priority.get(event, 99))

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        if value is None or pd.isna(value):
            return None
        return float(value)
