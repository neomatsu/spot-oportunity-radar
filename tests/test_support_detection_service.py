from __future__ import annotations

import pandas as pd

from services.support_detection_service import SupportDetectionService


def _make_frame() -> pd.DataFrame:
    closes = [
        100,
        99,
        97,
        95,
        98,
        101,
        103,
        102,
        100,
        97,
        94,
        96,
        99,
        103,
        106,
        104,
        102,
        100,
        97,
        95,
        98,
        102,
        105,
        108,
        107,
        105,
        103,
        100,
        98,
        96,
        99,
        104,
        109,
        111,
        110,
        108,
        105,
        102,
        99,
        97,
    ]
    return pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=len(closes), freq="D"),
            "open": closes,
            "high": [value + 1.5 for value in closes],
            "low": [value - 1.5 for value in closes],
            "close": closes,
            "volume": [1_000_000] * len(closes),
        }
    )


def test_simple_support_zone_returns_valid_legacy_band() -> None:
    service = SupportDetectionService({"method": "simple", "simple_lookback": 30})

    support = service.detect_support_zone(_make_frame())

    assert support.support_zone_low is not None
    assert support.support_zone_high is not None
    assert support.support_zone_low <= support.support_zone_high
    assert support.distance_to_support_pct is not None
    assert support.nearest_support_zone is not None


def test_clustering_detects_coherent_support_and_resistance_zones() -> None:
    service = SupportDetectionService(
        {
            "method": "clustering",
            "pivot_order": 2,
            "use_pivot_highs": True,
            "min_cluster_samples": 2,
            "cluster_eps_pct": 0.03,
            "historical_window_years": 5,
            "max_returned_zones": 6,
        }
    )

    support = service.detect_support_zone(_make_frame())

    assert support.support_zones
    assert support.resistance_zones
    assert support.nearest_support_zone is not None
    assert support.nearest_resistance_zone is not None
    assert support.nearest_support_zone["center"] < _make_frame()["close"].iloc[-1]


def test_price_time_detects_hvn_proxy_zones() -> None:
    frame = _make_frame()
    service = SupportDetectionService(
        {
            "method": "price_time",
            "price_time_bins": 30,
            "hvn_threshold_pct": 0.6,
            "historical_window_years": 5,
            "max_returned_zones": 6,
        }
    )

    support = service.detect_support_zone(frame)

    assert support.support_zones or support.resistance_zones
    assert any(zone["time_score"] > 0 for zone in support.support_zones + support.resistance_zones)


def test_combined_method_prioritizes_strong_zone() -> None:
    frame = _make_frame()
    service = SupportDetectionService(
        {
            "method": "combined",
            "pivot_order": 2,
            "use_pivot_highs": True,
            "min_cluster_samples": 2,
            "cluster_eps_pct": 0.03,
            "price_time_bins": 30,
            "hvn_threshold_pct": 0.6,
            "combine_proximity_pct": 0.04,
            "historical_window_years": 5,
            "max_returned_zones": 6,
            "use_recency_weighting": True,
        }
    )

    support = service.detect_support_zone(frame)

    assert support.nearest_support_zone is not None
    assert (
        support.nearest_support_zone["combined_score"]
        >= support.nearest_support_zone["pivot_score"]
    )
    assert support.support_zone_low == support.nearest_support_zone["low"]
    assert support.support_zone_high == support.nearest_support_zone["high"]


def test_zone_rows_and_bands_render_without_errors() -> None:
    frame = _make_frame()
    service = SupportDetectionService(
        {
            "method": "combined",
            "pivot_order": 2,
            "use_pivot_highs": True,
            "min_cluster_samples": 2,
            "cluster_eps_pct": 0.03,
            "price_time_bins": 30,
            "hvn_threshold_pct": 0.6,
            "combine_proximity_pct": 0.04,
            "historical_window_years": 5,
            "max_returned_zones": 6,
        }
    )

    support = service.detect_support_zone(frame)
    rows = service.build_zone_table_rows(support, current_price=float(frame.iloc[-1]["close"]))
    bands = service.build_zone_bands(support)

    assert rows
    assert bands
    assert all("y0" in band and "y1" in band for band in bands)
