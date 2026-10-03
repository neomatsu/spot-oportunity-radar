from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime

import pandas as pd

from data.database import AssetORM


@dataclass(frozen=True, slots=True)
class LiveQuote:
    session_date: date
    as_of: datetime
    price: float
    open: float
    high: float
    low: float
    volume: float
    provider: str
    quote_currency: str | None = None


class ProviderError(RuntimeError):
    pass


class MarketDataProvider(ABC):
    name: str

    @abstractmethod
    def supports(self, asset: AssetORM) -> bool:
        raise NotImplementedError

    @abstractmethod
    def fetch_daily_prices(self, asset: AssetORM) -> pd.DataFrame:
        raise NotImplementedError

    def fetch_fundamentals(self, asset: AssetORM) -> dict | None:
        return None

    def fetch_current_quote(self, asset: AssetORM) -> LiveQuote | None:
        return None
