from __future__ import annotations

import time
from datetime import date
from importlib import import_module

import pandas as pd

from data.database import AssetORM
from data.providers.base_provider import MarketDataProvider, ProviderError


class YFinanceProvider(MarketDataProvider):
    name = "yfinance"
    SUFFIX_ALIASES = {
        ".LON": ".L",
        ".AMS": ".AS",
        ".MIL": ".MI",
        ".FRK": ".F",
    }

    def __init__(
        self,
        *,
        default_period: str = "1y",
        timeout: float = 30.0,
        retries: int = 2,
        backoff_seconds: float = 1.0,
        symbol_aliases: dict[str, str] | None = None,
    ) -> None:
        self.default_period = default_period
        self.timeout = timeout
        self.retries = retries
        self.backoff_seconds = backoff_seconds
        self.symbol_aliases = symbol_aliases or {}

    def supports(self, asset: AssetORM) -> bool:
        return asset.asset_type in {"stock", "etf"}

    def fetch_daily_prices(self, asset: AssetORM) -> pd.DataFrame:
        return self.fetch_daily_prices_for_history(asset, period=self.default_period)

    def fetch_daily_prices_for_history(
        self,
        asset: AssetORM,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
        period: str | None = None,
    ) -> pd.DataFrame:
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            try:
                frame = self._download_history(
                    asset,
                    start_date=start_date,
                    end_date=end_date,
                    period=period,
                )
                if frame.empty:
                    raise ProviderError(f"yfinance returned no daily series for {asset.symbol}.")
                return frame
            except ProviderError:
                raise
            except Exception as exc:
                last_error = exc
                if attempt >= self.retries:
                    break
                time.sleep(self.backoff_seconds * (attempt + 1))

        raise ProviderError(f"yfinance request failed for {asset.symbol}: {last_error}")

    def _download_history(
        self,
        asset: AssetORM,
        *,
        start_date: date | None,
        end_date: date | None,
        period: str | None,
    ) -> pd.DataFrame:
        yf = self._get_yfinance_module()
        history_kwargs: dict[str, object] = {
            "interval": "1d",
            "auto_adjust": False,
            "actions": False,
            "repair": False,
            "timeout": self.timeout,
        }
        if start_date is not None:
            history_kwargs["start"] = start_date.isoformat()
        if end_date is not None:
            history_kwargs["end"] = end_date.isoformat()
        if start_date is None and period:
            history_kwargs["period"] = period
        elif start_date is None:
            history_kwargs["period"] = self.default_period

        for symbol_candidate in self._symbol_candidates(asset):
            history = yf.Ticker(symbol_candidate).history(**history_kwargs)
            if history is None or history.empty:
                continue

            history = history.reset_index()
            if "Date" not in history.columns:
                raise ProviderError(f"yfinance payload for {asset.symbol} is missing Date column.")

            frame = pd.DataFrame(
                {
                    "date": pd.to_datetime(history["Date"]).dt.tz_localize(None).dt.date,
                    "open": pd.to_numeric(history["Open"], errors="coerce"),
                    "high": pd.to_numeric(history["High"], errors="coerce"),
                    "low": pd.to_numeric(history["Low"], errors="coerce"),
                    "close": pd.to_numeric(history["Close"], errors="coerce"),
                    "volume": pd.to_numeric(history["Volume"], errors="coerce"),
                }
            )
            return frame.dropna().sort_values("date").drop_duplicates(
                subset=["date"],
                keep="last",
            )

        return pd.DataFrame()

    @staticmethod
    def _get_yfinance_module():
        try:
            return import_module("yfinance")
        except ModuleNotFoundError as exc:
            raise ProviderError(
                "yfinance is not installed. Run `pip install -e .[dev]` or install `yfinance`."
            ) from exc

    def _symbol_candidates(self, asset: AssetORM) -> list[str]:
        symbol = asset.symbol
        configured_alias = self.symbol_aliases.get(symbol)
        candidates: list[str] = [configured_alias, symbol] if configured_alias else [symbol]

        for source_suffix, yahoo_suffix in self.SUFFIX_ALIASES.items():
            if symbol.endswith(source_suffix):
                candidates.append(symbol.removesuffix(source_suffix) + yahoo_suffix)
                break

        if "." not in symbol and asset.asset_type == "etf" and asset.region in {
            "EU",
            "Global",
            "APAC",
        }:
            candidates.extend(
                [
                    f"{symbol}.DE",
                    f"{symbol}.MI",
                    f"{symbol}.L",
                    f"{symbol}.AS",
                ]
            )

        ordered: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
            if candidate not in seen:
                ordered.append(candidate)
                seen.add(candidate)
        return ordered
