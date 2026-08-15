from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from data.database import FxRateDailyORM


class FxRatesRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert(
        self,
        *,
        source_currency: str,
        target_currency: str,
        rate_date: date,
        rate: float,
        provider: str,
    ) -> FxRateDailyORM:
        source = source_currency.upper()
        target = target_currency.upper()
        statement = select(FxRateDailyORM).where(
            FxRateDailyORM.source_currency == source,
            FxRateDailyORM.target_currency == target,
            FxRateDailyORM.date == rate_date,
        )
        entity = self.session.scalar(statement)
        if entity is None:
            entity = FxRateDailyORM(
                source_currency=source,
                target_currency=target,
                date=rate_date,
                rate=float(rate),
                provider=provider,
            )
            self.session.add(entity)
        else:
            entity.rate = float(rate)
            entity.provider = provider
        self.session.flush()
        return entity

    def latest(
        self,
        source_currency: str,
        target_currency: str,
        *,
        as_of: date | None = None,
    ) -> FxRateDailyORM | None:
        statement = select(FxRateDailyORM).where(
            FxRateDailyORM.source_currency == source_currency.upper(),
            FxRateDailyORM.target_currency == target_currency.upper(),
        )
        if as_of is not None:
            statement = statement.where(FxRateDailyORM.date <= as_of)
        statement = statement.order_by(FxRateDailyORM.date.desc()).limit(1)
        return self.session.scalar(statement)
