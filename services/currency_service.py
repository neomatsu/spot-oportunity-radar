from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd
from sqlalchemy.orm import Session

from core.config import load_assets_config
from core.currency import infer_quote_currency
from data.database import AssetORM
from data.repositories.fx_rates_repo import FxRatesRepository


@dataclass(frozen=True)
class CurrencyConversion:
    source_currency: str
    target_currency: str
    rate: float | None
    rate_date: date | None
    provider: str | None

    @property
    def available(self) -> bool:
        return self.rate is not None


class AssetCurrencyResolver:
    def __init__(self) -> None:
        self._configured = {
            asset.symbol: asset.quote_currency.upper()
            for asset in load_assets_config().assets
            if asset.quote_currency
        }

    def resolve(self, asset: AssetORM) -> str | None:
        if asset.quote_currency:
            return asset.quote_currency.upper()
        configured = self._configured.get(asset.symbol)
        if configured:
            return configured
        return infer_quote_currency(asset.symbol)


class CurrencyService:
    """Converts native quotes to the portfolio currency with a persistent FX cache."""

    def __init__(
        self,
        session: Session,
        *,
        base_currency: str = "EUR",
        loader: Callable[[str, date | None], pd.DataFrame] | None = None,
        max_cache_age_days: int = 7,
    ) -> None:
        self.repo = FxRatesRepository(session)
        self.base_currency = base_currency.upper()
        self.loader = loader or self._download_rate
        self.max_cache_age_days = max_cache_age_days
        self.asset_currency_resolver = AssetCurrencyResolver()

    def asset_currency(self, asset: AssetORM) -> str | None:
        return self.asset_currency_resolver.resolve(asset)

    def conversion(
        self,
        source_currency: str | None,
        *,
        as_of: date | None = None,
    ) -> CurrencyConversion:
        raw_source = (source_currency or "").strip()
        if not raw_source:
            return CurrencyConversion("UNKNOWN", self.base_currency, None, None, None)
        is_pence = raw_source == "GBp" or raw_source.upper() == "GBX"
        source = "GBX" if is_pence else raw_source.upper()
        scale = 0.01 if is_pence else 1.0
        normalized_source = "GBP" if is_pence else source
        if normalized_source == self.base_currency:
            return CurrencyConversion(source, self.base_currency, scale, as_of, "identity")

        cached = self.repo.latest(normalized_source, self.base_currency, as_of=as_of)
        reference_date = as_of or date.today()
        if cached is not None and cached.date >= reference_date - timedelta(
            days=self.max_cache_age_days
        ):
            return CurrencyConversion(
                source,
                self.base_currency,
                cached.rate * scale,
                cached.date,
                cached.provider,
            )

        try:
            frame = self.loader(normalized_source, as_of)
            for row in frame.itertuples(index=False):
                self.repo.upsert(
                    source_currency=normalized_source,
                    target_currency=self.base_currency,
                    rate_date=pd.Timestamp(row.date).date(),
                    rate=float(row.rate),
                    provider="yfinance",
                )
            refreshed = self.repo.latest(normalized_source, self.base_currency, as_of=as_of)
            if refreshed is not None:
                return CurrencyConversion(
                    source,
                    self.base_currency,
                    refreshed.rate * scale,
                    refreshed.date,
                    refreshed.provider,
                )
        except Exception:
            if cached is not None:
                return CurrencyConversion(
                    source,
                    self.base_currency,
                    cached.rate * scale,
                    cached.date,
                    f"{cached.provider}:stale",
                )
        return CurrencyConversion(source, self.base_currency, None, None, None)

    def convert(
        self,
        amount: float,
        source_currency: str | None,
        *,
        as_of: date | None = None,
    ) -> tuple[float | None, CurrencyConversion]:
        conversion = self.conversion(source_currency, as_of=as_of)
        converted = amount * conversion.rate if conversion.rate is not None else None
        return converted, conversion

    def _download_rate(self, source_currency: str, as_of: date | None) -> pd.DataFrame:
        import yfinance as yf

        ticker = f"{source_currency}{self.base_currency}=X"
        end = (as_of + timedelta(days=2)) if as_of else None
        start = (as_of - timedelta(days=14)) if as_of else None
        kwargs: dict[str, object] = {
            "tickers": ticker,
            "interval": "1d",
            "progress": False,
            "auto_adjust": False,
        }
        if start is not None and end is not None:
            kwargs.update(start=start.isoformat(), end=end.isoformat())
        else:
            kwargs["period"] = "1mo"
        downloaded = yf.download(**kwargs)
        if downloaded.empty:
            raise ValueError(f"No FX data returned for {ticker}")
        close = downloaded["Close"]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        return pd.DataFrame(
            {"date": pd.to_datetime(close.index), "rate": close.astype(float).values}
        ).dropna()
