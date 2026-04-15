from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

import pandas as pd

from data.database import AssetDataStatusORM, AssetORM, DataRefreshLogORM, PriceBarDailyORM
from data.providers.base_provider import ProviderError
from services.market_data_service import MarketDataService


class StubProvider:
    def __init__(
        self,
        name: str,
        frame: pd.DataFrame | None = None,
        error: Exception | None = None,
    ):
        self.name = name
        self.frame = frame if frame is not None else pd.DataFrame()
        self.error = error
        self.called = 0

    def supports(self, asset: AssetORM) -> bool:
        return True

    def fetch_daily_prices(self, asset: AssetORM) -> pd.DataFrame:
        self.called += 1
        if self.error is not None:
            raise self.error
        return self.frame.copy()


def make_asset(db_session, symbol: str = "TEST", asset_type: str = "stock") -> AssetORM:
    asset = AssetORM(
        symbol=symbol,
        name=f"{symbol} Asset",
        asset_type=asset_type,
        sector="Technology" if asset_type != "crypto" else "Crypto",
        region="US",
        enabled=True,
        supports_fundamentals=asset_type != "crypto",
    )
    db_session.add(asset)
    db_session.flush()
    return asset


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def make_service(
    db_session,
    provider: StubProvider,
    *,
    demo_mode: bool = True,
    preserve_real_data_on_provider_failure: bool = True,
) -> MarketDataService:
    settings = SimpleNamespace(demo_mode=demo_mode)
    data_config = SimpleNamespace(
        prefer_cached_data=True,
        refresh_on_app_start=False,
        equities_refresh_interval_hours=24,
        crypto_refresh_interval_minutes=180,
        equities_market_day_rollover_hour_local=21,
        equities_market_day_rollover_minute_local=30,
        max_staleness_days=5,
        allow_demo_fallback=True,
        preserve_real_data_on_provider_failure=preserve_real_data_on_provider_failure,
        yfinance_enabled=False,
        yfinance_as_fallback=False,
        yfinance_long_history_enabled=False,
        yfinance_long_history_period="5y",
        yfinance_normal_history_period="1y",
        yfinance_long_history_min_rows=1000,
        yfinance_backfill_asset_types=["stock", "etf"],
        yfinance_request_pause_seconds=0.5,
        allow_provider_mixing=True,
        recent_provider_mix_window_days=90,
        providers_priority={"stock": ["stub"], "etf": ["stub"], "crypto": ["stub"]},
    )
    return MarketDataService(
        db_session,
        settings=settings,
        data_config=data_config,
        providers={"stub": provider},
    )


def test_cache_hit_skips_provider_call_when_data_is_fresh(db_session) -> None:
    asset = make_asset(db_session)
    db_session.add(
        PriceBarDailyORM(
            asset_id=asset.id,
            date=date.today(),
            open=10,
            high=11,
            low=9,
            close=10.5,
            volume=1000,
        )
    )
    db_session.add(
        AssetDataStatusORM(
            asset_id=asset.id,
            last_available_bar_date=date.today(),
            last_successful_refresh_at=utc_now(),
            last_refresh_status="success",
            last_refresh_source="stub",
            data_mode="real",
            freshness_status="fresh",
        )
    )
    db_session.flush()

    provider = StubProvider("stub", frame=pd.DataFrame())
    result = make_service(db_session, provider).refresh_daily_prices(asset)

    assert result.status == "cache_hit"
    assert provider.called == 0


