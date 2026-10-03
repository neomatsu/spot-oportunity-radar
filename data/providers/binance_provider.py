from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

import httpx
import pandas as pd

from data.database import AssetORM
from data.providers.base_provider import LiveQuote, MarketDataProvider, ProviderError


class BinanceProvider(MarketDataProvider):
    name = "binance"
    base_url = "https://api.binance.com/api/v3/klines"
    daily_interval_ms = 86_400_000
    interval_ms = {
        "1m": 60_000,
        "3m": 180_000,
        "5m": 300_000,
        "15m": 900_000,
        "30m": 1_800_000,
        "1h": 3_600_000,
        "2h": 7_200_000,
        "4h": 14_400_000,
        "6h": 21_600_000,
        "8h": 28_800_000,
        "12h": 43_200_000,
    }

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

    def fetch_current_quote(self, asset: AssetORM) -> LiveQuote | None:
        now = datetime.now(UTC)
        start = datetime.combine(now.date(), time.min, tzinfo=UTC)
        frame = self.fetch_intraday_prices(
            asset,
            interval="5m",
            start_at=start,
            end_at=now,
        )
        if frame.empty:
            return None
        latest = frame.iloc[-1]
        return LiveQuote(
            session_date=now.date(),
            as_of=pd.Timestamp(latest["open_time"]).to_pydatetime(),
            price=float(latest["close"]),
            open=float(frame.iloc[0]["open"]),
            high=float(frame["high"].max()),
            low=float(frame["low"].min()),
            volume=float(frame["volume"].sum()),
            provider=self.name,
            quote_currency=asset.quote_currency,
        )

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

    def fetch_intraday_prices(
        self,
        asset: AssetORM,
        *,
        interval: str,
        start_at: datetime,
        end_at: datetime,
    ) -> pd.DataFrame:
        """Download an inclusive intraday range using Binance's 1,000-row pages."""
        if not self.supports(asset):
            raise ProviderError("Binance intraday data is only supported for crypto assets")
        if interval not in self.interval_ms:
            raise ValueError(f"Unsupported Binance intraday interval: {interval}")
        if start_at > end_at:
            raise ValueError("start_at must be before end_at")

        cursor_ms = self._datetime_ms(start_at)
        end_ms = self._datetime_ms(end_at)
        step_ms = self.interval_ms[interval]
        payload: list[list] = []

        while cursor_ms <= end_ms:
            page = self._get_klines(
                {
                    "symbol": asset.symbol,
                    "interval": interval,
                    "startTime": cursor_ms,
                    "endTime": end_ms,
                    "limit": 1000,
                }
            )
            if not page:
                break
            payload.extend(page)
            next_cursor_ms = int(page[-1][0]) + step_ms
            if next_cursor_ms <= cursor_ms:
                raise ProviderError(
                    f"Binance intraday pagination did not advance for {asset.symbol}."
                )
            cursor_ms = next_cursor_ms
            if len(page) < 1000:
                break

        return self._payload_to_intraday_frame(asset.symbol, payload)

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
    def _datetime_ms(value: datetime) -> int:
        instant = pd.Timestamp(value)
        if instant.tzinfo is None:
            instant = instant.tz_localize(UTC)
        else:
            instant = instant.tz_convert(UTC)
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

    @staticmethod
    def _payload_to_intraday_frame(symbol: str, payload: list) -> pd.DataFrame:
        if not isinstance(payload, list) or not payload:
            raise ProviderError(f"Binance returned no intraday series for {symbol}.")

        frame = pd.DataFrame(
            [
                {
                    "open_time": pd.to_datetime(item[0], unit="ms", utc=True),
                    "open": float(item[1]),
                    "high": float(item[2]),
                    "low": float(item[3]),
                    "close": float(item[4]),
                    "volume": float(item[5]),
                }
                for item in payload
            ]
        )
        return (
            frame.sort_values("open_time")
            .drop_duplicates("open_time", keep="last")
            .dropna()
        )
