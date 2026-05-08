from __future__ import annotations

from unittest.mock import Mock, patch

import pandas as pd
import pytest

from data.database import AssetORM
from data.providers.base_provider import ProviderError
from data.providers.yfinance_provider import YFinanceProvider


def make_asset(symbol: str = "MSFT") -> AssetORM:
    return AssetORM(
        symbol=symbol,
        name="Microsoft",
        asset_type="stock",
        sector="Technology",
        region="US",
        enabled=True,
        supports_fundamentals=True,
    )


def test_yfinance_provider_parses_history_payload() -> None:
    provider = YFinanceProvider(default_period="1y")
    asset = make_asset()
    history = pd.DataFrame(
        {
            "Date": pd.to_datetime(["2026-03-26", "2026-03-27"]),
            "Open": [420.1, 421.2],
            "High": [425.0, 426.3],
            "Low": [418.2, 419.4],
            "Close": [423.5, 424.7],
            "Volume": [1000, 1100],
        }
    )

    mock_module = Mock()
    ticker = mock_module.Ticker.return_value
    with patch.object(provider, "_get_yfinance_module", return_value=mock_module):
        ticker.history.return_value = history

        frame = provider.fetch_daily_prices(asset)

    assert list(frame.columns) == ["date", "open", "high", "low", "close", "volume"]
    assert len(frame) == 2
    assert frame.iloc[-1]["close"] == pytest.approx(424.7)


def test_yfinance_provider_raises_on_empty_history() -> None:
    provider = YFinanceProvider(default_period="1y")
    asset = make_asset()

    mock_module = Mock()
    ticker = mock_module.Ticker.return_value
    with patch.object(provider, "_get_yfinance_module", return_value=mock_module):
        ticker.history.return_value = pd.DataFrame()

        with pytest.raises(ProviderError, match="returned no daily series"):
            provider.fetch_daily_prices(asset)


def test_yfinance_provider_wraps_unexpected_errors() -> None:
    provider = YFinanceProvider(default_period="1y", retries=0)
    asset = make_asset()

    mock_module = Mock()
    ticker = mock_module.Ticker.return_value
    with patch.object(provider, "_get_yfinance_module", return_value=mock_module):
        ticker.history.side_effect = RuntimeError("network down")

        with pytest.raises(ProviderError, match="yfinance request failed"):
            provider.fetch_daily_prices(asset)


def test_yfinance_provider_tries_yahoo_london_alias_after_lon_suffix() -> None:
    provider = YFinanceProvider(default_period="1y")
    asset = make_asset("EIMI.LON")
    empty_history = pd.DataFrame()
    valid_history = pd.DataFrame(
        {
            "Date": pd.to_datetime(["2026-03-27"]),
            "Open": [40.0],
            "High": [41.0],
            "Low": [39.5],
            "Close": [40.5],
            "Volume": [1000],
        }
    )
    mock_module = Mock()
    ticker = mock_module.Ticker.return_value
    ticker.history.side_effect = [empty_history, valid_history]

    with patch.object(provider, "_get_yfinance_module", return_value=mock_module):
        frame = provider.fetch_daily_prices(asset)

    assert len(frame) == 1
    assert mock_module.Ticker.call_args_list[0].args[0] == "EIMI.LON"
    assert mock_module.Ticker.call_args_list[1].args[0] == "EIMI.L"


def test_yfinance_provider_tries_eu_etf_suffix_candidates_for_bare_symbol() -> None:
    provider = YFinanceProvider(default_period="1y")
    asset = AssetORM(
        symbol="IB28",
        name="IB28",
        asset_type="etf",
        sector="Bonds",
        region="EU",
        enabled=True,
        supports_fundamentals=False,
    )
    empty_history = pd.DataFrame()
    valid_history = pd.DataFrame(
        {
            "Date": pd.to_datetime(["2026-03-27"]),
            "Open": [99.0],
            "High": [100.0],
            "Low": [98.5],
            "Close": [99.5],
            "Volume": [1200],
        }
    )
    mock_module = Mock()
    ticker = mock_module.Ticker.return_value
    ticker.history.side_effect = [empty_history, valid_history]

    with patch.object(provider, "_get_yfinance_module", return_value=mock_module):
        frame = provider.fetch_daily_prices(asset)

    assert len(frame) == 1
    assert mock_module.Ticker.call_args_list[0].args[0] == "IB28"
    assert mock_module.Ticker.call_args_list[1].args[0] == "IB28.DE"
