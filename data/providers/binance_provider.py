from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import httpx
import pandas as pd

from data.database import AssetORM
from data.providers.base_provider import MarketDataProvider, ProviderError


class BinanceProvider(MarketDataProvider):
    name = "binance"
    base_url = "https://api.binance.com/api/v3/klines"
    daily_interval_ms = 86_400_000

    def supports(self, asset: AssetORM) -> bool:
        return asset.asset_type == "crypto"

    def __init__(
        self,
        *,
        http_client: httpx.Client | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.http_client = http_client
        self.timeout_seconds = timeout_seconds

    def fetch_daily_prices(self, asset: AssetORM) -> pd.DataFrame:
        payload = self._get_klines(
            {"symbol": asset.symbol, "interval": "1d", "limit": 1000}
        )

        return self._payload_to_frame(asset.symbol, payload)

    def fetch_daily_prices_for_history(
        self,
        asset: AssetORM,
        *,
        start_date: date,
        end_date: date,
    ) -> pd.DataFrame:
        """Download an inclusive daily range using Binance's 1,000-row pages."""
        if start_date > end_date:
            raise ValueError("start_date must be before end_date")

        cursor_ms = self._start_of_day_ms(start_date)
        end_ms = self._start_of_day_ms(end_date + timedelta(days=1)) - 1
        payload: list[list] = []

        while cursor_ms <= end_ms:
            page = self._get_klines(
                {
                    "symbol": asset.symbol,
                    "interval": "1d",
                    "startTime": cursor_ms,
                    "endTime": end_ms,
                    "limit": 1000,
                }
            )
            if not page:
                break
            payload.extend(page)
            next_cursor_ms = int(page[-1][0]) + self.daily_interval_ms
            if next_cursor_ms <= cursor_ms:
                raise ProviderError(
                    f"Binance pagination did not advance for {asset.symbol}."
                )
            cursor_ms = next_cursor_ms
            if len(page) < 1000:
                break

        return self._payload_to_frame(asset.symbol, payload)

    def _get_klines(self, params: dict[str, object]) -> list:
        try:
            if self.http_client is not None:
                response = self.http_client.get(self.base_url, params=params)
            else:
                with httpx.Client(timeout=self.timeout_seconds) as client:
                    response = client.get(self.base_url, params=params)
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise ProviderError(f"Binance request failed: {exc}") from exc
        payload = response.json()
        if not isinstance(payload, list):
            raise ProviderError("Binance returned an invalid daily-series payload.")
        return payload

    @staticmethod
    def _start_of_day_ms(value: date) -> int:
        instant = datetime.combine(value, time.min, tzinfo=UTC)
        return int(instant.timestamp() * 1000)

    @staticmethod
    def _payload_to_frame(symbol: str, payload: list) -> pd.DataFrame:

        if not isinstance(payload, list) or not payload:
            raise ProviderError(f"Binance returned no daily series for {symbol}.")

        frame = pd.DataFrame(
            [
                {
                    "date": pd.to_datetime(item[0], unit="ms").date(),
                    "open": float(item[1]),
                    "high": float(item[2]),
                    "low": float(item[3]),
                    "close": float(item[4]),
                    "volume": float(item[5]),
                }
                for item in payload
            ]
        )
        return frame.sort_values("date").drop_duplicates("date", keep="last").dropna()
