"""Smoke tests that ensure the Crypto Pump Radar module does NOT interfere
with the main investment pipeline (scoring, alerts, portfolio, daily job).
"""

from __future__ import annotations

import importlib

import pytest

from data.repositories.alerts_repo import AlertsRepository
from data.repositories.crypto_pump_repo import CryptoPumpRepository
from data.repositories.portfolio_repo import PortfolioRepository
from data.repositories.signals_repo import SignalsRepository


def test_main_scoring_service_does_not_import_crypto_pump_module():
    """ScoringService must not depend on crypto pump code."""

    module = importlib.import_module("services.scoring_service")
    module_text = "".join(getattr(module, "__file__", "") or "")
    # Hard import check: the source must not reference the pump module.
    with open(module.__file__, encoding="utf-8") as handler:
        source = handler.read()
    assert "crypto_pump" not in source, (
        "ScoringService should remain independent from the Crypto Pump Radar."
    )
    assert module_text == module.__file__


def test_recommendation_facade_does_not_reference_crypto_pump():
    with open(
        importlib.import_module("services.recommendation_facade").__file__,
        encoding="utf-8",
    ) as handler:
        source = handler.read()
    assert "crypto_pump" not in source


def test_alert_service_does_not_reference_crypto_pump():
    with open(
        importlib.import_module("services.alert_service").__file__,
        encoding="utf-8",
    ) as handler:
        source = handler.read()
    assert "crypto_pump" not in source


def test_daily_market_run_service_does_not_reference_crypto_pump():
    with open(
        importlib.import_module(
            "services.daily_market_run_service"
        ).__file__,
        encoding="utf-8",
    ) as handler:
        source = handler.read()
    assert "crypto_pump" not in source


def test_crypto_pump_run_does_not_touch_main_signal_tables(db_session):
    """Persisting pump snapshots must leave signals/alerts/portfolio untouched."""

    repo = CryptoPumpRepository(db_session)
    signals_repo = SignalsRepository(db_session)
    alerts_repo = AlertsRepository(db_session)
    portfolio_repo = PortfolioRepository(db_session)

    signals_before = len(signals_repo.latest_signals())
    alerts_before = len(alerts_repo.list_recent(limit=1000))
    positions_before = len(portfolio_repo.list_positions())

    repo.add_snapshot(
        {
            "chain": "ethereum",
            "pair_address": "0xiso",
            "symbol": "ISO/WETH",
            "price_usd": 1.0,
            "final_speculative_score": 70.0,
            "classification": "HIGH_RISK_PUMP",
        }
    )

    assert len(signals_repo.latest_signals()) == signals_before
    assert len(alerts_repo.list_recent(limit=1000)) == alerts_before
    assert len(portfolio_repo.list_positions()) == positions_before


def test_crypto_pump_config_yaml_loads():
    from core.config import load_yaml_config

    cfg = load_yaml_config("crypto_pump_radar.yaml")
    assert "scanner" in cfg
    assert "scoring" in cfg
    assert cfg["job"]["affects_main_daily_job"] is False


def test_pump_classifications_are_stored_separately_from_main_signals():
    """Even though label strings overlap (e.g. WATCH), pump labels live in a
    separate ORM table so they cannot be confused with main signals."""

    from data.database import CryptoPumpSnapshotORM, SignalORM

    assert CryptoPumpSnapshotORM.__tablename__ != SignalORM.__tablename__
    pump_columns = {c.name for c in CryptoPumpSnapshotORM.__table__.columns}
    signal_columns = {c.name for c in SignalORM.__table__.columns}
    # Pump table must not look like a SignalORM — different shape on purpose.
    assert "pair_address" in pump_columns
    assert "asset_id" not in pump_columns
    assert "pair_address" not in signal_columns


@pytest.mark.parametrize("module_path", [
    "services.crypto_pump_radar_service",
    "services.crypto_pump_scoring_service",
    "services.crypto_pump_history_service",
    "data.providers.dexscreener_provider",
    "data.repositories.crypto_pump_repo",
])
def test_crypto_pump_module_imports_cleanly(module_path):
    """Smoke test that the modules import without side-effects on the main system."""

    module = importlib.import_module(module_path)
    assert module is not None
