from __future__ import annotations

import pandas as pd

from services.bitcoin_opportunity_alerts_service import BitcoinOpportunityAlertsService


def _config() -> dict:
    return {
        "buy_thresholds": [70, 75, 80],
        "buy_capital_pcts": [0.10, 0.40, 0.40],
        "sell_thresholds": [15, 20, 25],
        "sell_position_pcts": [0.40, 0.10, 0.10],
        "buy_reset_threshold": 60,
        "sell_reset_threshold": 40,
    }


def _history(scores: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.date_range("2026-08-01", periods=len(scores), freq="D"),
            "overall_score": scores,
        }
    )


def test_buy_alert_combines_crossed_threshold_percentages() -> None:
    signal = BitcoinOpportunityAlertsService(_config()).detect_latest_signal(
        _history([50, 82])
    )

    assert signal is not None
    assert signal.event_type == "bitcoin_opportunity_buy"
    assert signal.thresholds == (70.0, 75.0, 80.0)
    assert signal.recommended_pct == 90.0


def test_sell_alert_combines_crossed_threshold_percentages() -> None:
    signal = BitcoinOpportunityAlertsService(_config()).detect_latest_signal(
        _history([50, 30, 14])
    )

    assert signal is not None
    assert signal.event_type == "bitcoin_opportunity_sell"
    assert signal.thresholds == (15.0, 20.0, 25.0)
    assert signal.recommended_pct == 60.0


def test_buy_threshold_does_not_repeat_without_rearming() -> None:
    signal = BitcoinOpportunityAlertsService(_config()).detect_latest_signal(
        _history([65, 71, 69, 71])
    )

    assert signal is None


def test_buy_threshold_rearms_below_configured_score() -> None:
    signal = BitcoinOpportunityAlertsService(_config()).detect_latest_signal(
        _history([65, 71, 59, 71])
    )

    assert signal is not None
    assert signal.thresholds == (70.0,)
    assert signal.recommended_pct == 10.0
