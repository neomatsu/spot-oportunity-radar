from __future__ import annotations

from datetime import UTC, date, datetime

import pandas as pd

from data.database import AssetORM, PriceBarDailyORM
from data.providers.base_provider import LiveQuote
from services.live_quote_service import LiveQuoteService


def _asset(db_session) -> AssetORM:
    asset = AssetORM(
        symbol="TEST",
        name="Test ETF",
        asset_type="etf",
        sector="Broad Market",
        region="EU",
        enabled=True,
        supports_fundamentals=False,
        quote_currency="EUR",
    )
    db_session.add(asset)
    db_session.flush()
    return asset


def test_live_quote_is_stored_separately_from_closed_daily_bars(db_session) -> None:
    asset = _asset(db_session)
    db_session.add(
        PriceBarDailyORM(
            asset_id=asset.id,
            date=date(2026, 8, 28),
            open=100,
            high=102,
            low=99,
            close=101,
            volume=1000,
        )
    )
    quote = LiveQuote(
        session_date=date(2026, 8, 31),
        as_of=datetime(2026, 8, 31, 10, 15, tzinfo=UTC),
        price=103,
        open=101.5,
        high=104,
        low=101,
        volume=500,
        provider="yfinance",
        quote_currency="EUR",
    )
    service = LiveQuoteService(db_session)

    stored = service.repo.upsert(asset.id, quote)

    assert stored.price == 103
    assert db_session.query(PriceBarDailyORM).count() == 1


def test_provisional_bar_is_appended_only_after_latest_closed_session() -> None:
    closed = pd.DataFrame(
        [
            {
                "date": date(2026, 8, 28),
                "open": 100,
                "high": 102,
                "low": 99,
                "close": 101,
                "volume": 1000,
            }
        ]
    )
    quote = LiveQuote(
        session_date=date(2026, 8, 31),
        as_of=datetime(2026, 8, 31, 10, 15, tzinfo=UTC),
        price=103,
        open=101.5,
        high=104,
        low=101,
        volume=500,
        provider="yfinance",
        quote_currency="EUR",
    )

    augmented = LiveQuoteService.append_provisional_bar(closed, quote)

    assert len(augmented) == 2
    assert bool(augmented.iloc[-1]["is_provisional"]) is True
    assert augmented.iloc[-1]["close"] == 103


def test_quote_does_not_override_closed_bar_for_same_session() -> None:
    closed = pd.DataFrame(
        [
            {
                "date": date(2026, 8, 31),
                "open": 100,
                "high": 105,
                "low": 99,
                "close": 104,
                "volume": 1000,
            }
        ]
    )
    quote = LiveQuote(
        session_date=date(2026, 8, 31),
        as_of=datetime(2026, 8, 31, 20, 0, tzinfo=UTC),
        price=103,
        open=101,
        high=104,
        low=100,
        volume=500,
        provider="yfinance",
    )

    augmented = LiveQuoteService.append_provisional_bar(closed, quote)

    assert len(augmented) == 1
    assert augmented.iloc[-1]["close"] == 104
    assert bool(augmented.iloc[-1]["is_provisional"]) is False
