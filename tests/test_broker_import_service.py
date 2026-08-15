# ruff: noqa: E501

from __future__ import annotations

import pytest

from data.database import AssetORM
from data.repositories.portfolio_repo import PortfolioRepository
from services.broker_import_service import (
    BrokerImportService,
    TradeRepublicCsvParser,
)

CSV_HEADER = (
    'datetime,"date","account_type","category","type","asset_class","name",'
    '"symbol","shares","price","amount","fee","tax","currency","original_amount",'
    '"original_currency","fx_rate","description","transaction_id","counterparty_name",'
    '"counterparty_iban","payment_reference","mcc_code"\n'
)


def _trade_republic_csv() -> str:
    return CSV_HEADER + """2026-01-01T12:37:45Z,"2026-01-01","DEFAULT","CASH","INTEREST_PAYMENT","","","","","","159.90","","-30.38","EUR","","","","Interest","cash-1","","","",""
2026-03-23T08:10:20Z,"2026-03-23","DEFAULT","TRADING","BUY","FUND","Physical Gold USD (Acc)","IE00B4ND3602","14","71.24","-997.36","-1.00","","EUR","","","","Gold buy","gold-buy-1","","","",""
2026-03-23T08:10:21Z,"2026-03-23","DEFAULT","TRADING","BUY","FUND","Physical Gold USD (Acc)","IE00B4ND3602","0.037057","71.24","-2.64","","","EUR","","","","Gold buy fractional","gold-buy-2","","","",""
2026-03-27T17:56:14Z,"2026-03-27","DEFAULT","TRADING","BUY","FUND","Core S&P 500 USD (Acc)","IE00B5BMR087","1.677289","596.20","-1000.00","-1.00","","EUR","","","","S&P buy","sp-buy-1","","","",""
2026-04-01T15:09:26Z,"2026-04-01","DEFAULT","TRADING","SELL","FUND","Physical Gold USD (Acc)","IE00B4ND3602","-14","79.695","1115.73","-1.00","-22.11","EUR","","","","Gold sell","gold-sell-1","","","",""
"""


def _seed_assets(db_session) -> tuple[AssetORM, AssetORM]:
    gold = AssetORM(
        symbol="PPFB.DE",
        name="iShares Physical Gold ETC",
        asset_type="etf",
        sector="Commodities",
        region="EU",
        enabled=True,
        supports_fundamentals=False,
    )
    sp500 = AssetORM(
        symbol="SXR8.DE",
        name="iShares Core S&P 500 UCITS ETF USD (Acc)",
        asset_type="etf",
        sector="Broad Market",
        region="EU",
        enabled=True,
        supports_fundamentals=True,
    )
    db_session.add_all([gold, sp500])
    db_session.flush()
    return gold, sp500


def test_trade_republic_parser_filters_and_normalizes_trading_rows() -> None:
    rows = TradeRepublicCsvParser().parse(_trade_republic_csv())

    assert len(rows) == 4
    assert rows[0].transaction_type == "BUY"
    assert rows[-1].transaction_type == "SELL"
    assert rows[-1].quantity == pytest.approx(14)
    assert rows[-1].gross_amount == pytest.approx(1115.73)
    assert rows[-1].fees == pytest.approx(1)
    assert rows[-1].taxes == pytest.approx(22.11)


def test_import_maps_isins_recalculates_positions_and_skips_duplicates(db_session) -> None:
    gold, sp500 = _seed_assets(db_session)
    service = BrokerImportService(db_session)
    rows = service.parse_trade_republic(_trade_republic_csv())

    first = service.import_trade_republic(rows)
    second = service.import_trade_republic(rows)

    assert first.imported == 4
    assert first.duplicates == 0
    assert second.imported == 0
    assert second.duplicates == 4
    repo = PortfolioRepository(db_session)
    gold_position = repo.get_by_asset_id(gold.id)
    sp500_position = repo.get_by_asset_id(sp500.id)
    assert gold_position is not None
    assert gold_position.quantity == pytest.approx(0.037057)
    assert sp500_position is not None
    assert sp500_position.quantity == pytest.approx(1.677289)
    transactions = repo.list_transactions()
    assert len(transactions) == 4
    assert transactions[-1].external_transaction_id == "gold-sell-1"
    assert transactions[-1].taxes == pytest.approx(22.11)
    assert transactions[-1].transaction_currency == "EUR"


def test_unmapped_asset_is_not_imported(db_session) -> None:
    unknown = CSV_HEADER + """2026-03-23T08:10:20Z,"2026-03-23","DEFAULT","TRADING","BUY","STOCK","Unknown","XX0000000001","1","10","-10","","","EUR","","","","Unknown buy","unknown-1","","","",""
"""
    service = BrokerImportService(db_session)

    summary = service.import_trade_republic(service.parse_trade_republic(unknown))

    assert summary.imported == 0
    assert summary.unmapped == 1
    assert PortfolioRepository(db_session).list_transactions() == []
