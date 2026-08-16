from __future__ import annotations

from datetime import UTC, date, datetime

import httpx

from data.database import AssetORM
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
