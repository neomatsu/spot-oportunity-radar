from __future__ import annotations

from data.providers.binance_provider import BinanceProvider


class BinanceUsdMFuturesProvider(BinanceProvider):
    """Public USD-M Futures klines, kept separate from Binance spot data."""

    name = "binance_usdm_futures"
    base_url = "https://fapi.binance.com/fapi/v1/klines"
