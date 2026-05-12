"""DexScreener API client for the Crypto Pump Radar module.

API docs: https://docs.dexscreener.com/api/reference

This provider is INDEPENDENT from the main investment pipeline. It does not
implement MarketDataProvider because it works with DEX pair data, not OHLC bars.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

import httpx

from core.logger import get_logger

logger = get_logger(__name__)


class DexScreenerError(RuntimeError):
    """Raised when DexScreener returns an unrecoverable error."""


@dataclass(slots=True)
class DexPair:
    """Normalized DexScreener pair payload used by the radar pipeline."""

    chain: str
    dex_id: str | None
    pair_address: str
    symbol: str
    base_token_name: str | None
    base_token_address: str | None
    base_token_symbol: str | None
    quote_token_symbol: str | None
    price_usd: float | None
    liquidity_usd: float | None
    fdv: float | None
    market_cap: float | None
    volume_5m: float | None
    volume_1h: float | None
    volume_6h: float | None
    volume_24h: float | None
    buys_5m: int | None
    sells_5m: int | None
    buys_1h: int | None
    sells_1h: int | None
    buys_6h: int | None
    sells_6h: int | None
    buys_24h: int | None
    sells_24h: int | None
    price_change_5m: float | None
    price_change_1h: float | None
    price_change_6h: float | None
    price_change_24h: float | None
    pair_created_at: datetime | None
    pair_age_hours: float | None
    url: str | None
    raw_payload: dict[str, Any] = field(default_factory=dict)

    @property
    def dedupe_key(self) -> str:
        return f"{self.chain.lower()}:{self.pair_address.lower()}"


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if result != result:  # NaN
        return None
    return result


def _safe_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _parse_pair(payload: dict[str, Any]) -> DexPair | None:
    """Convert a raw DexScreener pair dict into a DexPair. Returns None if invalid."""

    pair_address = payload.get("pairAddress")
    chain_id = payload.get("chainId")
    if not pair_address or not chain_id:
        return None

    base_token = payload.get("baseToken") or {}
    quote_token = payload.get("quoteToken") or {}
    liquidity = payload.get("liquidity") or {}
    volume = payload.get("volume") or {}
    txns = payload.get("txns") or {}
    price_change = payload.get("priceChange") or {}

    def _txn_pair(window: str) -> tuple[int | None, int | None]:
        slot = txns.get(window) or {}
        return _safe_int(slot.get("buys")), _safe_int(slot.get("sells"))

    buys_5m, sells_5m = _txn_pair("m5")
    buys_1h, sells_1h = _txn_pair("h1")
    buys_6h, sells_6h = _txn_pair("h6")
    buys_24h, sells_24h = _txn_pair("h24")

    pair_created_ms = payload.get("pairCreatedAt")
    pair_created_at: datetime | None = None
    pair_age_hours: float | None = None
    if isinstance(pair_created_ms, int | float) and pair_created_ms > 0:
        try:
            pair_created_at = datetime.fromtimestamp(pair_created_ms / 1000, tz=UTC)
            pair_age_hours = max(
                0.0,
                (datetime.now(UTC) - pair_created_at).total_seconds() / 3600.0,
            )
        except (OverflowError, OSError, ValueError):
            pair_created_at = None
            pair_age_hours = None

    base_symbol = base_token.get("symbol") or ""
    quote_symbol = quote_token.get("symbol") or ""
    if base_symbol and quote_symbol:
        symbol = f"{base_symbol}/{quote_symbol}"
    else:
        symbol = base_symbol or quote_symbol or str(pair_address)[:10]

    return DexPair(
        chain=str(chain_id),
        dex_id=payload.get("dexId"),
        pair_address=str(pair_address),
        symbol=symbol,
        base_token_name=base_token.get("name"),
        base_token_address=base_token.get("address"),
        base_token_symbol=base_token.get("symbol"),
        quote_token_symbol=quote_token.get("symbol"),
        price_usd=_safe_float(payload.get("priceUsd")),
        liquidity_usd=_safe_float(liquidity.get("usd")),
        fdv=_safe_float(payload.get("fdv")),
        market_cap=_safe_float(payload.get("marketCap")),
        volume_5m=_safe_float(volume.get("m5")),
        volume_1h=_safe_float(volume.get("h1")),
        volume_6h=_safe_float(volume.get("h6")),
        volume_24h=_safe_float(volume.get("h24")),
        buys_5m=buys_5m,
        sells_5m=sells_5m,
        buys_1h=buys_1h,
        sells_1h=sells_1h,
        buys_6h=buys_6h,
        sells_6h=sells_6h,
        buys_24h=buys_24h,
        sells_24h=sells_24h,
        price_change_5m=_safe_float(price_change.get("m5")),
        price_change_1h=_safe_float(price_change.get("h1")),
        price_change_6h=_safe_float(price_change.get("h6")),
        price_change_24h=_safe_float(price_change.get("h24")),
        pair_created_at=pair_created_at,
        pair_age_hours=pair_age_hours,
        url=payload.get("url"),
        raw_payload=payload,
    )


class DexScreenerProvider:
    """Lightweight client for the DexScreener public API.

    All methods are defensive: rate limits, malformed payloads or HTTP errors are
    caught and logged. Callers receive an empty list rather than an exception
    unless something genuinely catastrophic happened.
    """

    name = "dexscreener"
    base_url = "https://api.dexscreener.com"

    def __init__(
        self,
        *,
        timeout: float = 20.0,
        client: httpx.Client | None = None,
    ) -> None:
        self.timeout = timeout
        self._external_client = client

    def _client(self) -> httpx.Client:
        if self._external_client is not None:
            return self._external_client
        return httpx.Client(timeout=self.timeout)

    def _get_json(self, path: str, *, params: dict[str, Any] | None = None) -> Any:
        url = f"{self.base_url}{path}"
        try:
            if self._external_client is not None:
                response = self._external_client.get(url, params=params)
            else:
                with httpx.Client(timeout=self.timeout) as client:
                    response = client.get(url, params=params)
        except httpx.HTTPError as exc:
            logger.warning("DexScreener HTTP error on %s: %s", url, exc)
            return None

        if response.status_code == 429:
            logger.warning("DexScreener rate limit hit on %s", url)
            return None
        if response.status_code >= 500:
            logger.warning(
                "DexScreener server error %s on %s", response.status_code, url
            )
            return None
        if response.status_code >= 400:
            logger.info(
                "DexScreener client error %s on %s", response.status_code, url
            )
            return None

        try:
            return response.json()
        except ValueError as exc:
            logger.warning("DexScreener returned invalid JSON: %s", exc)
            return None

    def search(self, query: str) -> list[DexPair]:
        """Search for pairs matching a free-text query.

        Mapping: /latest/dex/search?q=<query>
        """

        if not query:
            return []
        payload = self._get_json("/latest/dex/search", params={"q": query})
        if not payload:
            return []
        pairs = payload.get("pairs") or []
        result: list[DexPair] = []
        for raw in pairs:
            try:
                parsed = _parse_pair(raw)
                if parsed is not None:
                    result.append(parsed)
            except Exception as exc:  # defensive
                logger.debug("Failed to parse DexScreener pair: %s", exc)
        return result

    def get_pair(self, chain: str, pair_address: str) -> DexPair | None:
        """Fetch a specific pair by chain and pair address."""

        if not chain or not pair_address:
            return None
        payload = self._get_json(f"/latest/dex/pairs/{chain}/{pair_address}")
        if not payload:
            return None
        pairs = payload.get("pairs") or payload.get("pair") or []
        if isinstance(pairs, dict):
            pairs = [pairs]
        for raw in pairs:
            parsed = _parse_pair(raw)
            if parsed is not None:
                return parsed
        return None

    def get_token_pairs(self, token_address: str) -> list[DexPair]:
        """Fetch all pairs for a token address (across chains)."""

        if not token_address:
            return []
        payload = self._get_json(f"/latest/dex/tokens/{token_address}")
        if not payload:
            return []
        pairs = payload.get("pairs") or []
        result: list[DexPair] = []
        for raw in pairs:
            parsed = _parse_pair(raw)
            if parsed is not None:
                result.append(parsed)
        return result

    @staticmethod
    def parse_pair(payload: dict[str, Any]) -> DexPair | None:
        """Public hook so tests can build DexPair objects from raw fixtures."""

        return _parse_pair(payload)
