from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np
import pandas as pd

from core.config import load_yaml_config
from market_regime.regime_models import MarketRegime
from services.technical_service import TechnicalService


class MarketRegimeService:
    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or load_yaml_config("regime_config.yaml")

    def compute_regime(
        self,
        price_frame: pd.DataFrame,
        *,
        benchmark_frame: pd.DataFrame | None = None,
        as_of_date: date | None = None,
    ) -> MarketRegime:
        if price_frame.empty:
            return self._empty_regime(as_of_date)

        frame = self._prepare_frame(price_frame)
        if as_of_date is not None:
            frame = frame[frame["date"].dt.date <= as_of_date]
        if frame.empty:
            return self._empty_regime(as_of_date)

        benchmark = self._prepare_benchmark(benchmark_frame, as_of_date=as_of_date)
        return self.compute_regime_from_prepared_frame(
            frame,
            benchmark_frame=benchmark,
            as_of_date=as_of_date,
        )

    def compute_regime_from_prepared_frame(
        self,
        prepared_frame: pd.DataFrame,
        *,
        benchmark_frame: pd.DataFrame | None = None,
        as_of_date: date | None = None,
    ) -> MarketRegime:
        frame = prepared_frame
        if as_of_date is not None:
            frame = frame[frame["date"].dt.date <= as_of_date]
        if frame.empty:
            return self._empty_regime(as_of_date)

        benchmark = benchmark_frame
        if benchmark is not None and as_of_date is not None:
            benchmark = benchmark[benchmark["date"].dt.date <= as_of_date]
        latest = frame.iloc[-1]
        history = frame.iloc[:-1]

        bull_breakdown = {
            "trend_score": self._bull_trend_score(latest),
            "structure_score": self._bull_structure_score(frame),
            "momentum_score": self._bull_momentum_score(latest),
            "relative_strength_score": self._relative_strength_score(frame, benchmark),
        }
        bear_breakdown = {
            "trend_break_score": self._bear_trend_score(latest),
            "drawdown_score": self._drawdown_score(latest),
            "structure_break_score": self._bear_structure_score(frame),
            "relative_weakness_score": 100.0 - bull_breakdown["relative_strength_score"],
        }
        bubble_breakdown = {
            "extension_score": self._extension_score(latest),
            "acceleration_score": self._acceleration_score(frame),
            "volatility_score": self._volatility_score(frame),
            "ath_proximity_score": self._ath_proximity_score(latest, history),
            "optional_valuation_score": 0.0,
        }

        bull_score = self._weighted_score(
            bull_breakdown,
            {
                "trend_score": 0.35,
                "structure_score": 0.25,
                "momentum_score": 0.20,
                "relative_strength_score": 0.20,
            },
        )
        bear_score = self._weighted_score(
            bear_breakdown,
            {
                "trend_break_score": 0.35,
                "drawdown_score": 0.25,
                "structure_break_score": 0.20,
                "relative_weakness_score": 0.20,
            },
        )
        bubble_score = self._weighted_score(
            bubble_breakdown,
            {
                "extension_score": 0.30,
                "acceleration_score": 0.25,
                "volatility_score": 0.20,
                "ath_proximity_score": 0.15,
                "optional_valuation_score": 0.10,
            },
        )

        bull_probability, bear_probability, bubble_probability = self._normalize_scores(
            bull_score,
            bear_score,
            bubble_score,
        )
        dominant = self._dominant_regime(bull_probability, bear_probability)

        return MarketRegime(
            bull_probability=round(bull_probability, 2),
            bear_probability=round(bear_probability, 2),
            bubble_probability=round(bubble_probability, 2),
            dominant_regime=dominant,
            as_of_date=latest["date"].date(),
            breakdown={
                "bull_score": round(bull_score, 2),
                "bear_score": round(bear_score, 2),
                "bubble_score": round(bubble_score, 2),
                "bull": {k: round(v, 2) for k, v in bull_breakdown.items()},
                "bear": {k: round(v, 2) for k, v in bear_breakdown.items()},
                "bubble": {k: round(v, 2) for k, v in bubble_breakdown.items()},
            },
        )

    def compute_regime_history(
        self,
        price_frame: pd.DataFrame,
        *,
        benchmark_frame: pd.DataFrame | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> pd.DataFrame:
        if price_frame.empty:
            return pd.DataFrame()

        frame = self._prepare_frame(price_frame)
        if end_date is not None:
            frame = frame[frame["date"].dt.date <= end_date]
        if start_date is not None:
            calculation_dates = frame[frame["date"].dt.date >= start_date]["date"].dt.date
        else:
            calculation_dates = frame["date"].dt.date

        min_rows = int(self.config.get("history", {}).get("min_rows", 220))
        rows: list[dict[str, Any]] = []
        for current_date in calculation_dates:
            subset = frame[frame["date"].dt.date <= current_date]
            if len(subset) < min_rows:
                continue
            regime = self.compute_regime(
                subset,
                benchmark_frame=benchmark_frame,
                as_of_date=current_date,
            )
            rows.append(self.to_record(regime))
        return pd.DataFrame(rows)

    @staticmethod
    def to_record(regime: MarketRegime) -> dict[str, Any]:
        return {
            "date": regime.as_of_date,
            "bull_probability": regime.bull_probability,
            "bear_probability": regime.bear_probability,
            "bubble_probability": regime.bubble_probability,
            "dominant_regime": regime.dominant_regime,
            "breakdown_json": regime.breakdown,
        }

    def _prepare_frame(self, price_frame: pd.DataFrame) -> pd.DataFrame:
        frame = price_frame.copy()
        frame["date"] = pd.to_datetime(frame["date"])
        frame = frame.sort_values("date").drop_duplicates(subset=["date"], keep="last")
        required_indicators = {"rsi14", "sma50", "sma200", "ema20", "atr14", "week_52_high"}
        if not required_indicators.issubset(frame.columns):
            frame = TechnicalService.compute_indicators(frame)
        frame["drawdown_from_52w_high"] = (
            (frame["close"] / frame["week_52_high"].replace(0, np.nan)) - 1
        ).fillna(0.0)
        return frame

    def _prepare_benchmark(
        self,
        benchmark_frame: pd.DataFrame | None,
        *,
        as_of_date: date | None,
    ) -> pd.DataFrame | None:
        if benchmark_frame is None or benchmark_frame.empty:
            return None
        benchmark = self._prepare_frame(benchmark_frame)
        if as_of_date is not None:
            benchmark = benchmark[benchmark["date"].dt.date <= as_of_date]
        return benchmark

    def _bull_trend_score(self, latest: pd.Series) -> float:
        score = 0.0
        close = self._value(latest, "close")
        sma50 = self._value(latest, "sma50")
        sma200 = self._value(latest, "sma200")
        if close is not None and sma200 is not None and close > sma200:
            score += 45.0
        if sma50 is not None and sma200 is not None and sma50 > sma200:
            score += 45.0
        if close is not None and sma50 is not None and close > sma50:
            score += 10.0
        return min(score, 100.0)

    def _bear_trend_score(self, latest: pd.Series) -> float:
        score = 0.0
        close = self._value(latest, "close")
        sma50 = self._value(latest, "sma50")
        sma200 = self._value(latest, "sma200")
        if close is not None and sma200 is not None and close < sma200:
            score += 45.0
        if sma50 is not None and sma200 is not None and sma50 < sma200:
            score += 45.0
        if close is not None and sma50 is not None and close < sma50:
            score += 10.0
        return min(score, 100.0)

    def _bull_structure_score(self, frame: pd.DataFrame) -> float:
        short, long = self._structure_windows()
        if len(frame) < long + short:
            return 50.0
        recent = frame.tail(short)
        prior = frame.iloc[-(long + short):-short]
        higher_high = float(recent["high"].max()) > float(prior["high"].max())
        higher_low = float(recent["low"].min()) > float(prior["low"].min())
        if higher_high and higher_low:
            return 100.0
        if higher_high or higher_low:
            return 60.0
        return 20.0

    def _bear_structure_score(self, frame: pd.DataFrame) -> float:
        short, long = self._structure_windows()
        if len(frame) < long + short:
            return 50.0
        recent = frame.tail(short)
        prior = frame.iloc[-(long + short):-short]
        lower_high = float(recent["high"].max()) < float(prior["high"].max())
        lower_low = float(recent["low"].min()) < float(prior["low"].min())
        if lower_high and lower_low:
            return 100.0
        if lower_high or lower_low:
            return 60.0
        return 20.0

    def _bull_momentum_score(self, latest: pd.Series) -> float:
        rsi = self._value(latest, "rsi14")
        if rsi is None:
            return 50.0
        thresholds = self.config.get("rsi_thresholds", {})
        bull_min = float(thresholds.get("bull_min", 45))
        bull_max = float(thresholds.get("bull_max", 70))
        if bull_min <= rsi <= bull_max:
            return 100.0
        if 40 <= rsi < bull_min:
            return 65.0
        if bull_max < rsi < float(thresholds.get("bubble", 70)) + 10:
            return 45.0
        if rsi < float(thresholds.get("bear_max", 40)):
            return 20.0
        return 30.0

    def _drawdown_score(self, latest: pd.Series) -> float:
        drawdown = abs(float(latest.get("drawdown_from_52w_high", 0.0) or 0.0))
        thresholds = self.config.get("drawdown_thresholds", {})
        mild = float(thresholds.get("mild", 0.15))
        medium = float(thresholds.get("medium", 0.30))
        severe = float(thresholds.get("severe", 0.50))
        if drawdown >= severe:
            return 100.0
        if drawdown >= medium:
            return 75.0
        if drawdown >= mild:
            return 45.0
        return 10.0

    def _relative_strength_score(
        self,
        frame: pd.DataFrame,
        benchmark: pd.DataFrame | None,
    ) -> float:
        cfg = self.config.get("relative_strength", {})
        neutral = float(cfg.get("neutral_score", 50))
        lookback = int(cfg.get("lookback_days", 63))
        if benchmark is None or len(frame) <= lookback or len(benchmark) <= lookback:
            return neutral

        asset_return = float(frame["close"].iloc[-1] / frame["close"].iloc[-lookback] - 1)
        bench_return = float(benchmark["close"].iloc[-1] / benchmark["close"].iloc[-lookback] - 1)
        spread = asset_return - bench_return
        return float(np.clip(50 + (spread * 250), 0, 100))

    def _extension_score(self, latest: pd.Series) -> float:
        close = self._value(latest, "close")
        sma200 = self._value(latest, "sma200")
        if close is None or sma200 is None or sma200 <= 0:
            return 20.0
        extension = (close / sma200) - 1
        thresholds = self.config.get("sma_distance_thresholds", {})
        moderate = float(thresholds.get("moderate", 0.20))
        high = float(thresholds.get("high", 0.40))
        extreme = float(thresholds.get("extreme", 0.60))
        if extension >= extreme:
            return 100.0
        if extension >= high:
            return 80.0
        if extension >= moderate:
            return 50.0
        return max(0.0, extension / moderate * 35.0) if extension > 0 else 0.0

    def _acceleration_score(self, frame: pd.DataFrame) -> float:
        cfg = self.config.get("acceleration", {})
        short_days = int(cfg.get("short_roc_days", 20))
        long_days = int(cfg.get("long_roc_days", 60))
        if len(frame) <= long_days:
            return 20.0
        close = frame["close"]
        short_roc = float(close.iloc[-1] / close.iloc[-short_days] - 1)
        long_roc = float(close.iloc[-1] / close.iloc[-long_days] - 1)
        acceleration = short_roc - (long_roc / max(long_days / short_days, 1))
        return float(np.clip(30 + acceleration * 250, 0, 100))

    def _volatility_score(self, frame: pd.DataFrame) -> float:
        cfg = self.config.get("volatility", {})
        short = int(cfg.get("lookback_short", 20))
        long = int(cfg.get("lookback_long", 120))
        if len(frame) <= long:
            return 20.0
        returns = frame["close"].pct_change()
        short_vol = float(returns.tail(short).std() or 0.0)
        long_vol = float(returns.tail(long).std() or 0.0)
        if long_vol <= 0:
            return 20.0
        ratio = short_vol / long_vol
        high = float(cfg.get("expansion_ratio_high", 1.5))
        return float(np.clip((ratio - 1.0) / max(high - 1.0, 0.1) * 100, 0, 100))

    def _ath_proximity_score(self, latest: pd.Series, history: pd.DataFrame) -> float:
        close = self._value(latest, "close")
        high_52 = self._value(latest, "week_52_high")
        if close is None or high_52 is None or high_52 <= 0:
            return 20.0
        proximity = close / high_52
        base = float(np.clip((proximity - 0.90) / 0.10 * 80, 0, 80))
        if history.empty:
            return base
        near_high_count = int((history.tail(63)["close"] >= high_52 * 0.97).sum())
        return min(100.0, base + min(near_high_count * 2, 20))

    @staticmethod
    def _weighted_score(values: dict[str, float], weights: dict[str, float]) -> float:
        return sum(values[key] * weight for key, weight in weights.items())

    @staticmethod
    def _normalize_scores(
        bull_score: float,
        bear_score: float,
        bubble_score: float,
    ) -> tuple[float, float, float]:
        total = bull_score + bear_score + bubble_score
        if total <= 0:
            return 33.33, 33.33, 33.34
        return (
            bull_score / total * 100,
            bear_score / total * 100,
            bubble_score / total * 100,
        )

    @staticmethod
    def _dominant_regime(bull_probability: float, bear_probability: float) -> str:
        if bull_probability > 60 and bear_probability < 30:
            return "BULL"
        if bear_probability > 60:
            return "BEAR"
        return "TRANSITION"

    def _structure_windows(self) -> tuple[int, int]:
        cfg = self.config.get("structure", {})
        return int(cfg.get("lookback_short", 20)), int(cfg.get("lookback_long", 60))

    @staticmethod
    def _value(row: pd.Series, key: str) -> float | None:
        value = row.get(key)
        if value is None or pd.isna(value):
            return None
        return float(value)

    @staticmethod
    def _empty_regime(as_of_date: date | None) -> MarketRegime:
        return MarketRegime(
            bull_probability=33.33,
            bear_probability=33.33,
            bubble_probability=33.34,
            dominant_regime="TRANSITION",
            as_of_date=as_of_date,
            breakdown={"reason": "insufficient_price_history"},
        )
