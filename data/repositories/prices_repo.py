from __future__ import annotations

from datetime import date

import pandas as pd
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from data.database import PriceBarDailyORM


class PricesRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert_asset_prices(self, asset_id: int, frame: pd.DataFrame) -> int:
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
                    )
                )
                inserted += 1
                continue

            existing.open = float(row.open)
            existing.high = float(row.high)
            existing.low = float(row.low)
            existing.close = float(row.close)
            existing.volume = float(row.volume)

        self.session.flush()
        return inserted

    def replace_asset_prices(self, asset_id: int, frame: pd.DataFrame) -> None:
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
