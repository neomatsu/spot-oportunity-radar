from __future__ import annotations

from abc import ABC, abstractmethod

import pandas as pd

from data.database import AssetORM


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
