from __future__ import annotations

import httpx
import pandas as pd

from data.database import AssetORM
from data.providers.base_provider import MarketDataProvider, ProviderError


class BinanceProvider(MarketDataProvider):
    name = "binance"
    base_url = "https://api.binance.com/api/v3/klines"

    def supports(self, asset: AssetORM) -> bool:
        return asset.asset_type == "crypto"

    def fetch_daily_prices(self, asset: AssetORM) -> pd.DataFrame:
        params = {"symbol": asset.symbol, "interval": "1d", "limit": 1000}
        with httpx.Client(timeout=30.0) as client:
            response = client.get(self.base_url, params=params)
            response.raise_for_status()
            payload = response.json()

        if not isinstance(payload, list) or not payload:
            raise ProviderError(f"Binance returned no daily series for {asset.symbol}.")

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
        return frame.sort_values("date").dropna()
