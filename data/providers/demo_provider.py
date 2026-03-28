from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from data.database import AssetORM
from data.providers.base_provider import MarketDataProvider


class DemoMarketDataProvider(MarketDataProvider):
    name = "demo"

    def supports(self, asset: AssetORM) -> bool:
        return True

    def fetch_daily_prices(self, asset: AssetORM) -> pd.DataFrame:
        periods = 420
        end_date = pd.Timestamp(date.today())
        dates = pd.date_range(end=end_date, periods=periods, freq="B")

        seed = abs(hash(asset.symbol)) % (2**32)
        rng = np.random.default_rng(seed)

        base_price = self._base_price(asset)
        drift = self._drift(asset)
        vol = self._volatility(asset)

        returns = rng.normal(loc=drift, scale=vol, size=periods)
        close = base_price * np.exp(np.cumsum(returns))
        close = np.maximum(close, 1.0)

        daily_noise = rng.normal(0, vol * base_price * 0.35, size=periods)
        open_ = np.concatenate([[close[0]], close[:-1]]) + daily_noise
        high = np.maximum(open_, close) * (1 + rng.uniform(0.001, 0.025, size=periods))
        low = np.minimum(open_, close) * (1 - rng.uniform(0.001, 0.025, size=periods))
        volume = rng.integers(500_000, 8_000_000, size=periods)

        frame = pd.DataFrame(
            {
                "date": dates.date,
                "open": np.round(open_, 2),
                "high": np.round(high, 2),
                "low": np.round(low, 2),
                "close": np.round(close, 2),
                "volume": volume.astype(float),
            }
        )
        return frame.sort_values("date").reset_index(drop=True)

    @staticmethod
    def _base_price(asset: AssetORM) -> float:
        if asset.asset_type == "crypto":
            return {
                "BTCUSDT": 60_000.0,
                "ETHUSDT": 3_000.0,
                "SOLUSDT": 140.0,
            }.get(asset.symbol, 100.0)
        if asset.asset_type == "etf":
            return 400.0 if asset.symbol == "SPY" else 450.0
        return {
            "MSFT": 420.0,
            "GOOGL": 170.0,
            "AMZN": 190.0,
            "NVDA": 900.0,
            "ASML": 950.0,
            "TSM": 160.0,
        }.get(asset.symbol, 100.0)

    @staticmethod
    def _drift(asset: AssetORM) -> float:
        if asset.asset_type == "crypto":
            return 0.0010
        if asset.asset_type == "etf":
            return 0.0005
        return 0.0007

    @staticmethod
    def _volatility(asset: AssetORM) -> float:
        if asset.asset_type == "crypto":
            return 0.035
        if asset.asset_type == "etf":
            return 0.012
        return 0.02
