from __future__ import annotations

from datetime import date

import pytest

from data.database import AssetORM, PlannedEntryLevelORM, PriceBarDailyORM
from data.repositories.portfolio_repo import PortfolioRepository
from services.portfolio_service import PortfolioService


def test_portfolio_repo_can_delete_existing_position(db_session) -> None:
    asset = AssetORM(
        symbol="MSFT",
        name="Microsoft",
        asset_type="stock",
        sector="Technology",
        region="US",
        enabled=True,
        supports_fundamentals=True,
    )
    db_session.add(asset)
    db_session.flush()

    repo = PortfolioRepository(db_session)
    repo.upsert_position(
        asset_id=asset.id,
        quantity=10,
        avg_cost=400,
        current_weight=0.1,
        target_weight=0.08,
    )

    deleted = repo.delete_position(asset.id)

    assert deleted is True
    assert repo.get_by_asset_id(asset.id) is None


def _seed_asset_with_prices(db_session) -> AssetORM:
    asset = AssetORM(
        symbol="SXR8.DE",
        name="S&P 500 ETF",
        asset_type="etf",
        sector="Broad Market",
        region="EU",
        enabled=True,
        supports_fundamentals=True,
    )
    db_session.add(asset)
    db_session.flush()
    db_session.add_all(
        [
            PriceBarDailyORM(
                asset_id=asset.id,
                date=date(2026, 1, 2),
                open=100,
                high=101,
                low=99,
                close=100,
                volume=1000,
            ),
            PriceBarDailyORM(
                asset_id=asset.id,
                date=date(2026, 1, 9),
                open=120,
                high=121,
                low=119,
                close=120,
                volume=1000,
            ),
            PriceBarDailyORM(
                asset_id=asset.id,
                date=date(2026, 1, 16),
                open=150,
                high=151,
                low=149,
                close=150,
                volume=1000,
            ),
        ]
    )
    db_session.flush()
    return asset


def test_portfolio_transactions_accumulate_weighted_average_cost(db_session) -> None:
    asset = _seed_asset_with_prices(db_session)
    service = PortfolioService(db_session)
    service.set_total_capital(10_000)

    service.add_transaction_and_recalculate(
        asset_id=asset.id,
        transaction_type="BUY",
        transaction_date=date(2026, 1, 2),
        quantity=10,
        gross_amount=None,
        price=100,
    )
    service.add_transaction_and_recalculate(
        asset_id=asset.id,
        transaction_type="BUY",
        transaction_date=date(2026, 1, 9),
        quantity=5,
        gross_amount=None,
        price=120,
    )

    position = PortfolioRepository(db_session).get_by_asset_id(asset.id)

    assert position is not None
    assert position.quantity == pytest.approx(15)
    assert position.avg_cost == pytest.approx((10 * 100 + 5 * 120) / 15)
    assert position.current_weight == pytest.approx((15 * 150) / 10_000)


def test_portfolio_sell_reduces_quantity_and_keeps_average_cost_basis(db_session) -> None:
    asset = _seed_asset_with_prices(db_session)
    service = PortfolioService(db_session)
    service.set_total_capital(10_000)
    service.add_transaction_and_recalculate(
        asset_id=asset.id,
        transaction_type="BUY",
        transaction_date=date(2026, 1, 2),
        quantity=10,
        gross_amount=None,
        price=100,
    )
    service.add_transaction_and_recalculate(
        asset_id=asset.id,
        transaction_type="SELL",
        transaction_date=date(2026, 1, 16),
        quantity=4,
        gross_amount=None,
        price=150,
    )

    position = PortfolioRepository(db_session).get_by_asset_id(asset.id)

    assert position is not None
    assert position.quantity == pytest.approx(6)
    assert position.avg_cost == pytest.approx(100)


def test_portfolio_prevents_selling_more_than_available(db_session) -> None:
    asset = _seed_asset_with_prices(db_session)
    service = PortfolioService(db_session)
    service.add_transaction_and_recalculate(
        asset_id=asset.id,
        transaction_type="BUY",
        transaction_date=date(2026, 1, 2),
        quantity=2,
        gross_amount=None,
        price=100,
    )

    with pytest.raises(ValueError, match="cannot sell more units"):
        service.add_transaction_and_recalculate(
            asset_id=asset.id,
            transaction_type="SELL",
            transaction_date=date(2026, 1, 16),
            quantity=3,
            gross_amount=None,
            price=150,
        )


def test_price_for_date_uses_previous_close_when_market_date_missing(db_session) -> None:
    asset = _seed_asset_with_prices(db_session)
    service = PortfolioService(db_session)

    price, source, used_date = service.price_for_date(asset.id, date(2026, 1, 10))

    assert price == pytest.approx(120)
    assert source == "previous_close"
    assert used_date == date(2026, 1, 9)


