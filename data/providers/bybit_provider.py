from __future__ import annotations

import httpx
import pandas as pd

from data.database import AssetORM
from data.providers.base_provider import MarketDataProvider, ProviderError


class BybitProvider(MarketDataProvider):
    name = "bybit"
    base_url = "https://api.bybit.com/v5/market/kline"

    def supports(self, asset: AssetORM) -> bool:
        return asset.asset_type == "crypto"

    def fetch_daily_prices(self, asset: AssetORM) -> pd.DataFrame:
        # Bybit paginates with a max of 1000 candles per request; fetch two pages to get ~2 years
        frames: list[pd.DataFrame] = []
        end_time: int | None = None

        for _ in range(2):
            params: dict[str, object] = {
                "symbol": asset.symbol,
                "interval": "D",
                "limit": 1000,
            }
            if end_time is not None:
                params["end"] = end_time

            with httpx.Client(timeout=30.0) as client:
                response = client.get(self.base_url, params=params)
                response.raise_for_status()
                payload = response.json()

            if payload.get("retCode") != 0:
                raise ProviderError(
                    f"Bybit error for {asset.symbol}: {payload.get('retMsg', 'unknown error')}"
                )

            raw_list = payload.get("result", {}).get("list", [])
            if not raw_list:
                break

            frame = pd.DataFrame(
                [
                    {
                        "date": pd.to_datetime(int(item[0]), unit="ms").date(),
                        "open": float(item[1]),
                        "high": float(item[2]),
                        "low": float(item[3]),
                        "close": float(item[4]),
                        "volume": float(item[5]),
                    }
                    for item in raw_list
                ]
            )
            frames.append(frame)

            # Bybit returns newest first; oldest timestamp drives the next page
            oldest_ts = int(raw_list[-1][0])
            if oldest_ts <= 0:
                break
            end_time = oldest_ts - 1  # exclusive upper bound for next page

        if not frames:
            raise ProviderError(f"Bybit returned no daily series for {asset.symbol}.")

        result = pd.concat(frames, ignore_index=True)
        result = result.drop_duplicates(subset=["date"]).sort_values("date").dropna()
        return result
