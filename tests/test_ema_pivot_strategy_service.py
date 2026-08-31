from __future__ import annotations

import numpy as np
import pandas as pd

from services.ema_pivot_strategy_service import (
    EmaPivotStrategyConfig,
    EmaPivotStrategyService,
)


def _wave_bars(rows: int = 500) -> pd.DataFrame:
    index = np.arange(rows)
    close = 100 + np.sin(index / 8) * 8 + np.sin(index / 2.5)
    return pd.DataFrame(
        {
            "open_time": pd.date_range("2026-08-01", periods=rows, freq="5min", tz="UTC"),
            "open": close - 0.1,
            "high": close + 0.8,
            "low": close - 0.8,
            "close": close,
            "volume": 100 + np.cos(index / 6) * 10,
        }
    )


def test_pivots_are_not_available_before_right_side_confirmation() -> None:
    config = EmaPivotStrategyConfig(
        pivot_left_bars=3,
        pivot_right_bars=4,
        min_pivot_distance_atr=0,
        volume_window_bars=10_000,
    )

    result = EmaPivotStrategyService(config).analyze(_wave_bars())

    assert result.pivots
    assert all(
        pivot.confirmed_index == pivot.pivot_index + config.pivot_right_bars
        for pivot in result.pivots
    )
    assert all(setup.signal_index >= setup.pivot.confirmed_index for setup in result.setups)


def test_detected_setups_have_consistent_long_and_short_risk_geometry() -> None:
    config = EmaPivotStrategyConfig(
        pivot_left_bars=2,
        pivot_right_bars=2,
        min_pivot_distance_atr=0,
        volume_window_bars=10_000,
    )

    result = EmaPivotStrategyService(config).analyze(_wave_bars())

    assert result.setups
    for setup in result.setups:
        if setup.direction == "LONG":
            assert setup.stop < setup.entry < setup.tp1
            assert setup.pivot.kind == "LOW"
        else:
            assert setup.tp1 < setup.entry < setup.stop
            assert setup.pivot.kind == "HIGH"
        assert 0 <= setup.confirmation_score <= 5


def test_bearish_divergence_can_skip_minor_pivot_and_uses_either_extreme() -> None:
    rows = 24
    high = np.full(rows, 10.0)
    low = np.full(rows, 8.0)
    rsi = np.full(rows, 50.0)
    high[[5, 10, 15]] = [15.0, 14.0, 16.0]
    rsi[[5, 10, 15]] = [75.0, 60.0, 65.0]
    bars = pd.DataFrame(
        {
            "open_time": pd.date_range("2026-08-01", periods=rows, freq="5min", tz="UTC"),
            "high": high,
            "low": low,
            "rsi": rsi,
        }
    )
    service = EmaPivotStrategyService(
        EmaPivotStrategyConfig(
            pivot_left_bars=2,
            pivot_right_bars=2,
            divergence_rsi_window_bars=0,
            divergence_min_rsi_delta=2.0,
        )
    )

    high_pivots = [pivot for pivot in service._detect_pivots(bars) if pivot.kind == "HIGH"]
    divergent = high_pivots[-1]

    assert divergent.divergence is True
    assert divergent.divergence_from_index == 5
    assert divergent.divergence_from_rsi == 75.0
    assert divergent.rsi == 65.0


def test_pivot_rsi_uses_local_extreme_without_looking_past_confirmation() -> None:
    rows = 15
    bars = pd.DataFrame(
        {
            "open_time": pd.date_range("2026-08-01", periods=rows, freq="5min", tz="UTC"),
            "high": np.full(rows, 10.0),
            "low": np.full(rows, 8.0),
            "rsi": np.full(rows, 50.0),
        }
    )
    bars.loc[5, "rsi"] = 68.0
    bars.loc[6, "rsi"] = 74.0
    bars.loc[8, "rsi"] = 90.0
    service = EmaPivotStrategyService(
        EmaPivotStrategyConfig(
            pivot_left_bars=2,
            pivot_right_bars=2,
            divergence_rsi_window_bars=3,
        )
    )

    assert service._pivot_rsi(bars, index=5, kind="HIGH") == 74.0


def test_divergence_does_not_connect_pivots_more_than_six_hours_apart() -> None:
    rows = 100
    high = np.full(rows, 10.0)
    low = np.full(rows, 8.0)
    rsi = np.full(rows, 50.0)
    high[[5, 85]] = [15.0, 16.0]
    rsi[[5, 85]] = [75.0, 65.0]
    bars = pd.DataFrame(
        {
            "open_time": pd.date_range("2026-08-01", periods=rows, freq="5min", tz="UTC"),
            "high": high,
            "low": low,
            "rsi": rsi,
        }
    )
    service = EmaPivotStrategyService(
        EmaPivotStrategyConfig(
            pivot_left_bars=2,
            pivot_right_bars=2,
            divergence_rsi_window_bars=0,
        )
    )

    high_pivots = [pivot for pivot in service._detect_pivots(bars) if pivot.kind == "HIGH"]

    assert high_pivots[-1].pivot_index == 85
    assert high_pivots[-1].divergence is False
