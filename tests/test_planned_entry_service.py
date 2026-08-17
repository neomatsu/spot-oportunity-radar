from __future__ import annotations

from datetime import date, timedelta

from data.database import AssetORM
from services.planned_entry_service import PlannedEntryService


def _asset(db_session, symbol: str = "PLAN") -> AssetORM:
    asset = AssetORM(
        symbol=symbol,
        name="Planned asset",
        asset_type="stock",
        sector="Technology",
        region="US",
        enabled=True,
        supports_fundamentals=False,
        quote_currency="USD",
    )
    db_session.add(asset)
    db_session.flush()
    return asset


def test_level_alerts_once_and_rearms_after_price_moves_away(db_session) -> None:
    asset = _asset(db_session)
    service = PlannedEntryService(db_session)
    level = service.create_level(
        asset=asset,
        target_price=100,
        suggested_weight_pct=10,
        tolerance_pct=1,
        rearm_distance_pct=3,
    )

    first = service.evaluate_asset(asset=asset, current_price=100.8, observed_date=date.today())
    repeated = service.evaluate_asset(asset=asset, current_price=100.4, observed_date=date.today())
    rearm = service.evaluate_asset(asset=asset, current_price=103.1, observed_date=date.today())
    second_cycle = service.evaluate_asset(
        asset=asset, current_price=99.8, observed_date=date.today()
    )

    assert [trigger.event_type for trigger in first] == ["manual_buy_level_near"]
    assert repeated == []
    assert rearm == []
    assert [trigger.event_type for trigger in second_cycle] == [
        "manual_buy_level_crossed"
    ]
    assert level.status == "triggered"


def test_groups_multiple_levels_reached_on_same_scan(db_session) -> None:
    asset = _asset(db_session, "GROUP")
    service = PlannedEntryService(db_session)
    service.create_level(asset=asset, target_price=100, suggested_weight_pct=5)
    service.create_level(asset=asset, target_price=95, suggested_weight_pct=10)

    triggers = service.evaluate_asset(
        asset=asset, current_price=94, observed_date=date.today()
    )

    assert len(triggers) == 1
    assert triggers[0].event_type == "manual_buy_level_crossed"
    assert len(triggers[0].levels) == 2


def test_crossed_level_wins_over_near_level_on_same_scan(db_session) -> None:
    asset = _asset(db_session, "PRIORITY")
    service = PlannedEntryService(db_session)
    service.create_level(asset=asset, target_price=100, suggested_weight_pct=5)
    service.create_level(asset=asset, target_price=99, suggested_weight_pct=10)

    triggers = service.evaluate_asset(
        asset=asset, current_price=99.5, observed_date=date.today()
    )

    assert len(triggers) == 1
    assert triggers[0].event_type == "manual_buy_level_crossed"
    assert len(triggers[0].levels) == 2


def test_expired_level_is_not_alerted(db_session) -> None:
    asset = _asset(db_session, "EXPIRED")
    service = PlannedEntryService(db_session)
    level = service.create_level(asset=asset, target_price=100)
    level.expires_at = date.today() - timedelta(days=1)

    triggers = service.evaluate_asset(
        asset=asset, current_price=99, observed_date=date.today()
    )

    assert triggers == []
    assert level.status == "expired"


def test_rejects_rearm_inside_notification_zone(db_session) -> None:
    asset = _asset(db_session, "INVALID")
    service = PlannedEntryService(db_session)

    try:
        service.create_level(
            asset=asset,
            target_price=100,
            tolerance_pct=2,
            rearm_distance_pct=1,
        )
    except ValueError as exc:
        assert "rearme" in str(exc).lower()
    else:  # pragma: no cover
        raise AssertionError("Expected invalid rearm distance to be rejected")
