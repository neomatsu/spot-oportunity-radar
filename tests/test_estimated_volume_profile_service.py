from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from app.components.estimated_volume_profile import build_estimated_volume_profile_figure
from services.estimated_volume_profile_service import EstimatedVolumeProfileService


def _clustered_frame() -> pd.DataFrame:
    rows: list[dict[str, float | pd.Timestamp]] = []
    day = pd.Timestamp("2025-01-01")
    for center, count, volume in ((100.0, 45, 2_000.0), (110.0, 30, 1_500.0), (121.0, 20, 900.0)):
        for index in range(count):
            shift = ((index % 5) - 2) * 0.08
            close = center + shift
            rows.append(
                {
                    "date": day,
                    "open": close - 0.1,
                    "high": close + 0.65,
                    "low": close - 0.65,
                    "close": close,
                    "volume": volume,
                }
            )
            day += pd.Timedelta(days=1)
    return pd.DataFrame(rows)


def test_profile_preserves_total_volume_and_identifies_poc() -> None:
    frame = _clustered_frame()
    result = EstimatedVolumeProfileService(
        min_hvn_intensity=0.2,
        min_hvn_prominence=0.02,
    ).calculate(frame, bins=80, max_hvns=6)

    assert result.profile["estimated_volume"].sum() == pytest.approx(frame["volume"].sum())
    assert result.poc.relative_intensity == pytest.approx(1.0)
    assert abs(result.poc.center - 100.0) < 1.0
    assert result.bars_used == len(frame)


def test_hvns_are_grouped_ranked_and_do_not_duplicate_poc() -> None:
    result = EstimatedVolumeProfileService(
        min_hvn_intensity=0.2,
        min_hvn_prominence=0.02,
        min_hvn_separation_ratio=0.025,
    ).calculate(_clustered_frame(), bins=100, max_hvns=6)

    assert result.hvns
    assert any(node.low <= 110.0 <= node.high for node in result.hvns)
    assert all(not (node.start_bin <= result.poc.start_bin <= node.end_bin) for node in result.hvns)
    assert [node.relative_intensity for node in result.hvns] == sorted(
        (node.relative_intensity for node in result.hvns),
        reverse=True,
    )


def test_default_sensitivity_keeps_meaningful_secondary_volume_node() -> None:
    result = EstimatedVolumeProfileService().calculate(
        _clustered_frame(),
        bins=100,
        max_hvns=6,
    )

    assert any(node.low <= 110.0 <= node.high for node in result.hvns)
    assert all(node.relative_intensity >= 0.40 for node in result.hvns)


def test_distances_and_table_rows_use_current_close() -> None:
    service = EstimatedVolumeProfileService(
        min_hvn_intensity=0.2,
        min_hvn_prominence=0.02,
    )
    result = service.calculate(_clustered_frame(), bins=80, max_hvns=5)
    rows = service.table_rows(result)

    expected_distance = ((result.poc.center - result.current_price) / result.current_price) * 100
    assert result.poc.distance_to_current_price_pct == pytest.approx(expected_distance)
    assert rows[0]["Type"] == "POC"
    assert all(row["Type"] == "HVN" for row in rows[1:])


def test_invalid_or_zero_volume_data_is_rejected() -> None:
    frame = _clustered_frame().drop(columns="volume")
    with pytest.raises(ValueError, match="Missing OHLCV columns"):
        EstimatedVolumeProfileService().calculate(frame)

    zero_volume = _clustered_frame()
    zero_volume["volume"] = 0.0
    with pytest.raises(ValueError, match="positive volume"):
        EstimatedVolumeProfileService().calculate(zero_volume)


def test_plot_contains_candles_horizontal_profile_poc_and_hvns() -> None:
    frame = _clustered_frame()
    result = EstimatedVolumeProfileService(
        min_hvn_intensity=0.2,
        min_hvn_prominence=0.02,
    ).calculate(frame, bins=80, max_hvns=5)
    figure = build_estimated_volume_profile_figure(frame, result, symbol="TEST")

    assert figure.data[0].type == "candlestick"
    assert figure.data[1].type == "bar"
    assert figure.data[1].orientation == "h"
    assert np.all(np.asarray(figure.data[1].x) <= 0)
    assert any(annotation.text.startswith("POC") for annotation in figure.layout.annotations)
