from __future__ import annotations

from datetime import UTC, date, datetime

from data.database import (
    AssetDataStatusORM,
    AssetORM,
    PortfolioPositionORM,
    PriceBarDailyORM,
    SignalORM,
    TechnicalSnapshotORM,
)
from data.repositories.alerts_repo import AlertsRepository
from data.repositories.trade_intents_repo import TradeIntentsRepository
from services.alert_service import AlertService


def _seed_actionable_asset(
    db_session,
    *,
    symbol: str = "ETF1",
    score: float = 72.0,
    risk: float = 35.0,
):
    asset = AssetORM(
        symbol=symbol,
        name=f"{symbol} Fund",
        asset_type="etf",
        sector="Broad Market",
        region="US",
        enabled=True,
        supports_fundamentals=False,
    )
    db_session.add(asset)
    db_session.flush()
    today = date.today()
    db_session.add(
        PriceBarDailyORM(
            asset_id=asset.id,
            date=today,
            open=100,
            high=103,
            low=99,
            close=101,
            volume=1000,
        )
    )
    db_session.add(
        TechnicalSnapshotORM(
            asset_id=asset.id,
            date=today,
            rsi14=45,
            sma50=100,
            sma200=98,
            ema20=100,
            atr14=2,
            support_low=98,
            support_high=101,
            distance_to_support_pct=1.0,
            technical_score=70,
            rationale_json={"breakdown": {"rsi": 20}},
        )
    )
    db_session.add(
        SignalORM(
            asset_id=asset.id,
            date=today,
            final_score=score,
            recommendation="BUY_CANDIDATE",
            suggested_buy_low=99,
            suggested_buy_high=102,
            suggested_weight_add=4,
            risk_score=risk,
            rationale_json={
                "risk_level": "medium",
                "portfolio_fit_score": 65,
                "score_breakdown": {"final": score},
                "reasons": ["Precio en buy zone"],
            },
        )
    )
    db_session.add(
        AssetDataStatusORM(
            asset_id=asset.id,
            last_available_bar_date=today,
            last_refresh_attempt_at=datetime.now(UTC),
            last_successful_refresh_at=datetime.now(UTC),
            last_refresh_status="refreshed",
            last_refresh_source="alphavantage",
            data_mode="real",
            freshness_status="fresh",
            last_error_message=None,
        )
    )
    db_session.flush()
    return asset


def test_generates_entry_alert_and_trade_intent(db_session) -> None:
    _seed_actionable_asset(db_session)
    summary = AlertService(db_session).scan_market_events()

    alerts = AlertsRepository(db_session).list_recent()
    intents = TradeIntentsRepository(db_session).list_recent()

    assert summary.alerts_created >= 1
    assert len(alerts) >= 1
    assert alerts[0].alert_type == "entry_signal"
    assert len(intents) >= 1
    assert intents[0].status == "new"


def test_does_not_duplicate_alert_inside_cooldown(db_session) -> None:
    _seed_actionable_asset(db_session)
    service = AlertService(db_session)
    first = service.scan_market_events()
    second = service.scan_market_events()

    alerts = AlertsRepository(db_session).list_recent()
    assert first.alerts_created >= 1
    assert second.alerts_deduplicated >= 1
    assert len(alerts) == 1


def test_realerts_when_severity_changes_materially(db_session) -> None:
    asset = _seed_actionable_asset(db_session, score=72)
    service = AlertService(db_session)
    service.scan_market_events()

    latest_signal = db_session.query(SignalORM).filter(SignalORM.asset_id == asset.id).one()
    latest_signal.final_score = 82
    db_session.flush()

    summary = service.scan_market_events()
    alerts = AlertsRepository(db_session).list_recent()

    assert summary.alerts_created >= 1
    assert len(alerts) >= 2
    assert {alert.severity for alert in alerts}.issuperset({"warning", "high"})


def test_no_trade_intent_if_data_is_demo_or_stale(db_session) -> None:
    asset = _seed_actionable_asset(db_session, symbol="ETF2")
    status = (
        db_session.query(AssetDataStatusORM)
        .filter(AssetDataStatusORM.asset_id == asset.id)
        .one()
    )
    status.data_mode = "demo"
    status.freshness_status = "stale"
    db_session.flush()

    service = AlertService(db_session)
    service.scan_market_events()
    intents = TradeIntentsRepository(db_session).list_recent()

    assert intents == []


def test_no_trade_intent_if_portfolio_constraints_are_breached(db_session) -> None:
    asset = _seed_actionable_asset(db_session, symbol="ETF4")
    db_session.add(
        PortfolioPositionORM(
            asset_id=asset.id,
            quantity=10,
            avg_cost=100,
            current_weight=0.30,
            target_weight=0.20,
        )
    )
    db_session.flush()

    service = AlertService(db_session)
    service.scan_market_events()
    intents = TradeIntentsRepository(db_session).list_recent()

    assert intents == []


def test_trade_intent_status_changes(db_session) -> None:
    _seed_actionable_asset(db_session, symbol="ETF3")
    AlertService(db_session).scan_market_events()
    repo = TradeIntentsRepository(db_session)
    intent = repo.list_recent()[0]

    repo.update_status(intent.id, "reviewed")
    updated = repo.list_recent()[0]

    assert updated.status == "reviewed"
    assert updated.reviewed_at is not None
