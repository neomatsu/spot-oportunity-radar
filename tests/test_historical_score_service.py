from __future__ import annotations

from datetime import date, timedelta

from sqlalchemy import select

from data.database import AssetORM, HistoricalScoreSnapshotORM, PriceBarDailyORM
from services.historical_score_service import HistoricalScoreService


def _seed_price_history(db_session, asset_id: int, *, days: int = 320) -> None:
    start = date(2024, 1, 1)
    for offset in range(days):
        current_date = start + timedelta(days=offset)
        base = 100 + (offset * 0.22)
        if offset % 40 in {8, 9, 10}:
            base -= 6.5
        if offset % 55 in {15, 16}:
            base += 4.5
        db_session.add(
            PriceBarDailyORM(
                asset_id=asset_id,
                date=current_date,
                open=base - 0.7,
                high=base + 1.2,
                low=base - 1.6,
                close=base + 0.4,
                volume=1000 + offset,
                provider="test",
                is_adjusted=False,
            )
        )
    db_session.flush()


def test_get_score_as_of_uses_persistent_cache(db_session) -> None:
    asset = AssetORM(
        symbol="CACHE",
        name="Cache Asset",
        asset_type="etf",
        sector="Broad Market",
        region="US",
        enabled=True,
        supports_fundamentals=False,
    )
    db_session.add(asset)
    db_session.flush()
    _seed_price_history(db_session, asset.id)

    service = HistoricalScoreService(db_session)
    as_of_date = date(2024, 11, 15)

    first = service.get_score_as_of(asset, as_of_date=as_of_date)
    assert first is not None
    assert first["date"] == as_of_date
    assert first["technical_score"] >= 0

    cached_rows = list(
        db_session.scalars(
            select(HistoricalScoreSnapshotORM).where(
                HistoricalScoreSnapshotORM.asset_id == asset.id
            )
        )
    )
    assert len(cached_rows) == 1

    def _should_not_run(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("historical score was recomputed instead of using cache")

    service._compute_snapshot = _should_not_run  # type: ignore[method-assign]
    second = service.get_score_as_of(asset, as_of_date=as_of_date)
    assert second is not None
    assert second["final_score"] == first["final_score"]


def test_get_score_history_computes_missing_dates_only_once(db_session) -> None:
    asset = AssetORM(
        symbol="HIST",
        name="History Asset",
        asset_type="stock",
        sector="Technology",
        region="US",
        enabled=True,
        supports_fundamentals=False,
    )
    db_session.add(asset)
    db_session.flush()
    _seed_price_history(db_session, asset.id, days=340)

    service = HistoricalScoreService(db_session)
    start_date = date(2024, 9, 1)
    end_date = date(2024, 10, 31)

    first_frame = service.get_score_history(
        asset,
        start_date=start_date,
        end_date=end_date,
    )
    assert not first_frame.empty
    first_count = len(first_frame)

    def _should_not_run(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("history range recomputed instead of using cached rows")

    service._compute_snapshot = _should_not_run  # type: ignore[method-assign]
    second_frame = service.get_score_history(
        asset,
        start_date=start_date,
        end_date=end_date,
    )
    assert len(second_frame) == first_count
    assert second_frame["date"].min() == start_date
    assert second_frame["date"].max() == end_date
