from __future__ import annotations

import time
from typing import Any

import httpx
import pandas as pd

from core.config import get_provider_settings
from core.logger import get_logger
from data.database import AssetORM
from data.providers.base_provider import MarketDataProvider, ProviderError

logger = get_logger(__name__)


class AlphaVantageProvider(MarketDataProvider):
    """Provider para Alpha Vantage compatible con plan gratuito.

    Usa `TIME_SERIES_DAILY`, suficiente para el analisis tecnico del MVP.
    No depende de campos ajustados ni de endpoints premium.

    Alpha Vantage en plan gratuito suele tener un rate limit estricto. Para
    evitar abusos se aplica un throttling sencillo entre peticiones reales.
    """

    name = "alphavantage"
    base_url = "https://www.alphavantage.co/query"
    function = "TIME_SERIES_DAILY"
    outputsize = "compact"
    min_request_interval_seconds = 12.0
    _last_request_monotonic = 0.0

    def __init__(self) -> None:
        self.api_key = get_provider_settings().alphavantage_api_key

    def supports(self, asset: AssetORM) -> bool:
        return asset.asset_type in {"stock", "etf"} and bool(self.api_key)

    def fetch_daily_prices(self, asset: AssetORM) -> pd.DataFrame:
        if not self.api_key:
            raise ProviderError("Alpha Vantage API key is missing.")

        self._throttle()
        payload = self._request_payload(asset.symbol)
        series = self._extract_daily_series(payload, asset.symbol)
        frame = self._parse_daily_series(series, asset.symbol)
        if frame.empty:
            raise ProviderError(f"Alpha Vantage returned an empty daily series for {asset.symbol}.")
        return frame

    def _request_payload(self, symbol: str) -> dict[str, Any]:
        params = {
            "function": self.function,
            "symbol": symbol,
            "outputsize": self.outputsize,
            "apikey": self.api_key,
        }
        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.get(self.base_url, params=params)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPStatusError as exc:
            raise ProviderError(
                f"Alpha Vantage HTTP error for {symbol}: {exc.response.status_code}"
            ) from exc
        except httpx.RequestError as exc:
            raise ProviderError(f"Alpha Vantage network error for {symbol}: {exc}") from exc
        except ValueError as exc:
            raise ProviderError(f"Alpha Vantage returned invalid JSON for {symbol}.") from exc

        if not isinstance(payload, dict):
            raise ProviderError(f"Alpha Vantage returned an unexpected payload type for {symbol}.")
        return payload

    @classmethod
    def _extract_daily_series(cls, payload: dict[str, Any], symbol: str) -> dict[str, Any]:
        note = payload.get("Note")
        if note:
            raise ProviderError(f"Alpha Vantage rate limit reached for {symbol}: {note}")

        information = payload.get("Information")
        if information:
            lower_info = str(information).lower()
            if "premium" in lower_info:
                raise ProviderError(
                    "Alpha Vantage endpoint unavailable on current plan for "
                    f"{symbol}: {information}"
                )
            raise ProviderError(f"Alpha Vantage informational response for {symbol}: {information}")

        error_message = payload.get("Error Message")
        if error_message:
            raise ProviderError(f"Alpha Vantage symbol error for {symbol}: {error_message}")

        series = payload.get("Time Series (Daily)")
        if not isinstance(series, dict) or not series:
            raise ProviderError(f"Alpha Vantage returned no daily series for {symbol}.")
        return series

    @staticmethod
    def _parse_daily_series(series: dict[str, Any], symbol: str) -> pd.DataFrame:
        try:
            frame = (
                pd.DataFrame.from_dict(series, orient="index")
                .rename(
                    columns={
                        "1. open": "open",
                        "2. high": "high",
                        "3. low": "low",
                        "4. close": "close",
                        "5. volume": "volume",
                    }
                )[["open", "high", "low", "close", "volume"]]
                .reset_index(names="date")
            )
        except KeyError as exc:
            raise ProviderError(
                f"Alpha Vantage daily payload for {symbol} is missing expected OHLCV keys."
            ) from exc

        for column in ["open", "high", "low", "close", "volume"]:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date
        frame = frame.dropna(subset=["date", "open", "high", "low", "close", "volume"])
        return frame.sort_values("date").reset_index(drop=True)

    @classmethod
    def _throttle(cls) -> None:
        now = time.monotonic()
        elapsed = now - cls._last_request_monotonic
        if cls._last_request_monotonic and elapsed < cls.min_request_interval_seconds:
            sleep_time = cls.min_request_interval_seconds - elapsed
            logger.info("Alpha Vantage throttle active. Sleeping %.1f seconds.", sleep_time)
            time.sleep(sleep_time)
        cls._last_request_monotonic = time.monotonic()
