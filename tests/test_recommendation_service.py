from __future__ import annotations

from datetime import date

from core.enums import RecommendationStatus, RiskLevel
from core.models import RiskAssessmentModel, TechnicalSnapshotModel
from services.recommendation_service import RecommendationService


def test_high_score_case_produces_buy_candidate() -> None:
    service = RecommendationService()
    technical = TechnicalSnapshotModel(
        asset_id=1,
        date=date(2026, 3, 26),
        support_low=95,
        support_high=100,
        distance_to_support_pct=1.5,
        technical_score=82,
        rationale={"reasons": ["RSI en zona de sobreventa", "Precio muy cerca de soporte"]},
    )
    risk = RiskAssessmentModel(
        risk_score=28,
        risk_level=RiskLevel.LOW,
        rationale={"reasons": ["Volatilidad contenida"]},
    )

    result = service.build_recommendation(
        asset_id=1,
        technical_snapshot=technical,
        risk=risk,
        final_score=78,
        portfolio_fit_score=76,
        score_breakdown={"technical": 82, "risk": 28, "portfolio_fit": 76, "final": 78},
    )

    assert result.recommendation == RecommendationStatus.BUY_CANDIDATE
    assert result.suggested_buy_low == 95
    assert result.risk_level == RiskLevel.LOW
    assert result.suggested_weight_add >= 4


def test_low_score_case_produces_avoid() -> None:
    service = RecommendationService()
    technical = TechnicalSnapshotModel(
        asset_id=1,
        date=date(2026, 3, 26),
        support_low=80,
        support_high=84,
        distance_to_support_pct=16.0,
        technical_score=22,
        rationale={"reasons": ["Precio demasiado lejos del soporte"]},
    )
    risk = RiskAssessmentModel(
        risk_score=82,
        risk_level=RiskLevel.HIGH,
        rationale={"reasons": ["Volatilidad reciente elevada"]},
    )

    result = service.build_recommendation(
        asset_id=1,
        technical_snapshot=technical,
        risk=risk,
        final_score=24,
        portfolio_fit_score=20,
        score_breakdown={"technical": 22, "risk": 82, "portfolio_fit": 20, "final": 24},
    )

    assert result.recommendation == RecommendationStatus.AVOID
    assert result.invalidation != ""
    assert result.suggested_weight_add == 0.0


def test_medium_case_produces_watch() -> None:
    service = RecommendationService()
    technical = TechnicalSnapshotModel(
        asset_id=1,
        date=date(2026, 3, 26),
        support_low=100,
        support_high=105,
        distance_to_support_pct=7.0,
        technical_score=58,
        rationale={"reasons": ["Setup interesante pero sin timing perfecto"]},
    )
    risk = RiskAssessmentModel(
        risk_score=49,
        risk_level=RiskLevel.MEDIUM,
        rationale={"reasons": ["Volatilidad moderada"]},
    )

    result = service.build_recommendation(
        asset_id=1,
        technical_snapshot=technical,
        risk=risk,
        final_score=58,
        portfolio_fit_score=61,
        score_breakdown={"technical": 58, "risk": 49, "portfolio_fit": 61, "final": 58},
    )

    assert result.recommendation == RecommendationStatus.WATCH
