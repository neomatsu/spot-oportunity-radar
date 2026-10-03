from __future__ import annotations

import time
from datetime import UTC, date
from importlib import import_module

import pandas as pd

from data.database import AssetORM
from data.providers.base_provider import LiveQuote, MarketDataProvider, ProviderError


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

    def fetch_current_quote(self, asset: AssetORM) -> LiveQuote | None:
        yf = self._get_yfinance_module()
        last_error: Exception | None = None
        for symbol_candidate in self._symbol_candidates(asset):
            try:
                ticker = yf.Ticker(symbol_candidate)
                history = ticker.history(
                    period="5d",
                    interval="5m",
                    auto_adjust=False,
                    actions=False,
                    repair=False,
                    prepost=False,
                    timeout=self.timeout,
                )
                if history is None or history.empty:
                    continue
                history = history.copy()
                exchange_timestamps = pd.DatetimeIndex(
                    pd.to_datetime(history.index, errors="coerce")
                )
                session_dates = exchange_timestamps.date
                if exchange_timestamps.tz is None:
                    timestamps = exchange_timestamps.tz_localize(UTC)
                else:
                    timestamps = exchange_timestamps.tz_convert(UTC)
                history = history.assign(
                    _timestamp=timestamps,
                    _session_date=session_dates,
                ).dropna(subset=["_timestamp"])
                if history.empty:
                    continue
                latest_session = history.iloc[-1]["_session_date"]
                session = history[history["_session_date"] == latest_session]
                numeric = session[["Open", "High", "Low", "Close", "Volume"]].apply(
                    pd.to_numeric, errors="coerce"
                ).dropna(subset=["Open", "High", "Low", "Close"])
                if numeric.empty:
                    continue
                latest_timestamp = pd.Timestamp(session.iloc[-1]["_timestamp"])
                currency = None
                try:
                    currency = ticker.fast_info.get("currency")
                except Exception:
                    currency = asset.quote_currency
                return LiveQuote(
                    session_date=latest_session,
                    as_of=latest_timestamp.to_pydatetime().astimezone(UTC),
                    price=float(numeric.iloc[-1]["Close"]),
                    open=float(numeric.iloc[0]["Open"]),
                    high=float(numeric["High"].max()),
                    low=float(numeric["Low"].min()),
                    volume=float(numeric["Volume"].fillna(0).sum()),
                    provider=self.name,
                    quote_currency=str(currency or asset.quote_currency or "") or None,
                )
            except Exception as exc:
                last_error = exc
                continue
        if last_error is not None:
            raise ProviderError(
                f"yfinance current quote failed for {asset.symbol}: {last_error}"
            ) from last_error
        return None

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
