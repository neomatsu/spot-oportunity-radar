from __future__ import annotations

from typing import Any

import httpx
import pandas as pd

from core.config import get_provider_settings
from data.database import AssetORM
from data.providers.base_provider import MarketDataProvider, ProviderError


class FinancialModelingPrepProvider(MarketDataProvider):
    """FMP provider backed by the current Stable API.

    Uses the stable historical EOD endpoint instead of the deprecated
    `api/v3/historical-price-full/...` legacy route.
    """

    name = "fmp"
    base_url = "https://financialmodelingprep.com/stable"

    def __init__(self) -> None:
        self.api_key = get_provider_settings().fmp_api_key

    def supports(self, asset: AssetORM) -> bool:
        return asset.asset_type in {"stock", "etf"} and bool(self.api_key)

    def fetch_daily_prices(self, asset: AssetORM) -> pd.DataFrame:
        if not self.api_key:
            raise ProviderError("FMP API key is missing.")

        payload = self._request_json(
            "/historical-price-eod/full",
            {"symbol": asset.symbol},
            asset.symbol,
        )
        rows = self._extract_value_rows(payload, asset.symbol, context="historical prices")
        frame = self._parse_price_rows(rows, asset.symbol)
        if frame.empty:
            raise ProviderError(f"FMP returned an empty daily series for {asset.symbol}.")
        return frame

    def fetch_fundamentals(self, asset: AssetORM) -> dict | None:
        if not self.api_key:
            return None

        ratios_payload = self._request_json("/ratios-ttm", {"symbol": asset.symbol}, asset.symbol)
        growth_payload = self._request_json(
            "/financial-growth",
            {"symbol": asset.symbol},
            asset.symbol,
        )
        ratios = self._extract_first_row(ratios_payload, asset.symbol, "ratios")
        growth = self._extract_first_row(growth_payload, asset.symbol, "growth")
        if ratios is None and growth is None:
            return None

        ratios = ratios or {}
        growth = growth or {}
        return {
            "revenue_growth": growth.get("revenueGrowth"),
            "eps_growth": growth.get("epsgrowth"),
            "debt_to_ebitda": ratios.get("debtToEquityRatioTTM"),
            "fcf_margin": ratios.get("freeCashFlowOperatingCashFlowRatioTTM"),
            "pe": ratios.get("priceToEarningsRatioTTM"),
            "ps": ratios.get("priceToSalesRatioTTM"),
            "ev_ebitda": ratios.get("enterpriseValueMultipleTTM"),
        }

    def _request_json(
        self,
        path: str,
        params: dict[str, Any],
        symbol: str,
    ) -> dict[str, Any] | list[Any]:
        request_params = {**params, "apikey": self.api_key}
        try:
            with httpx.Client(timeout=30.0) as client:
                response = client.get(f"{self.base_url}{path}", params=request_params)
                response.raise_for_status()
                payload = response.json()
        except httpx.HTTPStatusError as exc:
            response = exc.response
            response_text = response.text.strip()
            lower_text = response_text.lower()
            if response.status_code == 402 and (
                "special endpoint" in lower_text
                or "current subscription" in lower_text
                or "premium query parameter" in lower_text
            ):
                raise ProviderError(
                    f"FMP subscription restriction for {symbol}: {response_text}"
                ) from exc
            raise ProviderError(
                f"FMP HTTP error for {symbol}: {response.status_code} {response_text}".strip()
            ) from exc
        except httpx.RequestError as exc:
            raise ProviderError(f"FMP network error for {symbol}: {exc}") from exc
        except ValueError as exc:
            raise ProviderError(f"FMP returned invalid JSON for {symbol}.") from exc

        self._raise_for_payload_message(payload, symbol)
        return payload

    @staticmethod
    def _raise_for_payload_message(payload: dict[str, Any] | list[Any], symbol: str) -> None:
        if isinstance(payload, dict):
            for field_name in ("Error Message", "error", "message", "Information"):
                message = payload.get(field_name)
                if not message:
                    continue
                text = str(message)
                if "legacy endpoint" in text.lower():
                    raise ProviderError(f"FMP legacy endpoint message for {symbol}: {text}")
                raise ProviderError(f"FMP error for {symbol}: {text}")

    @staticmethod
    def _extract_value_rows(
        payload: dict[str, Any] | list[Any],
        symbol: str,
        *,
        context: str,
    ) -> list[dict[str, Any]]:
        if isinstance(payload, list):
            rows = payload
        elif isinstance(payload, dict):
            rows = payload.get("value", [])
        else:
            rows = []

        if not isinstance(rows, list) or not rows:
            raise ProviderError(f"FMP returned no {context} for {symbol}.")
        return [row for row in rows if isinstance(row, dict)]

    @classmethod
    def _extract_first_row(
        cls,
        payload: dict[str, Any] | list[Any],
        symbol: str,
        context: str,
    ) -> dict[str, Any] | None:
        rows = cls._extract_value_rows(payload, symbol, context=context)
        return rows[0] if rows else None

    @staticmethod
    def _parse_price_rows(rows: list[dict[str, Any]], symbol: str) -> pd.DataFrame:
        frame = pd.DataFrame(rows)
        required_columns = ["date", "open", "high", "low", "close", "volume"]
        missing = [column for column in required_columns if column not in frame.columns]
        if missing:
            raise ProviderError(
                f"FMP payload for {symbol} is missing expected OHLCV keys: {', '.join(missing)}."
            )

        frame = frame[required_columns].copy()
        frame["date"] = pd.to_datetime(frame["date"], errors="coerce").dt.date
        for column in ["open", "high", "low", "close", "volume"]:
            frame[column] = pd.to_numeric(frame[column], errors="coerce")
        frame = frame.dropna(subset=required_columns)
        return frame.sort_values("date").reset_index(drop=True)