def test_cache_stale_refresh_ok_adds_only_new_rows(db_session) -> None:
    asset = make_asset(db_session)
    old_date = date.today() - timedelta(days=10)
    new_date = date.today()
    db_session.add(
        PriceBarDailyORM(
            asset_id=asset.id,
            date=old_date,
            open=10,
            high=11,
            low=9,
            close=10.5,
            volume=1000,
        )
    )
    db_session.add(
        AssetDataStatusORM(
            asset_id=asset.id,
            last_available_bar_date=old_date,
            last_successful_refresh_at=utc_now() - timedelta(days=10),
            last_refresh_status="success",
            last_refresh_source="stub",
            data_mode="real",
            freshness_status="stale",
        )
    )
    db_session.flush()

    provider = StubProvider(
        "stub",
        frame=pd.DataFrame(
            [
                {
                    "date": old_date,
                    "open": 10,
                    "high": 11,
                    "low": 9,
                    "close": 10.5,
                    "volume": 1000,
                },
                {
                    "date": new_date,
                    "open": 12,
                    "high": 13,
                    "low": 11,
                    "close": 12.5,
                    "volume": 1500,
                },
            ]
        ),
    )

    result = make_service(db_session, provider).refresh_daily_prices(asset)

    assert result.status == "refreshed"
    assert result.rows_stored == 1
    assert provider.called == 1
    prices = db_session.query(PriceBarDailyORM).filter_by(asset_id=asset.id).all()
    assert len(prices) == 2


def test_cache_stale_refresh_failure_preserves_existing_real_data(db_session) -> None:
    asset = make_asset(db_session)
    old_date = date.today() - timedelta(days=6)
    db_session.add(
        PriceBarDailyORM(
            asset_id=asset.id,
            date=old_date,
            open=10,
            high=11,
            low=9,
            close=10.5,
            volume=1000,
        )
    )
    db_session.add(
        AssetDataStatusORM(
            asset_id=asset.id,
            last_available_bar_date=old_date,
            last_successful_refresh_at=utc_now() - timedelta(days=6),
            last_refresh_status="success",
            last_refresh_source="stub",
            data_mode="real",
            freshness_status="stale",
        )
    )
    db_session.flush()

    provider = StubProvider("stub", error=ProviderError("boom"))
    result = make_service(db_session, provider).refresh_daily_prices(asset)

    assert result.status == "preserved_cached_data"
    assert result.data_mode == "real"
    prices = db_session.query(PriceBarDailyORM).filter_by(asset_id=asset.id).all()
    assert len(prices) == 1


def test_no_data_and_provider_failure_uses_demo_fallback_if_allowed(db_session) -> None:
    asset = make_asset(db_session)
    provider = StubProvider("stub", error=ProviderError("boom"))

    result = make_service(db_session, provider).refresh_daily_prices(asset)

    assert result.status == "demo_fallback"
    assert result.provider_name == "demo"
    assert result.data_mode == "demo"
    assert db_session.query(PriceBarDailyORM).filter_by(asset_id=asset.id).count() > 0


def test_no_data_and_provider_success_persists_real_data(db_session) -> None:
    asset = make_asset(db_session)
    provider = StubProvider(
        "stub",
        frame=pd.DataFrame(
            [
                {
                    "date": date.today() - timedelta(days=1),
                    "open": 20,
                    "high": 21,
                    "low": 19,
                    "close": 20.5,
                    "volume": 2000,
                }
            ]
        ),
    )

    result = make_service(db_session, provider).refresh_daily_prices(asset)

    assert result.status == "refreshed"
    assert result.data_mode == "real"
    assert db_session.query(PriceBarDailyORM).filter_by(asset_id=asset.id).count() == 1


def test_demo_history_is_replaced_when_real_data_arrives(db_session) -> None:
    asset = make_asset(db_session)
    db_session.add(
        PriceBarDailyORM(
            asset_id=asset.id,
            date=date.today() - timedelta(days=3),
            open=400,
            high=410,
            low=390,
            close=405,
            volume=1000,
        )
    )
    db_session.add(
        AssetDataStatusORM(
            asset_id=asset.id,
            last_available_bar_date=date.today() - timedelta(days=3),
            last_successful_refresh_at=utc_now() - timedelta(days=3),
            last_refresh_status="demo_fallback",
            last_refresh_source="demo",
            data_mode="demo",
            freshness_status="stale",
        )
    )
    db_session.flush()

    provider = StubProvider(
        "stub",
        frame=pd.DataFrame(
            [
                {
                    "date": date.today() - timedelta(days=1),
                    "open": 70,
                    "high": 71,
                    "low": 69,
                    "close": 70.5,
                    "volume": 2000,
                }
            ]
        ),
    )

    result = make_service(db_session, provider).refresh_daily_prices(asset, force=True)

    assert result.status == "refreshed"
    assert result.data_mode == "real"
    rows = db_session.query(PriceBarDailyORM).filter_by(asset_id=asset.id).all()
    assert len(rows) == 1
    assert rows[0].close == 70.5


