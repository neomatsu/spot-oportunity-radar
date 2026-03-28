from __future__ import annotations

from datetime import date

from data.database import AssetORM, PriceBarDailyORM, SignalORM, TechnicalSnapshotORM
from services.watchlist_service import WatchlistService


def test_watchlist_service_returns_latest_signal_and_snapshot(db_session) -> None:
    asset = AssetORM(
        symbol="BTCUSDT",
        name="Bitcoin",
        asset_type="crypto",
        sector="Crypto",
        region="Global",
        enabled=True,
        supports_fundamentals=False,
    )
    db_session.add(asset)
    db_session.flush()

    db_session.add(
        PriceBarDailyORM(
            asset_id=asset.id,
            date=date(2026, 3, 25),
            open=86_000,
            high=88_000,
            low=84_000,
            close=87_500,
            volume=1234,
        )
    )
    db_session.add_all(
        [
            TechnicalSnapshotORM(
                asset_id=asset.id,
                date=date(2026, 3, 24),
                rsi14=48,
                sma50=80_000,
                sma200=70_000,
                ema20=84_000,
                atr14=2_500,
                support_low=82_000,
                support_high=84_000,
                distance_to_support_pct=4,
                technical_score=61,
                rationale_json={"version": 1},
            ),
            TechnicalSnapshotORM(
                asset_id=asset.id,
                date=date(2026, 3, 25),
                rsi14=44,
                sma50=81_000,
                sma200=71_000,
                ema20=85_000,
                atr14=2_400,
                support_low=83_000,
                support_high=85_000,
                distance_to_support_pct=2.9,
                technical_score=72,
                rationale_json={"version": 2},
            ),
        ]
    )
    db_session.add_all(
        [
            SignalORM(
                asset_id=asset.id,
                date=date(2026, 3, 24),
                final_score=60,
                recommendation="WATCH",
                suggested_buy_low=82_000,
                suggested_buy_high=84_000,
                suggested_weight_add=2,
                risk_score=55,
                rationale_json={"portfolio_fit_score": 58},
            ),
            SignalORM(
                asset_id=asset.id,
                date=date(2026, 3, 25),
                final_score=74,
                recommendation="BUY_CANDIDATE",
                suggested_buy_low=83_000,
                suggested_buy_high=85_000,
                suggested_weight_add=4,
                risk_score=42,
                rationale_json={"portfolio_fit_score": 77},
            ),
        ]
    )
    db_session.flush()

    rows = WatchlistService(db_session).get_watchlist_rows()

    assert len(rows) == 1
    row = rows[0]
    assert row["symbol"] == "BTCUSDT"
    assert row["technical_score"] == 72
    assert row["final_opportunity_score"] == 74
    assert row["recommendation"] == "BUY_CANDIDATE"
    assert row["portfolio_fit_score"] == 77
