from __future__ import annotations

import pandas as pd

from services.support_detection_service import SupportDetectionService


def test_detect_support_zone_returns_valid_band() -> None:
    frame = pd.DataFrame(
        {
            "date": pd.date_range("2024-01-01", periods=40, freq="D"),
            "open": [100, 99, 98, 97, 98, 99, 100, 99, 98, 97] * 4,
            "high": [101, 100, 99, 98, 99, 100, 101, 100, 99, 98] * 4,
            "low": [99, 98, 97, 95, 97, 98, 99, 98, 97, 95] * 4,
            "close": [100, 99, 98, 96, 98, 99, 100, 99, 98, 96] * 4,
            "volume": [1_000_000] * 40,
        }
    )

    support = SupportDetectionService.detect_support_zone(frame)

    assert support.support_zone_low is not None
    assert support.support_zone_high is not None
    assert support.support_zone_low <= support.support_zone_high
    assert support.distance_to_support_pct is not None
