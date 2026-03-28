from __future__ import annotations

from sqlalchemy.orm import Session

from data.database import AssetORM
from data.repositories.signals_repo import SignalsRepository
from services.portfolio_service import PortfolioService
from services.rebalance_service import RebalanceService
from services.recommendation_service import RecommendationService
from services.risk_service import RiskService
from services.scoring_service import ScoringService
from services.technical_snapshot_orchestrator import TechnicalSnapshotOrchestrator


class SignalPipelineService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.signals_repo = SignalsRepository(session)
        self.portfolio_service = PortfolioService(session)
        self.rebalance_service = RebalanceService()
        self.recommendation_service = RecommendationService()
        self.risk_service = RiskService()
        self.scoring_service = ScoringService()
        self.technical_orchestrator = TechnicalSnapshotOrchestrator(session)

    def generate_for_asset(self, asset: AssetORM) -> dict | None:
        technical_snapshot = self.technical_orchestrator.refresh_for_asset(asset)
        if technical_snapshot is None:
            return None

        frame = self.technical_orchestrator.market_data.load_price_frame(asset.id, min_rows=30)
        exposure = self.portfolio_service.get_exposures()
        portfolio_fit_score, portfolio_rationale = self.rebalance_service.portfolio_fit_score(
            asset,
            exposure,
        )
        risk = self.risk_service.assess_risk(
            frame,
            asset_type=asset.asset_type,
            current_asset_weight=exposure.by_asset.get(asset.symbol, 0.0),
            current_sector_weight=exposure.by_sector.get(asset.sector, 0.0),
            current_asset_type_weight=exposure.by_asset_type.get(asset.asset_type, 0.0),
        )

        score_breakdown = self.scoring_service.compute_final_score_details(
            technical_score=technical_snapshot.technical_score or 0.0,
            risk_score=risk.risk_score,
            portfolio_fit_score=portfolio_fit_score,
        )
        final_score = score_breakdown["final"]
        recommendation = self.recommendation_service.build_recommendation(
            asset_id=asset.id,
            technical_snapshot=technical_snapshot,
            risk=risk,
            final_score=final_score,
            portfolio_fit_score=portfolio_fit_score,
            score_breakdown=score_breakdown,
        )
        payload = {
            "asset_id": recommendation.asset_id,
            "date": recommendation.date,
            "final_score": recommendation.final_score,
            "recommendation": recommendation.recommendation.value,
            "suggested_buy_low": recommendation.suggested_buy_low,
            "suggested_buy_high": recommendation.suggested_buy_high,
            "suggested_weight_add": recommendation.suggested_weight_add,
            "risk_score": recommendation.risk_score,
        }
        payload["rationale_json"] = {
            **recommendation.rationale_json,
            "portfolio_rationale": portfolio_rationale,
            "technical_rationale": technical_snapshot.rationale,
            "risk_rationale": risk.rationale,
            "score_breakdown": score_breakdown,
            "risk_level": recommendation.risk_level.value,
            "invalidation": recommendation.invalidation,
        }
        self.signals_repo.upsert_signal(payload)
        return {
            "asset_id": asset.id,
            "symbol": asset.symbol,
            "technical_score": technical_snapshot.technical_score,
            "risk_score": risk.risk_score,
            "portfolio_fit_score": portfolio_fit_score,
            "final_opportunity_score": final_score,
            "recommendation": recommendation.recommendation.value,
        }
