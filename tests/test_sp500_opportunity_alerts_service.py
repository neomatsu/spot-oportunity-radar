from __future__ import annotations

import pandas as pd

from services.sp500_opportunity_alerts_service import SP500OpportunityAlertsService


def _config() -> dict:
    return {
        "buy_thresholds": [60, 62.5, 77.5],
        "buy_capital_pcts": [0.50, 0.50, 0.50],
        "sell_thresholds": [25],
        "sell_position_pcts": [0.50],
        "buy_reset_threshold": 55,
        "sell_reset_threshold": 55,
    }


def _history(scores: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.date_range("2026-08-01", periods=len(scores), freq="D"),
            "overall_score": scores,
        }
    )


def test_sp500_buy_alert_combines_crossed_thresholds() -> None:
    signal = SP500OpportunityAlertsService(_config()).detect_latest_signal(
        _history([50, 63])
    )

    assert signal is not None
    assert signal.event_type == "sp500_opportunity_buy"
    assert signal.thresholds == (60.0, 62.5)
    assert signal.recommended_pct == 100.0


def test_sp500_sell_alert_only_uses_extreme_threshold() -> None:
    signal = SP500OpportunityAlertsService(_config()).detect_latest_signal(
        _history([60, 24])
    )

    assert signal is not None
    assert signal.event_type == "sp500_opportunity_sell"
    assert signal.thresholds == (25.0,)
    assert signal.recommended_pct == 50.0


def test_sp500_buy_threshold_requires_rearming() -> None:
    signal = SP500OpportunityAlertsService(_config()).detect_latest_signal(
        _history([58, 61, 59, 61])
    )

    assert signal is None
