from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from data.database import (
    AssetDataStatusORM,
    AssetORM,
    PortfolioPositionORM,
    PriceBarDailyORM,
    SignalORM,
    TechnicalSnapshotORM,
)
from data.repositories.alerts_repo import AlertsRepository
from data.repositories.bitcoin_opportunity_repo import BitcoinOpportunityRepository
from data.repositories.sp500_opportunity_repo import SP500OpportunityRepository
from data.repositories.trade_intents_repo import TradeIntentsRepository
from services.alert_service import AlertService
from services.notification_service import NotificationResult
from services.planned_entry_service import PlannedEntryService
from services.rsi_cycle_alerts_service import RSICycleAlertSignal


def _seed_actionable_asset(
    db_session,
    *,
    symbol: str = "ETF1",
    score: float = 72.0,
    risk: float = 35.0,
    recommendation: str = "BUY_CANDIDATE",
    last_price: float = 101.0,
    rsi14: float = 45.0,
    sma50: float = 100.0,
    sma200: float = 98.0,
    support_low: float = 98.0,
    support_high: float = 101.0,
    portfolio_fit_score: float = 65.0,
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
            close=last_price,
            volume=1000,
        )
    )
    db_session.add(
        TechnicalSnapshotORM(
            asset_id=asset.id,
            date=today,
            rsi14=rsi14,
            sma50=sma50,
            sma200=sma200,
            ema20=100,
            atr14=2,
            support_low=support_low,
            support_high=support_high,
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
            recommendation=recommendation,
            suggested_buy_low=99,
            suggested_buy_high=102,
            suggested_weight_add=4,
            risk_score=risk,
            rationale_json={
                "risk_level": "medium",
                "portfolio_fit_score": portfolio_fit_score,
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
    assert alerts[0].payload_json["is_portfolio_asset"] is False
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


def test_planned_entry_level_uses_existing_alert_pipeline(db_session) -> None:
    asset = _seed_actionable_asset(
        db_session,
        symbol="PLAN_ALERT",
        recommendation="WATCH",
        score=50,
        last_price=100.5,
    )
    PlannedEntryService(db_session).create_level(
        asset=asset,
        target_price=100,
        suggested_weight_pct=7.5,
        tolerance_pct=1,
        rearm_distance_pct=3,
    )

    service = AlertService(db_session)
    first = service.scan_market_events()
    second = service.scan_market_events()
    alerts = AlertsRepository(db_session).list_recent()
    planned = [alert for alert in alerts if alert.alert_type == "manual_buy_level_near"]

    assert first.alerts_created == 1
    assert second.events_detected == 0
    assert len(planned) == 1
    assert planned[0].payload_json["alert_group"] == "planned_entry"
    assert planned[0].payload_json["recommended_trade_pct"] == 7.5


def test_pending_alert_rechecks_portfolio_membership_before_delivery(db_session) -> None:
    asset = _seed_actionable_asset(
        db_session,
        symbol="ETF_PORTFOLIO",
        recommendation="WATCH",
    )
    db_session.add(
        PortfolioPositionORM(
            asset_id=asset.id,
            quantity=2,
            avg_cost=100,
            current_weight=0.05,
            target_weight=0.05,
        )
    )
    db_session.flush()
    service = AlertService(db_session)
    alert = service.alerts_repo.create_alert(
        {
            "asset_id": asset.id,
            "symbol": asset.symbol,
            "alert_type": "risk_deterioration",
            "severity": "warning",
            "title": "Riesgo",
            "message": "Cambio de riesgo",
            "payload_json": {"is_portfolio_asset": False},
            "status": "new",
            "delivery_channels": ["ui", "telegram"],
            "dedupe_key": f"{asset.symbol}:risk_deterioration",
        }
    )
    delivered: list[bool] = []
    service.notification_service.send_alert = lambda pending: (
        delivered.append(pending.payload_json["is_portfolio_asset"])
        or [NotificationResult(channel="console", status="sent")]
    )

    summary = service.send_pending_alerts()

    assert summary.alerts_sent == 1
    assert delivered == [True]
    assert alert.payload_json["is_portfolio_asset"] is True


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


def test_sell_alerts_are_only_generated_for_assets_in_portfolio(db_session) -> None:
    _seed_actionable_asset(
        db_session,
        symbol="ETF5",
        recommendation="WATCH",
        rsi14=82,
        last_price=135,
        sma50=100,
    )
    summary = AlertService(db_session).scan_market_events()
    alerts = AlertsRepository(db_session).list_recent()

    assert summary.alerts_created == 0
    assert alerts == []


def test_trim_position_alert_triggers_with_excess_weight(db_session) -> None:
    asset = _seed_actionable_asset(
        db_session,
        symbol="ETF6",
        recommendation="WATCH",
        score=58,
        risk=48,
        last_price=118,
    )
    db_session.add(
        PortfolioPositionORM(
            asset_id=asset.id,
            quantity=10,
            avg_cost=100,
            current_weight=0.18,
            target_weight=0.10,
        )
    )
    db_session.flush()

    AlertService(db_session).scan_market_events()
    alerts = AlertsRepository(db_session).list_recent()

    trim_alert = next(alert for alert in alerts if alert.alert_type == "trim_position")
    assert trim_alert.payload_json["is_portfolio_asset"] is True


def test_take_profit_alert_triggers_with_profit_and_extension(db_session) -> None:
    asset = _seed_actionable_asset(
        db_session,
        symbol="ETF7",
        recommendation="WATCH",
        score=55,
        risk=40,
        last_price=130,
        rsi14=74,
        sma50=110,
    )
    db_session.add(
        PortfolioPositionORM(
            asset_id=asset.id,
            quantity=10,
            avg_cost=100,
            current_weight=0.08,
            target_weight=0.08,
        )
    )
    db_session.flush()

    AlertService(db_session).scan_market_events()
    alerts = AlertsRepository(db_session).list_recent()

    assert any(alert.alert_type == "take_profit" for alert in alerts)


def test_exit_candidate_triggers_with_clear_deterioration(db_session) -> None:
    asset = _seed_actionable_asset(
        db_session,
        symbol="ETF8",
        recommendation="AVOID",
        score=25,
        risk=84,
        last_price=94,
        rsi14=38,
        sma50=105,
        support_low=98,
        support_high=100,
    )
    db_session.add(
        PortfolioPositionORM(
            asset_id=asset.id,
            quantity=10,
            avg_cost=108,
            current_weight=0.09,
            target_weight=0.08,
        )
    )
    db_session.flush()

    AlertService(db_session).scan_market_events()
    alerts = AlertsRepository(db_session).list_recent()

    assert any(alert.alert_type == "exit_candidate" for alert in alerts)


def test_sell_alerts_are_deduplicated(db_session) -> None:
    asset = _seed_actionable_asset(
        db_session,
        symbol="ETF9",
        recommendation="WATCH",
        score=57,
        risk=42,
        last_price=132,
        rsi14=78,
        sma50=112,
    )
    db_session.add(
        PortfolioPositionORM(
            asset_id=asset.id,
            quantity=5,
            avg_cost=100,
            current_weight=0.09,
            target_weight=0.07,
        )
    )
    db_session.flush()

    service = AlertService(db_session)
    first = service.scan_market_events()
    second = service.scan_market_events()
    alerts = AlertsRepository(db_session).list_recent()

    assert first.alerts_created >= 1
    assert second.alerts_deduplicated >= 1
    assert len([alert for alert in alerts if alert.alert_type == "take_profit"]) == 1


def test_rsi_cycle_alert_is_generated_for_latest_signal(db_session, monkeypatch) -> None:
    _seed_actionable_asset(
        db_session,
        symbol="BTCUSDT",
        recommendation="WATCH",
        score=55,
        risk=42,
        last_price=65000,
        rsi14=24,
        sma50=70000,
        portfolio_fit_score=55,
    )
    service = AlertService(db_session)

    monkeypatch.setattr(
        service.rsi_cycle_alerts_service,
        "detect_latest_signals",
        lambda frame: [
            RSICycleAlertSignal(
                event_type="buy_rsi_25",
                signal_date=date.today(),
                rsi14=24.1,
                price=65000.0,
                severity="high",
                title="Compra RSI<=25",
                message="RSI en sobreventa profunda de primer nivel.",
            )
        ],
    )

    summary = service.scan_market_events()
    alerts = AlertsRepository(db_session).list_recent()

    assert summary.alerts_created >= 1
    assert any(alert.alert_type == "buy_rsi_25" for alert in alerts)


def test_rsi_cycle_alerts_are_deduplicated(db_session, monkeypatch) -> None:
    _seed_actionable_asset(
        db_session,
        symbol="ETHUSDT",
        recommendation="WATCH",
        score=52,
        risk=45,
        last_price=3200,
        rsi14=78,
        sma50=2900,
    )
    service = AlertService(db_session)

    monkeypatch.setattr(
        service.rsi_cycle_alerts_service,
        "detect_latest_signals",
        lambda frame: [
            RSICycleAlertSignal(
                event_type="sell_rsi_80",
                signal_date=date.today(),
                rsi14=81.2,
                price=3200.0,
                severity="high",
                title="Venta RSI>=80",
                message="RSI en sobrecompra extrema de segundo nivel.",
            )
        ],
    )

    first = service.scan_market_events()
    second = service.scan_market_events()
    alerts = AlertsRepository(db_session).list_recent()

    assert first.alerts_created >= 1
    assert second.alerts_deduplicated >= 1
    assert len([alert for alert in alerts if alert.alert_type == "sell_rsi_80"]) == 1


def test_bitcoin_opportunity_crossing_creates_actionable_alert(db_session) -> None:
    _seed_actionable_asset(
        db_session,
        symbol="BTCUSDT",
        recommendation="WATCH",
        score=55,
        risk=42,
        last_price=65_000,
    )
    today = date.today()
    BitcoinOpportunityRepository(db_session).upsert_many(
        [
            {
                "date": today - timedelta(days=1),
                "overall_score": 65.0,
                "classification": "BUENA_OPORTUNIDAD",
                "available_components": 6,
                "bitcoin_price": 64_000,
            },
            {
                "date": today,
                "overall_score": 71.0,
                "classification": "BUENA_OPORTUNIDAD",
                "available_components": 6,
                "bitcoin_price": 65_000,
            },
        ],
        source_version="bitcoin_opportunity_v1",
    )

    summary = AlertService(db_session).scan_market_events()
    alerts = AlertsRepository(db_session).list_recent()
    alert = next(
        item for item in alerts if item.alert_type == "bitcoin_opportunity_buy"
    )

    assert summary.alerts_created >= 1
    assert alert.payload_json["crossed_thresholds"] == [70.0]
    assert alert.payload_json["recommended_trade_pct"] == 10.0


def test_sp500_opportunity_crossing_creates_global_alert(db_session) -> None:
    today = date.today()
    SP500OpportunityRepository(db_session).upsert_many(
        [
            {
                "date": today - timedelta(days=1),
                "overall_score": 59.0,
                "classification": "NEUTRAL",
                "available_components": 6,
                "sp500_price": 7_600,
            },
            {
                "date": today,
                "overall_score": 63.0,
                "classification": "OPORTUNIDAD",
                "available_components": 6,
                "sp500_price": 7_650,
            },
        ],
        source_version="sp500_opportunity_v5_rolling_calibration",
    )

    summary = AlertService(db_session).scan_market_events()
    alerts = AlertsRepository(db_session).list_recent()
    alert = next(item for item in alerts if item.alert_type == "sp500_opportunity_buy")

    assert summary.alerts_created >= 1
    assert alert.asset_id is None
    assert alert.symbol == "^GSPC"
    assert alert.payload_json["crossed_thresholds"] == [60.0, 62.5]
    assert alert.payload_json["recommended_trade_pct"] == 100.0
