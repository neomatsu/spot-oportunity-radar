from __future__ import annotations

import pytest

from data.providers.alphavantage_provider import AlphaVantageProvider
from data.providers.base_provider import ProviderError


def test_parse_time_series_daily_valid_payload() -> None:
    series = {
        "2026-03-26": {
            "1. open": "421.10",
            "2. high": "425.00",
            "3. low": "418.55",
            "4. close": "423.87",
            "5. volume": "18392000",
        },
        "2026-03-25": {
            "1. open": "418.20",
            "2. high": "422.80",
            "3. low": "417.00",
            "4. close": "421.11",
            "5. volume": "17500000",
        },
    }

    frame = AlphaVantageProvider._parse_daily_series(series, "MSFT")

    assert list(frame.columns) == ["date", "open", "high", "low", "close", "volume"]
    assert len(frame) == 2
    assert frame.iloc[-1]["close"] == 423.87


def test_extract_daily_series_rejects_premium_or_information_payload() -> None:
    payload = {
        "Information": (
            "Thank you for using Alpha Vantage! This is a premium endpoint."
        )
    }

    with pytest.raises(ProviderError, match="endpoint unavailable on current plan"):
        AlphaVantageProvider._extract_daily_series(payload, "MSFT")


def test_extract_daily_series_rejects_rate_limit_payload() -> None:
    payload = {
        "Note": (
            "Thank you for using Alpha Vantage! "
            "Our standard API rate limit is 25 requests per day."
        )
    }

    with pytest.raises(ProviderError, match="rate limit"):
        AlphaVantageProvider._extract_daily_series(payload, "MSFT")


def test_extract_daily_series_rejects_empty_payload() -> None:
    with pytest.raises(ProviderError, match="returned no daily series"):
        AlphaVantageProvider._extract_daily_series({}, "MSFT")
