from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pandas as pd
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from data.database import PriceBarDailyORM


class PricesRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert_asset_prices(
        self,
        asset_id: int,
        frame: pd.DataFrame,
        *,
        provider_name: str | None = None,
        is_adjusted: bool | None = None,
    ) -> int:
        if frame.empty:
            return 0

        working = frame.copy()
        working["date"] = pd.to_datetime(working["date"]).dt.date
        working = working.dropna(subset=["date", "open", "high", "low", "close", "volume"])
        working = working.sort_values("date").drop_duplicates(subset=["date"], keep="last")

        existing_rows = self.session.execute(
            select(PriceBarDailyORM).where(PriceBarDailyORM.asset_id == asset_id)
        ).scalars()
        existing_by_date = {row.date: row for row in existing_rows}

        inserted = 0
        for row in working.itertuples(index=False):
            bar_date = pd.Timestamp(row.date).date()
            existing = existing_by_date.get(bar_date)
            if existing is None:
                self.session.add(
                    PriceBarDailyORM(
                        asset_id=asset_id,
                        date=bar_date,
                        open=float(row.open),
                        high=float(row.high),
                        low=float(row.low),
                        close=float(row.close),
                        volume=float(row.volume),
                        provider=provider_name,
                        is_adjusted=is_adjusted,
                        inserted_at=datetime.now(UTC).replace(tzinfo=None),
                    )
                )
                inserted += 1
                continue

            existing.open = float(row.open)
            existing.high = float(row.high)
            existing.low = float(row.low)
            existing.close = float(row.close)
            existing.volume = float(row.volume)
            existing.provider = provider_name or existing.provider
            existing.is_adjusted = is_adjusted if is_adjusted is not None else existing.is_adjusted
            existing.inserted_at = datetime.now(UTC).replace(tzinfo=None)

        self.session.flush()
        return inserted

    def replace_asset_prices(
        self,
        asset_id: int,
        frame: pd.DataFrame,
        *,
        provider_name: str | None = None,
        is_adjusted: bool | None = None,
    ) -> None:
        self.session.execute(delete(PriceBarDailyORM).where(PriceBarDailyORM.asset_id == asset_id))
        records = []
        for row in frame.itertuples(index=False):
            records.append(
                PriceBarDailyORM(
                    asset_id=asset_id,
                    date=pd.Timestamp(row.date).date(),
                    open=float(row.open),
                    high=float(row.high),
                    low=float(row.low),
                    close=float(row.close),
                    volume=float(row.volume),
                    provider=provider_name,
                    is_adjusted=is_adjusted,
                    inserted_at=datetime.now(UTC).replace(tzinfo=None),
                )
            )
        self.session.add_all(records)
        self.session.flush()

    def get_asset_prices(self, asset_id: int, limit: int | None = None) -> pd.DataFrame:
        statement = select(PriceBarDailyORM).where(PriceBarDailyORM.asset_id == asset_id)
        statement = statement.order_by(PriceBarDailyORM.date)
        rows = list(self.session.scalars(statement))
        if limit:
            rows = rows[-limit:]
        return pd.DataFrame(
            [
                {
                    "date": row.date,
                    "open": row.open,
                    "high": row.high,
                    "low": row.low,
                    "close": row.close,
                    "volume": row.volume,
                    "provider": row.provider,
                    "is_adjusted": row.is_adjusted,
                    "inserted_at": row.inserted_at,
                }
                for row in rows
            ]
        )

    def latest_date(self, asset_id: int) -> date | None:
        statement = (
            select(PriceBarDailyORM.date)
            .where(PriceBarDailyORM.asset_id == asset_id)
            .order_by(PriceBarDailyORM.date.desc())
            .limit(1)
        )
        return self.session.scalar(statement)

    def row_count(self, asset_id: int) -> int:
        statement = select(func.count()).select_from(PriceBarDailyORM).where(
            PriceBarDailyORM.asset_id == asset_id
        )
        return int(self.session.scalar(statement) or 0)

    def has_data(self, asset_id: int) -> bool:
        return self.row_count(asset_id) > 0

    def earliest_date(self, asset_id: int) -> date | None:
        statement = (
            select(PriceBarDailyORM.date)
            .where(PriceBarDailyORM.asset_id == asset_id)
            .order_by(PriceBarDailyORM.date.asc())
            .limit(1)
        )
        return self.session.scalar(statement)

    def distinct_providers(
        self,
        asset_id: int,
        *,
        since_date: date | None = None,
    ) -> list[str]:
        statement = select(PriceBarDailyORM.provider).where(PriceBarDailyORM.asset_id == asset_id)
        if since_date is not None:
            statement = statement.where(PriceBarDailyORM.date >= since_date)
        statement = statement.distinct()
        return [
            str(provider)
            for provider in self.session.scalars(statement)
            if provider not in {None, ""}
        ]

    def has_recent_provider_mix(self, asset_id: int, window_days: int = 90) -> bool:
        latest_date = self.latest_date(asset_id)
        if latest_date is None:
            return False
        since_date = latest_date - timedelta(days=window_days)
        return len(self.distinct_providers(asset_id, since_date=since_date)) > 1
