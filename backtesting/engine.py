from __future__ import annotations

from dataclasses import dataclass
from statistics import mean

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from data.database import AssetORM, PriceBarDailyORM, SignalORM


@dataclass
class BacktestResult:
    trades: int
    hit_rate: float
    avg_return_5d: float
    avg_return_10d: float
    avg_return_20d: float
    worst_return_20d: float
    rows: list[dict]


class BacktestEngine:
    """Versión ligera del backtesting basada en señales persistidas.

    Evalúa el precio en la fecha de señal y el retorno 5, 10 y 20 sesiones
    después. Es una aproximación simple, útil para validar la dirección media
    de las señales antes de evolucionar a un motor histórico más completo.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def run(self, recommendation_filter: str | None = None) -> BacktestResult:
        statement = (
            select(SignalORM, AssetORM)
            .join(AssetORM, AssetORM.id == SignalORM.asset_id)
            .order_by(SignalORM.date.desc(), SignalORM.final_score.desc())
        )
        if recommendation_filter:
            statement = statement.where(SignalORM.recommendation == recommendation_filter)

        rows = self.session.execute(statement).all()
        evaluation_rows: list[dict] = []

        for signal, asset in rows:
            price_series = self._load_price_series(asset.id)
            if price_series.empty:
                continue

            evaluation = self._evaluate_signal(asset.symbol, signal, price_series)
            if evaluation is not None:
                evaluation_rows.append(evaluation)

        if not evaluation_rows:
            return BacktestResult(0, 0.0, 0.0, 0.0, 0.0, 0.0, [])

        positive_20d = [
            row["return_20d"] for row in evaluation_rows if row["return_20d"] is not None
        ]
        hit_rate = (
            sum(1 for value in positive_20d if value > 0) / len(positive_20d)
            if positive_20d
            else 0.0
        )
        return BacktestResult(
            trades=len(evaluation_rows),
            hit_rate=round(hit_rate * 100, 2),
            avg_return_5d=round(self._average_metric(evaluation_rows, "return_5d"), 2),
            avg_return_10d=round(self._average_metric(evaluation_rows, "return_10d"), 2),
            avg_return_20d=round(self._average_metric(evaluation_rows, "return_20d"), 2),
            worst_return_20d=round(
                min(
                    (
                        row["return_20d"]
                        for row in evaluation_rows
                        if row["return_20d"] is not None
                    ),
                    default=0.0,
                ),
                2,
            ),
            rows=evaluation_rows,
        )

    def _load_price_series(self, asset_id: int) -> pd.DataFrame:
        statement = (
            select(PriceBarDailyORM)
            .where(PriceBarDailyORM.asset_id == asset_id)
            .order_by(PriceBarDailyORM.date)
        )
        bars = list(self.session.scalars(statement))
        return pd.DataFrame(
            [{"date": bar.date, "close": bar.close} for bar in bars]
        )

    @staticmethod
    def _evaluate_signal(symbol: str, signal: SignalORM, price_series: pd.DataFrame) -> dict | None:
        series = price_series.copy()
        series["date"] = pd.to_datetime(series["date"])
        signal_date = pd.Timestamp(signal.date)
        signal_rows = series.index[series["date"] == signal_date]
        if len(signal_rows) == 0:
            return None

        idx = int(signal_rows[0])
        signal_price = float(series.iloc[idx]["close"])
        return {
            "symbol": symbol,
            "date": signal.date,
            "recommendation": signal.recommendation,
            "final_score": signal.final_score,
            "signal_price": signal_price,
            "return_5d": BacktestEngine._forward_return(series, idx, 5),
            "return_10d": BacktestEngine._forward_return(series, idx, 10),
            "return_20d": BacktestEngine._forward_return(series, idx, 20),
        }

    @staticmethod
    def _forward_return(series: pd.DataFrame, idx: int, periods: int) -> float | None:
        future_idx = idx + periods
        if future_idx >= len(series):
            return None
        start_price = float(series.iloc[idx]["close"])
        end_price = float(series.iloc[future_idx]["close"])
        return round(((end_price / start_price) - 1) * 100, 2)

    @staticmethod
    def _average_metric(rows: list[dict], key: str) -> float:
        values = [row[key] for row in rows if row[key] is not None]
        return mean(values) if values else 0.0