def test_conflicting_latest_overlap_replaces_existing_history(db_session) -> None:
    asset = make_asset(db_session)
    conflicting_date = date.today() - timedelta(days=1)
    db_session.add(
        PriceBarDailyORM(
            asset_id=asset.id,
            date=conflicting_date,
            open=430,
            high=440,
            low=420,
            close=430,
            volume=1000,
        )
    )
    db_session.add(
        AssetDataStatusORM(
            asset_id=asset.id,
            last_available_bar_date=conflicting_date,
            last_successful_refresh_at=utc_now() - timedelta(days=1),
            last_refresh_status="success",
            last_refresh_source="alphavantage",
            data_mode="real",
            freshness_status="stale",
        )
    )
    db_session.flush()

    provider = StubProvider(
        "stub",
        frame=pd.DataFrame(
            [
                {
                    "date": conflicting_date,
                    "open": 75,
                    "high": 76,
                    "low": 74,
                    "close": 76,
                    "volume": 2000,
                }
            ]
        ),
    )

    result = make_service(db_session, provider).refresh_daily_prices(asset, force=True)

    assert result.status == "refreshed"
    row = db_session.query(PriceBarDailyORM).filter_by(asset_id=asset.id).one()
    assert row.close == 76


def test_duplicate_dates_are_not_inserted_twice(db_session) -> None:
    asset = make_asset(db_session)
    duplicate_date = date.today() - timedelta(days=1)
    provider = StubProvider(
        "stub",
        frame=pd.DataFrame(
            [
                {
                    "date": duplicate_date,
                    "open": 20,
                    "high": 21,
                    "low": 19,
                    "close": 20.5,
                    "volume": 2000,
                },
                {
                    "date": duplicate_date,
                    "open": 20.1,
                    "high": 21.1,
                    "low": 19.1,
                    "close": 20.6,
                    "volume": 2100,
                },
            ]
        ),
    )

    result = make_service(db_session, provider).refresh_daily_prices(asset)

    assert result.status == "refreshed"
    assert db_session.query(PriceBarDailyORM).filter_by(asset_id=asset.id).count() == 1


def test_anomalous_legacy_rows_trigger_full_history_replacement(db_session) -> None:
    asset = make_asset(db_session)
    start_date = date.today() - timedelta(days=59)
    provider_rows = []

    for offset in range(60):
        current_date = start_date + timedelta(days=offset)
        real_close = 100 + offset
        existing_close = real_close * 8 if offset in {10, 20, 30} else real_close
        db_session.add(
            PriceBarDailyORM(
                asset_id=asset.id,
                date=current_date,
                open=existing_close,
                high=existing_close + 1,
                low=existing_close - 1,
                close=existing_close,
                volume=1000,
            )
        )
        provider_rows.append(
            {
                "date": current_date,
                "open": real_close,
                "high": real_close + 1,
                "low": real_close - 1,
                "close": real_close,
                "volume": 2000,
            }
        )

    db_session.add(
        AssetDataStatusORM(
            asset_id=asset.id,
            last_available_bar_date=start_date + timedelta(days=59),
            last_successful_refresh_at=utc_now() - timedelta(days=1),
            last_refresh_status="success",
            last_refresh_source="alphavantage",
            data_mode="real",
            freshness_status="stale",
        )
    )
    db_session.flush()

    provider = StubProvider("stub", frame=pd.DataFrame(provider_rows))
    result = make_service(db_session, provider).refresh_daily_prices(asset, force=True)

    assert result.status == "refreshed"
    refreshed_rows = (
        db_session.query(PriceBarDailyORM)
        .filter_by(asset_id=asset.id)
        .order_by(PriceBarDailyORM.date.asc())
        .all()
    )
    assert len(refreshed_rows) == 60
    assert refreshed_rows[10].close == 110


