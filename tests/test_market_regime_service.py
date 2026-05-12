from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd

from data.database import AssetORM
from data.repositories.market_regime_repo import MarketRegimeRepository
from market_regime import MarketRegimeService


def _price_frame(
    *,
    periods: int = 280,
    start: float = 100.0,
    end: float = 160.0,
    symbol_shift: float = 0.0,
) -> pd.DataFrame:
    dates = pd.date_range("2024-01-01", periods=periods, freq="D")
    close = np.linspace(start, end, periods) + symbol_shift
    high = close * 1.01
    low = close * 0.99
    open_ = close * 0.995
    return pd.DataFrame(
        {
            "date": dates,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": np.full(periods, 1_000_000.0),
        }
    )


def test_regime_detects_bull_context() -> None:
    service = MarketRegimeService()
    regime = service.compute_regime(
        _price_frame(start=100, end=180),
        benchmark_frame=_price_frame(start=100, end=140),
    )

    assert regime.bull_probability > regime.bear_probability
    assert regime.dominant_regime in {"BULL", "TRANSITION"}
    assert 0 <= regime.bubble_probability <= 100
    assert "bull_score" in regime.breakdown


def test_regime_detects_bear_context() -> None:
    service = MarketRegimeService()
    regime = service.compute_regime(
        _price_frame(start=180, end=80),
        benchmark_frame=_price_frame(start=100, end=140),
    )

    assert regime.bear_probability > regime.bull_probability
    assert regime.dominant_regime in {"BEAR", "TRANSITION"}


def test_bubble_probability_rises_on_extension() -> None:
    service = MarketRegimeService()
    steady = service.compute_regime(_price_frame(start=100, end=120))

    extended = _price_frame(start=100, end=120)
    extended.loc[240:, "close"] = np.linspace(150, 260, len(extended.loc[240:]))
    extended["high"] = extended["close"] * 1.01
    extended["low"] = extended["close"] * 0.99
    overheated = service.compute_regime(extended)

    assert overheated.bubble_probability > steady.bubble_probability


def test_regime_history_returns_daily_rows_after_minimum_history() -> None:
    service = MarketRegimeService({"history": {"min_rows": 220}})
    history = service.compute_regime_history(
        _price_frame(periods=240),
        start_date=date(2024, 8, 1),
        end_date=date(2024, 8, 20),
    )

    assert not history.empty
    assert {"bull_probability", "bear_probability", "bubble_probability"}.issubset(
        history.columns
    )


def test_market_regime_repository_upserts_and_reads_history(db_session) -> None:
    asset = AssetORM(
        symbol="TEST",
        name="Test Asset",
        asset_type="stock",
        sector="Testing",
        region="US",
        enabled=True,
        supports_fundamentals=False,
    )
    db_session.add(asset)
    db_session.flush()

    service = MarketRegimeService()
    regime = service.compute_regime(_price_frame(start=100, end=150))
    repo = MarketRegimeRepository(db_session)
    repo.upsert_regime(asset_id=asset.id, symbol=asset.symbol, regime=regime)
    repo.upsert_regime(asset_id=asset.id, symbol=asset.symbol, regime=regime)

    latest = repo.latest_for_asset(asset.id)
    history = repo.history_for_asset(asset.id)

    assert latest is not None
    assert latest.symbol == "TEST"
    assert len(history) == 1
