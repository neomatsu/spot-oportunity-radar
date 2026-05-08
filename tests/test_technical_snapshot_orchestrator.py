from __future__ import annotations

from datetime import date, timedelta

from data.database import AssetORM, PriceBarDailyORM
from services.recommendation_service import RecommendationService
from services.risk_service import RiskService
from services.scoring_service import ScoringService
from services.technical_snapshot_orchestrator import TechnicalSnapshotOrchestrator


def test_technical_snapshot_keeps_legacy_support_fields_and_adds_zone_payload(db_session) -> None:
    asset = AssetORM(
        symbol="ZONE",
        name="Zone Asset",
        asset_type="etf",
        sector="Broad Market",
        region="US",
        enabled=True,
        supports_fundamentals=False,
    )
    db_session.add(asset)
    db_session.flush()

    start = date(2021, 1, 1)
    for offset in range(320):
        current_date = start + timedelta(days=offset)
        base = 100 + (offset * 0.1)
        if offset % 40 in {10, 11, 12}:
            base -= 6
        if offset % 55 in {15, 16}:
            base += 5
        db_session.add(
            PriceBarDailyORM(
                asset_id=asset.id,
                date=current_date,
                open=base,
                high=base + 1.5,
                low=base - 1.5,
                close=base + 0.3,
                volume=1000 + offset,
                provider="test",
                is_adjusted=False,
            )
        )
    db_session.flush()

    orchestrator = TechnicalSnapshotOrchestrator(db_session)
    snapshot = orchestrator.refresh_for_asset(asset)

    assert snapshot is not None
    assert snapshot.support_low is not None
    assert snapshot.support_high is not None
    assert snapshot.distance_to_support_pct is not None
    assert "support_zones" in snapshot.rationale
    assert "nearest_support_zone" in snapshot.rationale

    scoring = ScoringService()
    risk = RiskService().assess_risk(
        orchestrator.market_data.load_price_frame(asset.id),
        asset_type=asset.asset_type,
        current_asset_weight=0.0,
        current_sector_weight=0.0,
        current_asset_type_weight=0.0,
    )
    final_score_details = scoring.compute_final_score_details(
        technical_score=float(snapshot.technical_score or 0.0),
        risk_score=float(risk.risk_score),
        portfolio_fit_score=93.0,
    )
    recommendation = RecommendationService().build_recommendation(
        asset_id=asset.id,
        technical_snapshot=snapshot,
        risk=risk,
        final_score=final_score_details["final"],
        portfolio_fit_score=93.0,
        score_breakdown=final_score_details,
    )

    assert recommendation.suggested_buy_low == snapshot.support_low
    assert recommendation.suggested_buy_high == snapshot.support_high
    assert recommendation.invalidation != ""