def test_extra_legacy_dates_trigger_full_history_replacement(db_session) -> None:
    asset = make_asset(db_session)
    start_date = date.today() - timedelta(days=99)
    provider_rows = []
    provider_dates = []

    for offset in range(100):
        current_date = start_date + timedelta(days=offset)
        if offset in {5, 25, 45}:
            continue
        real_close = 200 + offset
        provider_dates.append(current_date)
        provider_rows.append(
            {
                "date": current_date,
                "open": real_close,
                "high": real_close + 1,
                "low": real_close - 1,
                "close": real_close,
                "volume": 3000,
            }
        )
        db_session.add(
            PriceBarDailyORM(
                asset_id=asset.id,
                date=current_date,
                open=real_close,
                high=real_close + 1,
                low=real_close - 1,
                close=real_close,
                volume=1000,
            )
        )

    for extra_offset in {5, 25, 45}:
        extra_date = start_date + timedelta(days=extra_offset)
        db_session.add(
            PriceBarDailyORM(
                asset_id=asset.id,
                date=extra_date,
                open=1500,
                high=1550,
                low=1450,
                close=1520,
                volume=1000,
            )
        )

    db_session.add(
        AssetDataStatusORM(
            asset_id=asset.id,
            last_available_bar_date=provider_dates[-1],
            last_successful_refresh_at=utc_now() - timedelta(days=1),
            last_refresh_status="success",
            last_refresh_source="alphavantage",
            data_mode="real",
            freshness_status="stale",
        )
    )
    db_session.flush()

    provider = StubProvider("stub", frame=pd.DataFrame(provider_rows))
    result = make_service(db_session, provider).refresh_daily_prices(asset, force=True)

    assert result.status == "refreshed"
    refreshed_count = db_session.query(PriceBarDailyORM).filter_by(asset_id=asset.id).count()
    assert refreshed_count == len(provider_rows)


def test_market_data_service_skips_fmp_after_subscription_restriction(db_session) -> None:
    asset = make_asset(db_session, symbol="ASML")
    db_session.add(
        DataRefreshLogORM(
            asset_id=asset.id,
            provider="fmp",
            started_at=utc_now() - timedelta(hours=2),
            finished_at=utc_now() - timedelta(hours=2),
            status="provider_error",
            rows_inserted=0,
            error_message=(
                "FMP subscription restriction for ASML: Premium Query Parameter: "
                "Special Endpoint not available under your current subscription"
            ),
        )
    )
    db_session.flush()

    fmp_provider = StubProvider("fmp", error=ProviderError("should not be called"))
    alpha_provider = StubProvider(
        "alphavantage",
        frame=pd.DataFrame(
            [
                {
                    "date": date.today() - timedelta(days=1),
                    "open": 100,
                    "high": 101,
                    "low": 99,
                    "close": 100.5,
                    "volume": 1000,
                }
            ]
        ),
    )

    settings = SimpleNamespace(demo_mode=True)
    data_config = SimpleNamespace(
        prefer_cached_data=False,
        refresh_on_app_start=False,
        equities_refresh_interval_hours=24,
        crypto_refresh_interval_minutes=180,
        equities_market_day_rollover_hour_local=21,
        equities_market_day_rollover_minute_local=30,
        max_staleness_days=5,
        allow_demo_fallback=True,
        preserve_real_data_on_provider_failure=True,
        yfinance_enabled=False,
        yfinance_as_fallback=False,
        yfinance_long_history_enabled=False,
        yfinance_long_history_period="5y",
        yfinance_normal_history_period="1y",
        yfinance_long_history_min_rows=1000,
        yfinance_backfill_asset_types=["stock", "etf"],
        yfinance_request_pause_seconds=0.5,
        allow_provider_mixing=True,
        recent_provider_mix_window_days=90,
        providers_priority={"stock": ["fmp", "alphavantage"], "etf": ["fmp"], "crypto": ["stub"]},
    )

    service = MarketDataService(
        db_session,
        settings=settings,
        data_config=data_config,
        providers={"fmp": fmp_provider, "alphavantage": alpha_provider},
    )

    result = service.refresh_daily_prices(asset, force=True)

    assert result.status == "refreshed"
    assert result.provider_name == "alphavantage"
    assert fmp_provider.called == 0
    assert alpha_provider.called == 1


