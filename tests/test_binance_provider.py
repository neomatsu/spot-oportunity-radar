from __future__ import annotations

from datetime import UTC, date, datetime

import httpx

from data.database import AssetORM
from data.providers.binance_futures_provider import BinanceUsdMFuturesProvider
from data.providers.binance_provider import BinanceProvider


def test_binance_history_paginates_until_requested_end() -> None:
    calls: list[httpx.Request] = []
    first_open = int(datetime(2018, 1, 1, tzinfo=UTC).timestamp()) * 1000
    day_ms = 86_400_000

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        start_ms = int(request.url.params["startTime"])
        count = 1000 if len(calls) == 1 else 2
        rows = [
            [start_ms + index * day_ms, "1", "2", "0.5", "1.5", "10"]
            for index in range(count)
        ]
        return httpx.Response(200, json=rows)

    asset = AssetORM(symbol="BTCUSDT", name="Bitcoin", asset_type="crypto")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        frame = BinanceProvider(http_client=client).fetch_daily_prices_for_history(
            asset,
            start_date=date(2018, 1, 1),
            end_date=date(2020, 12, 31),
        )

    assert len(calls) == 2
    assert len(frame) == 1002
    assert frame["date"].is_unique
    assert int(calls[1].url.params["startTime"]) > first_open


def test_binance_intraday_history_uses_requested_interval_and_paginates() -> None:
    calls: list[httpx.Request] = []
    start_at = datetime(2026, 8, 1, tzinfo=UTC)
    five_minutes_ms = 300_000

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        cursor_ms = int(request.url.params["startTime"])
        count = 1000 if len(calls) == 1 else 3
        rows = [
            [cursor_ms + index * five_minutes_ms, "1", "2", "0.5", "1.5", "10"]
            for index in range(count)
        ]
        return httpx.Response(200, json=rows)

    asset = AssetORM(symbol="BTCUSDT", name="Bitcoin", asset_type="crypto")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        frame = BinanceProvider(http_client=client).fetch_intraday_prices(
            asset,
            interval="5m",
            start_at=start_at,
            end_at=start_at.replace(day=10),
        )

    assert len(calls) == 2
    assert all(request.url.params["interval"] == "5m" for request in calls)
    assert len(frame) == 1003
    assert frame["open_time"].is_unique
    assert str(frame["open_time"].dt.tz) == "UTC"


def test_binance_usdm_futures_uses_public_futures_klines_endpoint() -> None:
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        return httpx.Response(
            200,
            json=[
                [
                    int(request.url.params["startTime"]),
                    "1",
                    "2",
                    "0.5",
                    "1.5",
                    "10",
                ]
            ],
        )

    asset = AssetORM(symbol="BTCUSDT", name="Bitcoin", asset_type="crypto")
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        provider = BinanceUsdMFuturesProvider(http_client=client)
        provider.fetch_intraday_prices(
            asset,
            interval="5m",
            start_at=datetime(2026, 8, 1, tzinfo=UTC),
            end_at=datetime(2026, 8, 1, 0, 5, tzinfo=UTC),
        )

    assert provider.name == "binance_usdm_futures"
    assert requested_urls[0].startswith("https://fapi.binance.com/fapi/v1/klines")
