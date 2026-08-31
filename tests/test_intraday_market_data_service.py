from __future__ import annotations

from datetime import datetime, timedelta

import pandas as pd

from data.database import AssetORM, PriceBarDailyORM, PriceBarIntradayORM
from services.intraday_market_data_service import IntradayMarketDataService


class StubIntradayProvider:
    name = "binance"

    def __init__(self) -> None:
        self.calls: list[tuple[str, datetime, datetime]] = []

    def fetch_intraday_prices(
        self,
        asset: AssetORM,
        *,
        interval: str,
        start_at: datetime,
        end_at: datetime,
    ) -> pd.DataFrame:
        self.calls.append((interval, start_at, end_at))
        frequency = {"5m": "5min", "15m": "15min", "1h": "1h", "4h": "4h"}[interval]
        timestamps = pd.date_range(start_at, end_at, freq=frequency)
        return pd.DataFrame(
            {
                "open_time": timestamps,
                "open": 100.0,
                "high": 102.0,
                "low": 99.0,
                "close": 101.0,
                "volume": 10.0,
            }
        )


def _crypto_asset(db_session) -> AssetORM:
    asset = AssetORM(
        symbol="BTCUSDT",
        name="Bitcoin",
        asset_type="crypto",
        sector="Crypto",
        region="Global",
        enabled=True,
        supports_fundamentals=False,
        quote_currency="USDT",
    )
    db_session.add(asset)
    db_session.flush()
    return asset


def test_intraday_cache_is_separate_from_daily_prices(db_session) -> None:
    asset = _crypto_asset(db_session)
    provider = StubIntradayProvider()
    start_at = datetime(2026, 8, 1)
    end_at = start_at + timedelta(hours=1)

    result = IntradayMarketDataService(
        db_session,
        provider=provider,
    ).get_crypto_bars(asset, start_at=start_at, end_at=end_at)

    assert result.interval == "5m"
    assert result.cached_rows == 13
    assert db_session.query(PriceBarIntradayORM).count() == 13
    assert db_session.query(PriceBarDailyORM).count() == 0


def test_second_intraday_read_uses_cache_and_extension_is_incremental(db_session) -> None:
    asset = _crypto_asset(db_session)
    provider = StubIntradayProvider()
    service = IntradayMarketDataService(db_session, provider=provider)
    start_at = datetime(2026, 8, 1)
    first_end = start_at + timedelta(hours=1)

    service.get_crypto_bars(asset, start_at=start_at, end_at=first_end)
    cached = service.get_crypto_bars(asset, start_at=start_at, end_at=first_end)
    extended = service.get_crypto_bars(
        asset,
        start_at=start_at,
        end_at=first_end + timedelta(minutes=10),
    )

    assert len(provider.calls) == 2
    assert cached.downloaded_rows == 0
    assert extended.cached_rows == 15
    assert provider.calls[-1][1] == first_end + timedelta(minutes=5)


def test_adaptive_intraday_interval_depends_on_requested_range() -> None:
    start_at = datetime(2025, 1, 1)

    assert IntradayMarketDataService.interval_for_range(
        start_at, start_at + timedelta(days=7)
    ) == "5m"
    assert IntradayMarketDataService.interval_for_range(
        start_at, start_at + timedelta(days=90)
    ) == "15m"
    assert IntradayMarketDataService.interval_for_range(
        start_at, start_at + timedelta(days=365)
    ) == "1h"
    assert IntradayMarketDataService.interval_for_range(
        start_at, start_at + timedelta(days=730)
    ) == "4h"


def test_spot_and_futures_intraday_caches_are_isolated(db_session) -> None:
    asset = _crypto_asset(db_session)
    start_at = datetime(2026, 8, 1)
    end_at = start_at + timedelta(minutes=10)

    IntradayMarketDataService(
        db_session,
        provider=StubIntradayProvider(),
        market="spot",
    ).get_crypto_bars(asset, start_at=start_at, end_at=end_at, interval="5m")
    futures_result = IntradayMarketDataService(
        db_session,
        provider=StubIntradayProvider(),
        market="usd_m_futures",
    ).get_crypto_bars(asset, start_at=start_at, end_at=end_at, interval="5m")

    assert futures_result.cached_rows == 3
    assert db_session.query(PriceBarIntradayORM).count() == 6
    assert {
        row.market for row in db_session.query(PriceBarIntradayORM).all()
    } == {"spot", "usd_m_futures"}