def test_equity_freshness_is_stale_after_cutoff_without_same_day_bar(db_session) -> None:
    make_asset(db_session, asset_type="stock")
    provider = StubProvider("stub")
    service = make_service(db_session, provider)

    current_local = datetime(2026, 3, 30, 23, 0)
    latest_date = date(2026, 3, 27)

    assert service._is_equity_data_fresh(latest_date, current_local) is False


def test_equity_freshness_is_fresh_before_cutoff_with_previous_business_day(db_session) -> None:
    make_asset(db_session, asset_type="stock")
    provider = StubProvider("stub")
    service = make_service(db_session, provider)

    current_local = datetime(2026, 3, 30, 8, 15)
    latest_date = date(2026, 3, 27)

    assert service._is_equity_data_fresh(latest_date, current_local) is True


def test_yfinance_fallback_is_used_when_primary_provider_fails(db_session) -> None:
    asset = make_asset(db_session, symbol="MSFT")
    primary_provider = StubProvider("fmp", error=ProviderError("boom"))
    yfinance_provider = StubProvider(
        "yfinance",
        frame=pd.DataFrame(
            [
                {
                    "date": date.today() - timedelta(days=2),
                    "open": 100,
                    "high": 101,
                    "low": 99,
                    "close": 100.5,
                    "volume": 1000,
                },
                {
                    "date": date.today() - timedelta(days=1),
                    "open": 101,
                    "high": 102,
                    "low": 100,
                    "close": 101.5,
                    "volume": 1200,
                },
            ]
        ),
    )

    settings = SimpleNamespace(demo_mode=True)
    data_config = SimpleNamespace(
        prefer_cached_data=False,
        refresh_on_app_start=False,
        equities_refresh_interval_hours=24,
        crypto_refresh_interval_minutes=180,
        equities_market_day_rollover_hour_local=21,
        equities_market_day_rollover_minute_local=30,
        max_staleness_days=5,
        allow_demo_fallback=True,
        preserve_real_data_on_provider_failure=True,
        yfinance_enabled=True,
        yfinance_as_fallback=True,
        yfinance_long_history_enabled=False,
        yfinance_long_history_period="5y",
        yfinance_normal_history_period="1y",
        yfinance_long_history_min_rows=1000,
        yfinance_backfill_asset_types=["stock", "etf"],
        yfinance_request_pause_seconds=0.5,
        allow_provider_mixing=True,
        recent_provider_mix_window_days=90,
        providers_priority={
            "stock": ["fmp", "yfinance"],
            "etf": ["fmp", "yfinance"],
            "crypto": ["stub"],
        },
    )
    service = MarketDataService(
        db_session,
        settings=settings,
        data_config=data_config,
        providers={"fmp": primary_provider, "yfinance": yfinance_provider},
    )

    result = service.refresh_daily_prices(asset, force=True)

    assert result.status == "refreshed"
    assert result.provider_name == "yfinance"
    assert primary_provider.called == 1
    assert yfinance_provider.called == 1
    latest_row = (
        db_session.query(PriceBarDailyORM)
        .filter_by(asset_id=asset.id)
        .order_by(PriceBarDailyORM.date.desc())
        .first()
    )
    assert latest_row is not None
    assert latest_row.provider == "yfinance"


