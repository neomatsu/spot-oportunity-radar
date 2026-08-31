from __future__ import annotations

from datetime import UTC, datetime

import pandas as pd
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from data.database import PriceBarIntradayORM


class IntradayPricesRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert_bars(
        self,
        asset_id: int,
        interval: str,
        frame: pd.DataFrame,
        *,
        market: str = "spot",
        provider_name: str = "binance",
        quote_currency: str | None = None,
    ) -> int:
        if frame.empty:
            return 0

        working = frame.copy()
        working["open_time"] = pd.to_datetime(
            working["open_time"], errors="coerce", utc=True
        ).dt.tz_localize(None)
        working = working.dropna(
            subset=["open_time", "open", "high", "low", "close", "volume"]
        )
        working = working.sort_values("open_time").drop_duplicates(
            subset=["open_time"], keep="last"
        )
        if working.empty:
            return 0

        first_time = working["open_time"].min().to_pydatetime()
        last_time = working["open_time"].max().to_pydatetime()
        existing_rows = self.session.scalars(
            select(PriceBarIntradayORM).where(
                PriceBarIntradayORM.asset_id == asset_id,
                PriceBarIntradayORM.market == market,
                PriceBarIntradayORM.interval == interval,
                PriceBarIntradayORM.open_time >= first_time,
                PriceBarIntradayORM.open_time <= last_time,
            )
        )
        existing_by_time = {row.open_time: row for row in existing_rows}

        inserted = 0
        inserted_at = datetime.now(UTC).replace(tzinfo=None)
        for row in working.itertuples(index=False):
            open_time = pd.Timestamp(row.open_time).to_pydatetime()
            existing = existing_by_time.get(open_time)
            if existing is None:
                self.session.add(
                    PriceBarIntradayORM(
                        asset_id=asset_id,
                        market=market,
                        interval=interval,
                        open_time=open_time,
                        open=float(row.open),
                        high=float(row.high),
                        low=float(row.low),
                        close=float(row.close),
                        volume=float(row.volume),
                        provider=provider_name,
                        quote_currency=quote_currency,
                        inserted_at=inserted_at,
                    )
                )
                inserted += 1
                continue

            existing.open = float(row.open)
            existing.high = float(row.high)
            existing.low = float(row.low)
            existing.close = float(row.close)
            existing.volume = float(row.volume)
            existing.provider = provider_name
            existing.quote_currency = quote_currency or existing.quote_currency
            existing.inserted_at = inserted_at

        self.session.flush()
        return inserted

    def get_bars(
        self,
        asset_id: int,
        interval: str,
        *,
        market: str = "spot",
        start_at: datetime,
        end_at: datetime,
    ) -> pd.DataFrame:
        rows = self.session.scalars(
            select(PriceBarIntradayORM)
            .where(
                PriceBarIntradayORM.asset_id == asset_id,
                PriceBarIntradayORM.market == market,
                PriceBarIntradayORM.interval == interval,
                PriceBarIntradayORM.open_time >= self._naive_utc(start_at),
                PriceBarIntradayORM.open_time <= self._naive_utc(end_at),
            )
            .order_by(PriceBarIntradayORM.open_time)
        )
        return pd.DataFrame(
            [
                {
                    "open_time": row.open_time,
                    "open": row.open,
                    "high": row.high,
                    "low": row.low,
                    "close": row.close,
                    "volume": row.volume,
                    "provider": row.provider,
                    "quote_currency": row.quote_currency,
                }
                for row in rows
            ]
        )

    def coverage(
        self,
        asset_id: int,
        interval: str,
        *,
        market: str = "spot",
    ) -> tuple[datetime | None, datetime | None]:
        first_time, last_time = self.session.execute(
            select(
                func.min(PriceBarIntradayORM.open_time),
                func.max(PriceBarIntradayORM.open_time),
            ).where(
                PriceBarIntradayORM.asset_id == asset_id,
                PriceBarIntradayORM.market == market,
                PriceBarIntradayORM.interval == interval,
            )
        ).one()
        return first_time, last_time

    def row_count(self, asset_id: int, interval: str, *, market: str = "spot") -> int:
        return int(
            self.session.scalar(
                select(func.count())
                .select_from(PriceBarIntradayORM)
                .where(
                    PriceBarIntradayORM.asset_id == asset_id,
                    PriceBarIntradayORM.market == market,
                    PriceBarIntradayORM.interval == interval,
                )
            )
            or 0
        )

    @staticmethod
    def _naive_utc(value: datetime) -> datetime:
        timestamp = pd.Timestamp(value)
        if timestamp.tzinfo is not None:
            timestamp = timestamp.tz_convert("UTC").tz_localize(None)
        return timestamp.to_pydatetime()
