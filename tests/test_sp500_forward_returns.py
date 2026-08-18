from __future__ import annotations

import numpy as np
import pandas as pd

from analytics.sp500_forward_returns import SP500ForwardReturnsAnalyzer


def test_forward_returns_produces_band_statistics_and_mae() -> None:
    rows = 500
    history = pd.DataFrame(
        {
            "date": pd.date_range("2020-01-01", periods=rows, freq="B"),
            "overall_score": np.tile([10, 30, 50, 65, 75, 85, 95], rows // 7 + 1)[:rows],
            "sp500_price": 100 * np.cumprod(np.full(rows, 1.0005)),
        }
    )
    result = SP500ForwardReturnsAnalyzer().analyze(
        history,
        horizons={"3m": 63, "6m": 126},
        score_bands=[0, 20, 40, 60, 70, 80, 90, 100.0001],
    )

    assert set(result.summary["horizon"]) == {"3m", "6m"}
    assert {
        "mean_return_pct",
        "median_return_pct",
        "positive_probability_pct",
        "mean_mae_pct",
    }.issubset(result.summary.columns)
    assert len(result.monotonicity) == 2
    assert "return_3m_pct" in result.observations
    assert "mae_6m_pct" in result.observations
