from __future__ import annotations

from unittest.mock import Mock, patch

import httpx
import pandas as pd
import pytest

from data.database import AssetORM
from data.providers.base_provider import ProviderError
from data.providers.fmp_provider import FinancialModelingPrepProvider


def make_asset(symbol: str = "GOOGL") -> AssetORM:
    return AssetORM(
        symbol=symbol,
        name="Alphabet",
        asset_type="stock",
        sector="Technology",
        region="US",
        enabled=True,
        supports_fundamentals=True,
    )


def test_fmp_provider_parses_stable_historical_payload() -> None:
    provider = FinancialModelingPrepProvider()
    provider.api_key = "test-key"
    asset = make_asset()

    payload = {
        "value": [
            {
                "symbol": "GOOGL",
                "date": "2026-03-27",
                "open": 277.275,
                "high": 279.37,
                "low": 275.27,
                "close": 278.5572,
                "volume": 11286866,
            },
            {
                "symbol": "GOOGL",
                "date": "2026-03-26",
                "open": 287.91,
                "high": 287.95,
                "low": 278.5,
                "close": 280.92,
                "volume": 39080578,
            },
        ]
    }

    with patch("httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        response = Mock()
        response.json.return_value = payload
        response.raise_for_status.return_value = None
        client.get.return_value = response

        frame = provider.fetch_daily_prices(asset)

    assert isinstance(frame, pd.DataFrame)
    assert list(frame.columns) == ["date", "open", "high", "low", "close", "volume"]
    assert len(frame) == 2
    assert frame.iloc[-1]["close"] == pytest.approx(278.5572)


def test_fmp_provider_raises_on_legacy_endpoint_message() -> None:
    provider = FinancialModelingPrepProvider()
    provider.api_key = "test-key"
    asset = make_asset()

    payload = {
        "Error Message": (
            "Legacy Endpoint : Due to Legacy endpoints being no longer supported"
        )
    }

    with patch("httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        response = Mock()
        response.json.return_value = payload
        response.raise_for_status.return_value = None
        client.get.return_value = response

        with pytest.raises(ProviderError, match="legacy endpoint"):
            provider.fetch_daily_prices(asset)


def test_fmp_provider_raises_on_empty_value_payload() -> None:
    provider = FinancialModelingPrepProvider()
    provider.api_key = "test-key"
    asset = make_asset()

    with patch("httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        response = Mock()
        response.json.return_value = {"value": []}
        response.raise_for_status.return_value = None
        client.get.return_value = response

        with pytest.raises(ProviderError, match="returned no historical prices"):
            provider.fetch_daily_prices(asset)


def test_fmp_provider_raises_subscription_restriction_on_402() -> None:
    provider = FinancialModelingPrepProvider()
    provider.api_key = "test-key"
    asset = make_asset("ASML")

    with patch("httpx.Client") as client_cls:
        client = client_cls.return_value.__enter__.return_value
        response = Mock()
        response.status_code = 402
        response.text = (
            "Premium Query Parameter: 'Special Endpoint : This value set for "
            "'symbol' is not available under your current subscription"
        )
        client.get.side_effect = httpx.HTTPStatusError(
            "payment required",
            request=Mock(),
            response=response,
        )

        with pytest.raises(ProviderError, match="subscription restriction"):
            provider.fetch_daily_prices(asset)
