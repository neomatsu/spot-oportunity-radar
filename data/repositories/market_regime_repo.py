from __future__ import annotations

from datetime import UTC, date, datetime

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from data.database import MarketRegimeHistoryORM
from market_regime.regime_models import MarketRegime


class MarketRegimeRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert_regime(
        self,
        *,
        asset_id: int,
        symbol: str,
        regime: MarketRegime,
    ) -> MarketRegimeHistoryORM:
        if regime.as_of_date is None:
            raise ValueError("Cannot persist a market regime without as_of_date.")

        statement = select(MarketRegimeHistoryORM).where(
            MarketRegimeHistoryORM.asset_id == asset_id,
            MarketRegimeHistoryORM.date == regime.as_of_date,
        )
        entity = self.session.scalar(statement)
        payload = {
            "asset_id": asset_id,
            "symbol": symbol,
            "date": regime.as_of_date,
            "bull_probability": regime.bull_probability,
            "bear_probability": regime.bear_probability,
            "bubble_probability": regime.bubble_probability,
            "dominant_regime": regime.dominant_regime,
            "breakdown_json": regime.breakdown,
            "computed_at": datetime.now(UTC).replace(tzinfo=None),
        }
        if entity is None:
            entity = MarketRegimeHistoryORM(**payload)
            self.session.add(entity)
        else:
            for key, value in payload.items():
                setattr(entity, key, value)
        self.session.flush()
        return entity

    def upsert_history(
        self,
        *,
        asset_id: int,
        symbol: str,
        frame: pd.DataFrame,
    ) -> int:
        inserted = 0
        for row in frame.to_dict(orient="records"):
            regime = MarketRegime(
                bull_probability=float(row["bull_probability"]),
                bear_probability=float(row["bear_probability"]),
                bubble_probability=float(row["bubble_probability"]),
                dominant_regime=str(row["dominant_regime"]),  # type: ignore[arg-type]
                as_of_date=pd.Timestamp(row["date"]).date(),
                breakdown=row.get("breakdown_json") or {},
            )
            existing = self.latest_for_asset_on_date(asset_id, regime.as_of_date)
            self.upsert_regime(asset_id=asset_id, symbol=symbol, regime=regime)
            if existing is None:
                inserted += 1
        return inserted

    def latest_for_asset(self, asset_id: int) -> MarketRegimeHistoryORM | None:
        statement = (
            select(MarketRegimeHistoryORM)
            .where(MarketRegimeHistoryORM.asset_id == asset_id)
            .order_by(MarketRegimeHistoryORM.date.desc())
            .limit(1)
        )
        return self.session.scalar(statement)

    def latest_for_asset_on_date(
        self,
        asset_id: int,
        regime_date: date,
    ) -> MarketRegimeHistoryORM | None:
        statement = select(MarketRegimeHistoryORM).where(
            MarketRegimeHistoryORM.asset_id == asset_id,
            MarketRegimeHistoryORM.date == regime_date,
        )
        return self.session.scalar(statement)

    def history_for_asset(
        self,
        asset_id: int,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> pd.DataFrame:
        statement = select(MarketRegimeHistoryORM).where(
            MarketRegimeHistoryORM.asset_id == asset_id
        )
        if start_date is not None:
            statement = statement.where(MarketRegimeHistoryORM.date >= start_date)
        if end_date is not None:
            statement = statement.where(MarketRegimeHistoryORM.date <= end_date)
        statement = statement.order_by(MarketRegimeHistoryORM.date)
        rows = list(self.session.scalars(statement))
        return pd.DataFrame(
            [
                {
                    "date": row.date,
                    "symbol": row.symbol,
                    "bull_probability": row.bull_probability,
                    "bear_probability": row.bear_probability,
                    "bubble_probability": row.bubble_probability,
                    "dominant_regime": row.dominant_regime,
                    "breakdown_json": row.breakdown_json,
                    "computed_at": row.computed_at,
                }
                for row in rows
            ]
        )

