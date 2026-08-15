from __future__ import annotations

from datetime import UTC, date, datetime

import httpx
import numpy as np
import pandas as pd

from data.repositories.assets_repo import AssetsRepository
from data.repositories.prices_repo import PricesRepository
from services.bitcoin_opportunity_service import BitcoinOpportunityService


def _config() -> dict:
    return {
        "bitcoin_symbol": "BTCUSDT",
        "minimum_available_components": 4,
        "weights": {
            "fear_greed": 0.20,
            "rsi_daily": 0.20,
            "ema200": 0.20,
            "liquidity": 0.15,
            "dxy": 0.15,
            "public_interest": 0.10,
        },
        "classification": {
            "exceptional_min": 80,
            "good_min": 65,
            "neutral_min": 45,
            "caution_min": 30,
        },
        "fear_greed": {
            "url": "https://fear.test/fng",
            "opportunity_center": 70,
            "points_per_score": 6,
        },
        "rsi_daily": {"opportunity_center": 70, "points_per_score": 4},
        "ema200": {
            "thresholds": [
                {"max_distance_pct": -25, "score": 10},
                {"max_distance_pct": -10, "score": 8},
                {"max_distance_pct": 0, "score": 5},
                {"max_distance_pct": 10, "score": 2},
                {"max_distance_pct": 1_000_000, "score": 0},
            ]
        },
        "liquidity": {
            "recent_days": 7,
            "baseline_days": 30,
            "neutral_score": 5,
            "score_sensitivity": 0.10,
        },
        "dxy": {
            "lookback_sessions": 20,
            "neutral_score": 5,
            "score_per_pct_decline": 2.5,
        },
        "public_interest": {
            "url_template": "https://views.test/{start}/{end}",
            "history_days": 365,
            "recent_days": 7,
            "user_agent": "test",
        },
        "http": {"timeout_seconds": 5},
    }


def _seed_prices(session) -> None:
    asset = AssetsRepository(session).upsert_asset(
        symbol="BTCUSDT",
        name="Bitcoin",
        asset_type="crypto",
        sector="Crypto",
        region="Global",
        enabled=True,
        supports_fundamentals=False,
    )
    rows = 320
    close = np.linspace(70_000, 60_000, rows) + np.sin(np.arange(rows) / 10) * 1_000
    frame = pd.DataFrame(
        {
            "date": pd.date_range("2025-01-01", periods=rows, freq="D"),
            "open": close + 100,
            "high": close + 500,
            "low": close - 500,
            "close": close,
            "volume": np.concatenate([np.full(rows - 7, 1_000), np.full(7, 1_300)]),
        }
    )
    PricesRepository(session).upsert_asset_prices(asset.id, frame, provider_name="test")


def _client() -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "fear.test":
            rows = [
                {"value": "28", "value_classification": "Fear", "timestamp": "1750000000"}
                for _ in range(7)
            ]
            return httpx.Response(200, json={"data": rows})
        items = [{"views": 100 + index} for index in range(365)]
        items[-7:] = [{"views": 110} for _ in range(7)]
        return httpx.Response(200, json={"items": items})

    return httpx.Client(transport=httpx.MockTransport(handler))


def _dxy_frame() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=60, freq="B"),
            "close": np.linspace(105, 100, 60),
        }
    )


def test_computes_six_component_report(db_session) -> None:
    _seed_prices(db_session)
    now = datetime(2026, 8, 14, tzinfo=UTC)
    with _client() as client:
        report = BitcoinOpportunityService(
            db_session,
            config=_config(),
            http_client=client,
            dxy_loader=_dxy_frame,
            now=now,
        ).compute()

    assert report.available_components == 6
    assert report.actionable is True
    assert report.score is not None and 0 <= report.score <= 100
    assert len(report.components) == 6
    assert set(component.key for component in report.components) == set(_config()["weights"])
    assert not report.price_history.empty


def test_external_failures_do_not_break_local_components(db_session) -> None:
    _seed_prices(db_session)

    def failing_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "offline"})

    def failing_dxy() -> pd.DataFrame:
        raise RuntimeError("DXY offline")

    with httpx.Client(transport=httpx.MockTransport(failing_handler)) as client:
        report = BitcoinOpportunityService(
            db_session,
            config=_config(),
            http_client=client,
            dxy_loader=failing_dxy,
        ).compute()

    assert report.available_components == 3
    assert report.actionable is False
    assert report.classification == "DATOS_INSUFICIENTES"
    assert sum(component.error is not None for component in report.components) == 3


def test_contrarian_components_reward_fear_and_falling_dollar(db_session) -> None:
    service = BitcoinOpportunityService(db_session, config=_config())
    fearful = service._clip((70 - 20) / 6)
    greedy = service._clip((70 - 80) / 6)

    assert fearful > greedy
    falling_dxy_score = service._clip(5 - (-2.0 * 2.5))
    rising_dxy_score = service._clip(5 - (2.0 * 2.5))
    assert falling_dxy_score > rising_dxy_score


def test_history_is_persisted_and_reused_without_external_calls(db_session) -> None:
    _seed_prices(db_session)
    history_start = date(2025, 8, 1)
    history_end = date(2025, 11, 15)

    fear_dates = pd.date_range("2025-01-01", "2025-11-15", freq="D")
    fear_rows = [
        {
            "value": str(25 + index % 30),
            "timestamp": str(int(timestamp.timestamp())),
        }
        for index, timestamp in enumerate(fear_dates)
    ]
    view_dates = pd.date_range("2024-01-01", "2025-11-15", freq="D")
    view_rows = [
        {"timestamp": timestamp.strftime("%Y%m%d00"), "views": 100 + index % 50}
        for index, timestamp in enumerate(view_dates)
    ]

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "fear.test":
            return httpx.Response(200, json={"data": fear_rows})
        return httpx.Response(200, json={"items": view_rows})

    def dxy_history_loader(start: date, end: date) -> pd.DataFrame:
        dates = pd.date_range(start - pd.Timedelta(days=60), end, freq="B")
        return pd.DataFrame({"date": dates, "close": np.linspace(105, 100, len(dates))})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        first = BitcoinOpportunityService(
            db_session,
            config=_config(),
            http_client=client,
            dxy_history_loader=dxy_history_loader,
            now=datetime(2025, 11, 16, tzinfo=UTC),
        ).update_history(history_start, history_end)

    assert not first.empty
    assert first["overall_score"].notna().all()
    assert first["bitcoin_price"].notna().all()
    assert first["components_json"].map(lambda value: "fear_greed" in value).all()

    def should_not_call(*args, **kwargs):
        raise AssertionError("External provider was called despite complete SQLite cache")

    second = BitcoinOpportunityService(
        db_session,
        config=_config(),
        dxy_history_loader=should_not_call,
    )
    second._get_json = should_not_call  # type: ignore[method-assign]
    cached = second.update_history(history_start, history_end)

    assert len(cached) == len(first)
    assert cached["overall_score"].tolist() == first["overall_score"].tolist()
