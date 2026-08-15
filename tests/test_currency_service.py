from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from data.database import AssetORM, PriceBarDailyORM
from data.repositories.prices_repo import PricesRepository
from services.currency_service import CurrencyService
from services.portfolio_service import PortfolioService


def test_currency_service_caches_usd_eur_rate(db_session) -> None:
    calls: list[str] = []

    def loader(source_currency: str, as_of: date | None) -> pd.DataFrame:
        calls.append(source_currency)
        return pd.DataFrame([{"date": date(2026, 8, 14), "rate": 0.8641}])

    service = CurrencyService(db_session, loader=loader)
    first, first_meta = service.convert(100, "USD", as_of=date(2026, 8, 14))
    second, second_meta = service.convert(100, "USD", as_of=date(2026, 8, 14))

    assert first == pytest.approx(86.41)
    assert second == pytest.approx(86.41)
    assert first_meta.rate_date == date(2026, 8, 14)
    assert second_meta.provider == "yfinance"
    assert calls == ["USD"]


def test_price_repository_persists_quote_currency(db_session) -> None:
    asset = AssetORM(
        symbol="MSFT",
        name="Microsoft",
        asset_type="stock",
        sector="Technology",
        region="US",
        quote_currency="USD",
        enabled=True,
        supports_fundamentals=True,
    )
    db_session.add(asset)
    db_session.flush()
    frame = pd.DataFrame(
        [
            {
                "date": date(2026, 8, 14),
                "open": 100,
                "high": 102,
                "low": 99,
                "close": 101,
                "volume": 1_000,
            }
        ]
    )

    repo = PricesRepository(db_session)
    repo.upsert_asset_prices(asset.id, frame, quote_currency="USD")

    assert repo.get_asset_prices(asset.id).iloc[0]["quote_currency"] == "USD"


def test_currency_service_converts_pence_before_fx(db_session) -> None:
    def loader(source_currency: str, as_of: date | None) -> pd.DataFrame:
        assert source_currency == "GBP"
        return pd.DataFrame([{"date": date(2026, 8, 14), "rate": 1.1694}])

    converted, metadata = CurrencyService(db_session, loader=loader).convert(
        4_431, "GBX", as_of=date(2026, 8, 14)
    )

    assert converted == pytest.approx(51.8141, rel=1e-4)
    assert metadata.rate == pytest.approx(0.011694)


def test_portfolio_values_usd_quote_in_eur(db_session, monkeypatch) -> None:
    asset = AssetORM(
        symbol="AAPL",
        name="Apple",
        asset_type="stock",
        sector="Technology",
        region="US",
        quote_currency="USD",
        enabled=True,
        supports_fundamentals=True,
    )
    db_session.add(asset)
    db_session.flush()
    db_session.add(
        PriceBarDailyORM(
            asset_id=asset.id,
            date=date(2026, 8, 14),
            open=300,
            high=310,
            low=295,
            close=305,
            volume=1_000,
            quote_currency="USD",
        )
    )
    db_session.flush()

    service = PortfolioService(db_session)
    monkeypatch.setattr(
        service.currency_service,
        "loader",
        lambda source, as_of: pd.DataFrame(
            [{"date": date(2026, 8, 14), "rate": 0.8641}]
        ),
    )
    service.set_total_capital(10_000)
    service.add_transaction_and_recalculate(
        asset_id=asset.id,
        transaction_type="BUY",
        transaction_date=date(2026, 8, 14),
        quantity=2,
        gross_amount=500,
        price=250,
        transaction_currency="EUR",
    )

    row = service.portfolio_rows()[0]
    assert row["current_price_native"] == pytest.approx(305)
    assert row["current_price_eur"] == pytest.approx(263.5505)
    assert row["current_value"] == pytest.approx(527.101)
    assert row["pnl"] == pytest.approx(27.101)
    assert row["current_weight"] == pytest.approx(0.0527101)


def test_portfolio_flags_implausible_mapping(db_session) -> None:
    asset = AssetORM(
        symbol="TLT",
        name="Treasury ETF",
        asset_type="etf",
        sector="Bonds",
        region="US",
        quote_currency="EUR",
        enabled=True,
        supports_fundamentals=False,
    )
    db_session.add(asset)
    db_session.flush()
    db_session.add(
        PriceBarDailyORM(
            asset_id=asset.id,
            date=date(2026, 8, 14),
            open=80,
            high=83,
            low=79,
            close=82,
            volume=1_000,
            quote_currency="EUR",
        )
    )
    db_session.flush()
    service = PortfolioService(db_session)
    service.add_transaction_and_recalculate(
        asset_id=asset.id,
        transaction_type="BUY",
        transaction_date=date(2026, 8, 14),
        quantity=100,
        gross_amount=263,
        price=2.63,
    )

    assert "Revisar mapeo" in service.portfolio_rows()[0]["valuation_warning"]
