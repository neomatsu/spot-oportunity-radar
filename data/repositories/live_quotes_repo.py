from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from data.database import AssetLiveQuoteORM
from data.providers.base_provider import LiveQuote


class LiveQuotesRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, asset_id: int) -> AssetLiveQuoteORM | None:
        return self.session.scalar(
            select(AssetLiveQuoteORM)
            .where(AssetLiveQuoteORM.asset_id == asset_id)
            .limit(1)
        )

    def upsert(self, asset_id: int, quote: LiveQuote) -> AssetLiveQuoteORM:
        row = self.get(asset_id)
        if row is None:
            row = AssetLiveQuoteORM(asset_id=asset_id)
            self.session.add(row)
        row.session_date = quote.session_date
        row.as_of = self._naive_utc(quote.as_of)
        row.price = quote.price
        row.open = quote.open
        row.high = quote.high
        row.low = quote.low
        row.volume = quote.volume
        row.provider = quote.provider
        row.quote_currency = quote.quote_currency
        row.updated_at = datetime.now(UTC).replace(tzinfo=None)
        self.session.flush()
        return row

    @staticmethod
    def _naive_utc(value: datetime) -> datetime:
        if value.tzinfo is None:
            return value
        return value.astimezone(UTC).replace(tzinfo=None)
