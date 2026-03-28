from __future__ import annotations

from datetime import date

from core.config import load_yaml_config
from core.enums import RecommendationStatus
from core.models import RecommendationModel, RiskAssessmentModel, TechnicalSnapshotModel


class RecommendationService:
    def __init__(self, config: dict | None = None, portfolio_rules: dict | None = None) -> None:
        self.config = config or load_yaml_config("scoring.yaml")
        self.portfolio_rules = portfolio_rules or load_yaml_config("portfolio_rules.yaml")

    def build_recommendation(
        self,
        *,
        asset_id: int,
        technical_snapshot: TechnicalSnapshotModel,
        risk: RiskAssessmentModel,
        final_score: float,
        portfolio_fit_score: float,
        score_breakdown: dict[str, float] | None = None,
    ) -> RecommendationModel:
        thresholds = self.config["recommendation_thresholds"]
        recommendation = self._classify(
            final_score=final_score,
            risk_score=risk.risk_score,
            distance_to_support_pct=technical_snapshot.distance_to_support_pct,
            portfolio_fit_score=portfolio_fit_score,
            thresholds=thresholds,
        )
        suggested_weight_add = self._position_size(
            recommendation=recommendation.value,
            final_score=final_score,
            portfolio_fit_score=portfolio_fit_score,
            risk_score=risk.risk_score,
            risk_level=risk.risk_level.value,
        )
        buy_low, buy_high = self._buy_range(technical_snapshot)
        invalidation = self._build_invalidation(technical_snapshot)

        reasons = self._build_reasons(
            recommendation=recommendation.value,
            final_score=final_score,
            risk_score=risk.risk_score,
            portfolio_fit_score=portfolio_fit_score,
            distance_to_support_pct=technical_snapshot.distance_to_support_pct,
            technical_reasons=technical_snapshot.rationale.get("reasons", []),
            risk_reasons=risk.rationale.get("reasons", []),
        )
        rationale = {
            "score_breakdown": score_breakdown or {},
            "technical_score": technical_snapshot.technical_score,
            "risk_score": risk.risk_score,
            "risk_level": risk.risk_level.value,
            "portfolio_fit_score": portfolio_fit_score,
            "support_zone": [technical_snapshot.support_low, technical_snapshot.support_high],
            "reasons": reasons,
            "invalidation": invalidation,
        }
        return RecommendationModel(
            asset_id=asset_id,
            date=date.today(),
            final_score=final_score,
            recommendation=recommendation,
            suggested_buy_low=buy_low,
            suggested_buy_high=buy_high,
            suggested_weight_add=suggested_weight_add,
            risk_score=risk.risk_score,
            risk_level=risk.risk_level,
            invalidation=invalidation,
            rationale_json=rationale,
        )

    @staticmethod
    def _classify(
        *,
        final_score: float,
        risk_score: float,
        distance_to_support_pct: float | None,
        portfolio_fit_score: float,
        thresholds: dict[str, float],
    ) -> RecommendationStatus:
        close_to_support = distance_to_support_pct is not None and abs(distance_to_support_pct) <= 5
        if (
            final_score >= thresholds["buy_candidate_min"]
            and risk_score <= thresholds["max_buy_candidate_risk_score"]
            and close_to_support
            and portfolio_fit_score >= 45
        ):
            return RecommendationStatus.BUY_CANDIDATE
        if (
            final_score >= thresholds["watch_min"]
            and risk_score <= thresholds["max_watch_risk_score"]
        ):
            return RecommendationStatus.WATCH
        return RecommendationStatus.AVOID

    def _position_size(
        self,
        *,
        recommendation: str,
        final_score: float,
        portfolio_fit_score: float,
        risk_score: float,
        risk_level: str,
    ) -> float:
        if recommendation == RecommendationStatus.AVOID.value:
            return 0.0

        base_add = float(self.portfolio_rules["base_add_weights"][risk_level]) * 100
        score_multiplier = min(1.3, max(0.55, final_score / 75))
        fit_multiplier = min(1.15, max(0.6, portfolio_fit_score / 70))
        risk_penalty = max(0.35, 1 - (risk_score / 160))

        suggested_weight_add = base_add * score_multiplier * fit_multiplier * risk_penalty
        return round(suggested_weight_add, 2)

    @staticmethod
    def _buy_range(technical_snapshot: TechnicalSnapshotModel) -> tuple[float | None, float | None]:
        if (
            technical_snapshot.support_low is not None
            and technical_snapshot.support_high is not None
        ):
            return technical_snapshot.support_low, technical_snapshot.support_high
        return technical_snapshot.support_high, technical_snapshot.support_high

    @staticmethod
    def _build_invalidation(technical_snapshot: TechnicalSnapshotModel) -> str:
        if technical_snapshot.support_low is not None:
            return (
                f"Perdida clara de la zona de soporte por debajo de "
                f"{technical_snapshot.support_low:.2f} con volatilidad creciente"
            )
        return "El setup pierde validez si el precio deteriora estructura y momentum"

    @staticmethod
    def _build_reasons(
        *,
        recommendation: str,
        final_score: float,
        risk_score: float,
        portfolio_fit_score: float,
        distance_to_support_pct: float | None,
        technical_reasons: list[str],
        risk_reasons: list[str],
    ) -> list[str]:
        reasons: list[str] = []
        if recommendation == RecommendationStatus.BUY_CANDIDATE.value:
            reasons.append("Score total alto con riesgo razonable")
        elif recommendation == RecommendationStatus.WATCH.value:
            reasons.append("Setup interesante pero aun incompleto")
        else:
            reasons.append("La relacion riesgo-beneficio actual no compensa")

        if distance_to_support_pct is not None and abs(distance_to_support_pct) <= 5:
            reasons.append("Precio cerca de zona de soporte")
        elif distance_to_support_pct is not None:
            reasons.append("Precio lejos de la zona de compra ideal")

        if portfolio_fit_score >= 70:
            reasons.append("Buen encaje con el equilibrio actual de cartera")
        elif portfolio_fit_score <= 40:
            reasons.append("La cartera esta algo saturada para este activo")

        reasons.extend(technical_reasons[:3])
        reasons.extend(risk_reasons[:3])
        reasons.append(f"Final score {final_score:.1f} con risk score {risk_score:.1f}")
        return reasons
