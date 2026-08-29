from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from data.database import (
    AlertORM,
    AssetDataStatusORM,
    AssetORM,
    PortfolioPositionORM,
    PriceBarDailyORM,
    SignalORM,
    TechnicalSnapshotORM,
)
from data.repositories.bitcoin_opportunity_repo import BitcoinOpportunityRepository
from data.repositories.portfolio_repo import PortfolioRepository
from data.repositories.sp500_opportunity_repo import SP500OpportunityRepository
from services.dashboard_service import DashboardService


def test_dashboard_handles_empty_database(db_session) -> None:
    snapshot = DashboardService(
        db_session,
        now=datetime(2026, 8, 19, tzinfo=UTC),
    ).build()

    assert snapshot.watched_assets == 0
    assert snapshot.opportunities == ()
    assert snapshot.bitcoin.score is None
    assert snapshot.sp500.score is None
    assert snapshot.portfolio.total_capital == 0


def test_dashboard_aggregates_persisted_state_without_refresh(db_session) -> None:
    now = datetime(2026, 8, 19, 12, tzinfo=UTC)
    today = now.date()
    asset = AssetORM(
        symbol="ETF1",
        name="Core ETF",
        asset_type="etf",
        sector="Broad Market",
        region="US",
        enabled=True,
        supports_fundamentals=False,
        quote_currency="EUR",
    )
    db_session.add(asset)
    db_session.flush()
    db_session.add_all(
        [
            PriceBarDailyORM(
                asset_id=asset.id,
                date=today,
                open=110,
                high=112,
                low=109,
                close=111,
                volume=1000,
            ),
            TechnicalSnapshotORM(
                asset_id=asset.id,
                date=today,
                technical_score=75,
            ),
            SignalORM(
                asset_id=asset.id,
                date=today,
                final_score=78,
                recommendation="BUY_CANDIDATE",
                suggested_weight_add=5,
                risk_score=30,
                rationale_json={"portfolio_fit_score": 80},
            ),
            AssetDataStatusORM(
                asset_id=asset.id,
                last_available_bar_date=today,
                data_mode="real",
                freshness_status="fresh",
            ),
            PortfolioPositionORM(
                asset_id=asset.id,
                quantity=10,
                avg_cost=100,
                current_weight=0.20,
                target_weight=0.20,
            ),
        ]
    )
    PortfolioRepository(db_session).set_total_capital(10_000)
    db_session.add(
        AlertORM(
            asset_id=asset.id,
            symbol=asset.symbol,
            alert_type="take_profit",
            severity="high",
            title="Revisar beneficios",
            message="Valorar reducción",
            payload_json={
                "alert_group": "position_management",
                "action_suggestion": "Vender parcialmente",
            },
            status="sent",
            dedupe_key="ETF1:take_profit",
            created_at=now - timedelta(days=1),
        )
    )

    BitcoinOpportunityRepository(db_session).upsert_many(
        [
            {
                "date": today - timedelta(days=7),
                "overall_score": 60,
                "classification": "OPORTUNIDAD",
                "available_components": 6,
                "bitcoin_price": 60_000,
            },
            {
                "date": today,
                "overall_score": 68,
                "classification": "BUENA_OPORTUNIDAD",
                "available_components": 6,
                "bitcoin_price": 64_000,
            },
        ],
        source_version="bitcoin_opportunity_v1",
    )
    SP500OpportunityRepository(db_session).upsert_many(
        [
            {
                "date": today,
                "overall_score": 48,
                "classification": "NEUTRAL",
                "available_components": 6,
                "sp500_price": 7_700,
            }
        ],
        source_version="sp500_opportunity_v5_rolling_calibration",
    )
    db_session.flush()

    snapshot = DashboardService(db_session, now=now).build()

    assert snapshot.buy_candidates == 1
    assert snapshot.opportunities[0]["symbol"] == "ETF1"
    assert snapshot.bitcoin.score == 68
    assert snapshot.bitcoin.score_change == 8
    assert snapshot.sp500.score == 48
    assert snapshot.portfolio.invested_cost == pytest.approx(1_000)
    assert snapshot.portfolio.market_value == pytest.approx(2_000)
    assert snapshot.portfolio.pnl == pytest.approx(1_000)
    assert snapshot.attention_items[0]["action"] == "Vender parcialmente"