def test_yfinance_long_history_backfill_inserts_older_rows_without_duplicates(db_session) -> None:
    asset = make_asset(db_session, symbol="MSFT")
    recent_start = date.today() - timedelta(days=119)
    provider_rows = []
    for offset in range(120):
        current_date = recent_start + timedelta(days=offset)
        provider_rows.append(
            {
                "date": current_date,
                "open": 100 + offset,
                "high": 101 + offset,
                "low": 99 + offset,
                "close": 100.5 + offset,
                "volume": 1000 + offset,
            }
        )
    primary_provider = StubProvider("fmp", frame=pd.DataFrame(provider_rows))

    class BackfillProvider(StubProvider):
        def __init__(self) -> None:
            long_rows = []
            long_start = date.today() - timedelta(days=399)
            for offset in range(400):
                current_date = long_start + timedelta(days=offset)
                long_rows.append(
                    {
                        "date": current_date,
                        "open": 50 + offset,
                        "high": 51 + offset,
                        "low": 49 + offset,
                        "close": 50.5 + offset,
                        "volume": 2000 + offset,
                    }
                )
            super().__init__("yfinance", frame=pd.DataFrame(long_rows))

        def fetch_daily_prices_for_history(
            self,
            asset,
            *,
            start_date=None,
            end_date=None,
            period=None,
        ):
            self.called += 1
            return self.frame.copy()

    yfinance_provider = BackfillProvider()
    settings = SimpleNamespace(demo_mode=True)
    data_config = SimpleNamespace(
        prefer_cached_data=False,
        refresh_on_app_start=False,
        equities_refresh_interval_hours=24,
        crypto_refresh_interval_minutes=180,
        equities_market_day_rollover_hour_local=21,
        equities_market_day_rollover_minute_local=30,
        max_staleness_days=5,
        allow_demo_fallback=True,
        preserve_real_data_on_provider_failure=True,
        yfinance_enabled=True,
        yfinance_as_fallback=True,
        yfinance_long_history_enabled=True,
        yfinance_long_history_period="5y",
        yfinance_normal_history_period="1y",
        yfinance_long_history_min_rows=300,
        yfinance_backfill_asset_types=["stock", "etf"],
        yfinance_request_pause_seconds=0.5,
        allow_provider_mixing=True,
        recent_provider_mix_window_days=90,
        providers_priority={
            "stock": ["fmp", "yfinance"],
            "etf": ["fmp", "yfinance"],
            "crypto": ["stub"],
        },
    )
    service = MarketDataService(
        db_session,
        settings=settings,
        data_config=data_config,
        providers={"fmp": primary_provider, "yfinance": yfinance_provider},
    )

    result = service.refresh_daily_prices(asset, force=True)

    assert result.status == "refreshed"
    assert primary_provider.called == 1
    assert yfinance_provider.called == 1
    assert db_session.query(PriceBarDailyORM).filter_by(asset_id=asset.id).count() == 400
    oldest_row = (
        db_session.query(PriceBarDailyORM)
        .filter_by(asset_id=asset.id)
        .order_by(PriceBarDailyORM.date.asc())
        .first()
    )
    newest_row = (
        db_session.query(PriceBarDailyORM)
        .filter_by(asset_id=asset.id)
        .order_by(PriceBarDailyORM.date.desc())
        .first()
    )
    assert oldest_row.provider == "yfinance"
    assert newest_row.provider == "fmp"
    status = db_session.query(AssetDataStatusORM).filter_by(asset_id=asset.id).one()
    assert status.historical_coverage_start == oldest_row.date
    assert status.historical_coverage_end == newest_row.date
    assert status.recent_provider_mix is False
