from __future__ import annotations

import numpy as np
import pandas as pd

from services.technical_service import TechnicalService


def test_compute_indicators_adds_expected_columns() -> None:
    frame = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=260, freq="D"),
            "open": np.linspace(100, 150, 260),
            "high": np.linspace(101, 151, 260),
            "low": np.linspace(99, 149, 260),
            "close": np.linspace(100, 150, 260),
            "volume": np.full(260, 1_000_000),
        }
    )

    result = TechnicalService.compute_indicators(frame)

    assert {
        "rsi14",
        "sma50",
        "sma200",
        "ema20",
        "atr14",
        "distance_52w_high_pct",
    }.issubset(result.columns)
    assert result["sma200"].iloc[-1] is not None


def test_rsi_is_high_for_consistent_uptrend() -> None:
    frame = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=40, freq="D"),
            "open": np.linspace(100, 140, 40),
            "high": np.linspace(101, 141, 40),
            "low": np.linspace(99, 139, 40),
            "close": np.linspace(100, 140, 40),
            "volume": np.full(40, 1_000_000),
        }
    )

    result = TechnicalService.compute_indicators(frame)

    assert result["rsi14"].iloc[-1] >= 99


def test_compute_indicators_adds_volume_columns() -> None:
    frame = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=30, freq="D"),
            "open": np.linspace(100, 130, 30),
            "high": np.linspace(101, 131, 30),
            "low": np.linspace(99, 129, 30),
            "close": np.linspace(100, 130, 30),
            "volume": np.full(30, 1_000_000),
        }
    )

    result = TechnicalService.compute_indicators(frame)

    assert "vol_sma20" in result.columns
    assert "vol_ratio" in result.columns
    assert "vol_trend" in result.columns
    # Constant volume → vol_ratio should be ~1.0 once enough data
    assert abs(result["vol_ratio"].iloc[-1] - 1.0) < 0.01


def test_compute_indicators_adds_roc_columns() -> None:
    frame = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=30, freq="D"),
            "open": np.linspace(100, 130, 30),
            "high": np.linspace(101, 131, 30),
            "low": np.linspace(99, 129, 30),
            "close": np.linspace(100, 130, 30),
            "volume": np.full(30, 1_000_000),
        }
    )

    result = TechnicalService.compute_indicators(frame)

    assert "roc10" in result.columns
    assert "roc20" in result.columns
    # roc10 should be NaN for first 10 rows and positive for an uptrend
    assert result["roc10"].iloc[:10].isna().all()
    assert result["roc10"].iloc[-1] > 0