def test_recalculate_preserves_manual_positions_without_transactions(db_session) -> None:
    asset = _seed_asset_with_prices(db_session)
    repo = PortfolioRepository(db_session)
    repo.upsert_position(
        asset_id=asset.id,
        quantity=3,
        avg_cost=100,
        current_weight=0.0,
        target_weight=0.2,
    )
    service = PortfolioService(db_session)
    service.set_total_capital(1_000)

    position = repo.get_by_asset_id(asset.id)

    assert position is not None
    assert position.quantity == pytest.approx(3)
    assert position.avg_cost == pytest.approx(100)
    assert position.current_weight == pytest.approx((3 * 150) / 1_000)
    assert position.target_weight == pytest.approx(0.2)


def test_available_quantity_uses_manual_position_when_no_transactions(db_session) -> None:
    asset = _seed_asset_with_prices(db_session)
    PortfolioRepository(db_session).upsert_position(
        asset_id=asset.id,
        quantity=3.5,
        avg_cost=100,
        current_weight=0.0,
        target_weight=0.0,
    )

    quantity = PortfolioService(db_session).available_quantity(asset.id)

    assert quantity == pytest.approx(3.5)


def test_portfolio_rows_distinguish_cost_basis_from_current_value(db_session) -> None:
    asset = _seed_asset_with_prices(db_session)
    repo = PortfolioRepository(db_session)
    repo.upsert_position(
        asset_id=asset.id,
        quantity=2,
        avg_cost=100,
        current_weight=0.0,
        target_weight=0.0,
    )

    row = PortfolioService(db_session).portfolio_rows()[0]

    assert row["cost_basis"] == pytest.approx(200)
    assert row["current_value"] == pytest.approx(300)
    assert row["pnl"] == pytest.approx(100)


def test_planned_cash_reserve_uses_nominal_before_percentage(db_session) -> None:
    asset = _seed_asset_with_prices(db_session)
    db_session.add_all(
        [
            PlannedEntryLevelORM(
                asset_id=asset.id,
                target_price=140,
                price_currency="EUR",
                suggested_weight_pct=25,
                suggested_capital=1_500,
                tolerance_pct=1,
                rearm_distance_pct=3,
                status="active",
            ),
            PlannedEntryLevelORM(
                asset_id=asset.id,
                target_price=130,
                price_currency="EUR",
                suggested_weight_pct=10,
                tolerance_pct=1,
                rearm_distance_pct=3,
                status="triggered",
            ),
        ]
    )
    db_session.flush()

    result = PortfolioService(db_session).planned_cash_reserve(
        total_capital=10_000,
        estimated_cash=5_000,
        as_of=date(2026, 1, 16),
    )

    assert result.requested_commitment == pytest.approx(2_500)
    assert result.effective_reserved == pytest.approx(2_500)
    assert result.free_cash == pytest.approx(2_500)
    assert result.reserve_deficit == pytest.approx(0)
    assert result.reservations[0].calculation_basis == "capital_nominal"
    assert result.reservations[0].current_price == pytest.approx(150)


def test_planned_cash_reserve_excludes_inactive_and_expired_levels(db_session) -> None:
    asset = _seed_asset_with_prices(db_session)
    db_session.add_all(
        [
            PlannedEntryLevelORM(
                asset_id=asset.id,
                target_price=140,
                suggested_capital=2_000,
                tolerance_pct=1,
                rearm_distance_pct=3,
                status="paused",
            ),
            PlannedEntryLevelORM(
                asset_id=asset.id,
                target_price=130,
                suggested_capital=3_000,
                tolerance_pct=1,
                rearm_distance_pct=3,
                status="active",
                expires_at=date(2026, 1, 10),
            ),
        ]
    )
    db_session.flush()

    result = PortfolioService(db_session).planned_cash_reserve(
        total_capital=10_000,
        estimated_cash=5_000,
        as_of=date(2026, 1, 16),
    )

    assert result.reservations == ()
    assert result.requested_commitment == pytest.approx(0)
    assert result.free_cash == pytest.approx(5_000)


def test_planned_cash_reserve_reports_unfunded_commitment(db_session) -> None:
    asset = _seed_asset_with_prices(db_session)
    db_session.add(
        PlannedEntryLevelORM(
            asset_id=asset.id,
            target_price=140,
            suggested_weight_pct=60,
            tolerance_pct=1,
            rearm_distance_pct=3,
            status="active",
        )
    )
    db_session.flush()

    result = PortfolioService(db_session).planned_cash_reserve(
        total_capital=10_000,
        estimated_cash=2_000,
        as_of=date(2026, 1, 16),
    )

    assert result.requested_commitment == pytest.approx(6_000)
    assert result.effective_reserved == pytest.approx(2_000)
    assert result.free_cash == pytest.approx(0)
    assert result.reserve_deficit == pytest.approx(4_000)
