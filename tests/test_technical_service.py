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
