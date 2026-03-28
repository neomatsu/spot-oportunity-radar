from __future__ import annotations

import numpy as np
import pandas as pd

from core.enums import RiskLevel
from services.risk_service import RiskService


def _frame_from_returns(returns: np.ndarray) -> pd.DataFrame:
    prices = 100 * np.exp(np.cumsum(returns))
    return pd.DataFrame({"close": prices})


def test_crypto_produces_more_risk_than_etf_under_same_returns() -> None:
    rng = np.random.default_rng(7)
    returns = rng.normal(0.0004, 0.015, 260)
    frame = _frame_from_returns(returns)
    service = RiskService()

    crypto = service.assess_risk(frame, asset_type="crypto")
    etf = service.assess_risk(frame, asset_type="etf")

    assert crypto.risk_score > etf.risk_score


def test_more_concentration_implies_more_risk() -> None:
    returns = np.random.default_rng(5).normal(0.0003, 0.012, 260)
    frame = _frame_from_returns(returns)
    service = RiskService()

    low_concentration = service.assess_risk(
        frame,
        asset_type="stock",
        current_asset_weight=0.03,
        current_sector_weight=0.08,
        current_asset_type_weight=0.18,
    )
    high_concentration = service.assess_risk(
        frame,
        asset_type="stock",
        current_asset_weight=0.14,
        current_sector_weight=0.36,
        current_asset_type_weight=0.42,
    )

    assert high_concentration.risk_score > low_concentration.risk_score
    assert high_concentration.rationale["breakdown"]["concentration_component"] > 0


def test_insufficient_data_uses_base_risk() -> None:
    frame = pd.DataFrame({"close": [100, 101, 99, 102]})
    assessment = RiskService().assess_risk(frame, asset_type="etf")

    assert assessment.risk_level == RiskLevel.LOW
    assert assessment.rationale["reason"] == "insufficient_price_history"
