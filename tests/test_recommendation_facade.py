from __future__ import annotations

from types import SimpleNamespace

from data.database import AssetORM
from services.recommendation_facade import RecommendationFacade


def _make_asset(db_session) -> AssetORM:
    asset = AssetORM(
        symbol="EXSA.DE",
        name="iShares STOXX Europe 600 UCITS ETF",
        asset_type="etf",
        sector="Broad Market",
        region="EU",
        enabled=True,
        supports_fundamentals=True,
    )
    db_session.add(asset)
    db_session.flush()
    return asset


def test_stale_refresh_does_not_generate_a_signal(db_session) -> None:
    asset = _make_asset(db_session)
    facade = RecommendationFacade(db_session)
    facade.assets_repo.list_enabled = lambda: [asset]
    facade.market_data_service.refresh_daily_prices = lambda *_args, **_kwargs: (
        SimpleNamespace(
            status="preserved_cached_data",
            freshness_status="stale",
        )
    )
    generated_for: list[str] = []
    facade.signal_pipeline.generate_for_asset = lambda current_asset: generated_for.append(
        current_asset.symbol
    )

    summary = facade.refresh_and_generate_all(force=True)

    assert generated_for == []
    assert summary.generated_signals == 0
    assert summary.preserved_assets == 1
    assert summary.provider_error_assets == ["EXSA.DE"]


def test_fresh_refresh_generates_a_signal(db_session) -> None:
    asset = _make_asset(db_session)
    facade = RecommendationFacade(db_session)
    facade.assets_repo.list_enabled = lambda: [asset]
    facade.market_data_service.refresh_daily_prices = lambda *_args, **_kwargs: (
        SimpleNamespace(
            status="refreshed",
            freshness_status="fresh",
        )
    )
    facade.signal_pipeline.generate_for_asset = lambda _asset: {"signal": "BUY"}

    summary = facade.refresh_and_generate_all(force=True)

    assert summary.generated_signals == 1
    assert summary.signal_assets == ["EXSA.DE"]
    assert summary.provider_error_assets == []
