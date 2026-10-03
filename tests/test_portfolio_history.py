from datetime import date

import pandas as pd
import pytest
from streamlit.testing.v1 import AppTest

from data.database import AssetORM, FxRateDailyORM, PriceBarDailyORM
from data.repositories.portfolio_repo import PortfolioRepository
from services.portfolio_history_service import PortfolioHistoryService


def asset(session, symbol="TEST.DE", currency="EUR"):
    row = AssetORM(
        symbol=symbol,
        name="Test",
        asset_type="etf",
        sector="Broad Market",
        region="EU",
        enabled=True,
        supports_fundamentals=False,
        quote_currency=currency,
    )
    session.add(row)
    session.flush()
    return row


def price(session, aid, day, close, currency="EUR"):
    session.add(
        PriceBarDailyORM(
            asset_id=aid,
            date=date(2026, 1, day),
            open=close,
            high=close,
            low=close,
            close=close,
            volume=100,
            quote_currency=currency,
        )
    )
    session.flush()


def trade(session, aid, day, kind="BUY", quantity=10, value=100, fees=0, taxes=0):
    PortfolioRepository(session).add_transaction(
        asset_id=aid,
        transaction_type=kind,
        transaction_date=date(2026, 1, day),
        quantity=quantity,
        price=value,
        gross_amount=quantity * value,
        fees=fees,
        taxes=taxes,
    )


def test_history_weighted_cost_sales_expenses_and_daily_holdings(db_session):
    row = asset(db_session)
    price(db_session, row.id, 2, 110)
    price(db_session, row.id, 4, 130)
    trade(db_session, row.id, 2, fees=5, taxes=5)
    trade(db_session, row.id, 3, quantity=10, value=120)
    trade(db_session, row.id, 4, "SELL", quantity=5, value=130, fees=8)
    trade(db_session, row.id, 5, "SELL", quantity=15, value=130)
    trade(db_session, row.id, 9)  # Future purchases must not enter this chart.
    frame = PortfolioHistoryService(db_session).history(as_of=date(2026, 1, 6)).frame
    assert frame.invested_cost.tolist() == pytest.approx([1010, 2210, 1657.5, 0, 0])
    assert frame.market_value.tolist() == pytest.approx([1100, 2200, 1950, 0, 0])
    assert frame.iloc[1].oldest_price_date == date(2026, 1, 2)


def test_history_never_backfills_future_prices_or_treats_partial_value_as_total(db_session):
    first, second = asset(db_session), asset(db_session, "SECOND.DE")
    for row in (first, second):
        trade(db_session, row.id, 1)
    price(db_session, first.id, 1, 100)
    price(db_session, second.id, 3, 150)
    frame = PortfolioHistoryService(db_session).history(as_of=date(2026, 1, 4)).frame
    assert frame.market_value.iloc[:2].isna().all()
    assert frame.iloc[0].missing_symbols == "SECOND.DE"
    assert frame.market_value.iloc[2:].tolist() == [2500, 2500]
    assert frame.invested_cost.tolist() == [2000] * 4


@pytest.mark.parametrize(("currency", "close", "rate"), [("USD", 100, 0.9), ("GBp", 10000, 1.2)])
def test_history_uses_historical_fx_and_pence_without_future_rates(
    db_session, currency, close, rate
):
    row = asset(db_session, "FXTEST", currency)
    trade(db_session, row.id, 1)
    price(db_session, row.id, 1, close, currency)
    price(db_session, row.id, 2, close, currency)
    db_session.add(
        FxRateDailyORM(
            source_currency="GBP" if currency == "GBp" else currency,
            target_currency="EUR",
            date=date(2026, 1, 2),
            rate=rate,
        )
    )
    db_session.flush()
    frame = PortfolioHistoryService(db_session).history(as_of=date(2026, 1, 3)).frame
    assert pd.isna(frame.iloc[0].market_value)
    assert frame.iloc[1].market_value == pytest.approx(1000 * rate)
    assert frame.iloc[2].market_value == pytest.approx(1000 * rate)


def test_manual_positions_are_explicitly_excluded_without_inventing_history(db_session):
    row = asset(db_session)
    PortfolioRepository(db_session).upsert_position(
        asset_id=row.id,
        quantity=10,
        avg_cost=100,
        current_weight=0.1,
        target_weight=0.1,
    )
    result = PortfolioHistoryService(db_session).history()
    assert result.frame.empty
    assert result.excluded_symbols == ("TEST.DE",)


def test_extreme_valuations_are_flagged_but_preserved(db_session):
    row = asset(db_session)
    trade(db_session, row.id, 1)
    price(db_session, row.id, 1, 10000)
    frame = PortfolioHistoryService(db_session).history(as_of=date(2026, 1, 1)).frame
    assert frame.iloc[0].market_value == 100000
    assert frame.iloc[0].review_symbols == "TEST.DE"


@pytest.mark.parametrize("missing", [False, True])
def test_chart_renders_and_period_filter_preserves_cost(missing):
    script = f"""
import pandas as pd
from app.components.portfolio_history import render_portfolio_history
from services.portfolio_history_service import PortfolioHistory
frame = pd.DataFrame({{
    "date": pd.date_range("2026-01-01", "2026-04-01"),
    "invested_cost": 1000., "market_value": {"None" if missing else "1250."},
    "oldest_price_date": None,
}})
render_portfolio_history(PortfolioHistory(frame))
"""
    app = AppTest.from_string(script).run()
    assert not app.exception
    assert app.metric[0].value == "1.000,00 €"
    assert app.metric[1].value == ("Sin datos" if missing else "1.250,00 €")
    app.radio[0].set_value("1M").run()
    assert not app.exception
    assert app.metric[0].value == "1.000,00 €"
    assert len(app.get("plotly_chart")) == 1
