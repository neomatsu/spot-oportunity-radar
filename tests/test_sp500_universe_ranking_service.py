from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import Mock

import pandas as pd

from services.sp500_scoring_service import (
    SP500IncrementalRefreshResult,
    SP500ScoringResult,
)
from services.sp500_universe_ranking_service import SP500UniverseRankingService


def _config(tmp_path: Path) -> dict:
    return {
        "snapshot": {
            "ranking_file": str(tmp_path / "ranking.csv"),
            "errors_file": str(tmp_path / "errors.csv"),
            "metadata_file": str(tmp_path / "metadata.json"),
        }
    }


def _ranking() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "symbol": "MSFT",
                "company": "Microsoft",
                "sector": "Technology",
                "recommendation": "BUY_CANDIDATE",
                "dominant_regime": "BULL",
                "final_score": 75.0,
                "technical_score": 72.0,
                "risk_score": 30.0,
            },
            {
                "symbol": "KO",
                "company": "Coca-Cola",
                "sector": "Consumer Staples",
                "recommendation": "WATCH",
                "dominant_regime": "TRANSITION",
                "final_score": 58.0,
                "technical_score": 60.0,
                "risk_score": 42.0,
            },
        ]
    )


def test_refresh_persists_incremental_ranking_snapshot(tmp_path: Path) -> None:
    scoring = Mock()
    scoring.refresh_price_cache_incremental.return_value = SP500IncrementalRefreshResult(
        target_session=date(2026, 8, 19),
        symbols_total=503,
        symbols_current=500,
        symbols_refreshed=2,
        symbols_failed=1,
    )
    scoring.run_cached.return_value = SP500ScoringResult(
        ranking=_ranking(),
        errors=pd.DataFrame([{"symbol": "EA", "error": "missing"}]),
        metadata={"finished_at_utc": "2026-08-20T12:00:00+00:00"},
    )
    service = SP500UniverseRankingService(scoring_service=scoring, config=_config(tmp_path))

    refreshed = service.refresh()
    loaded = service.load()

    assert len(refreshed.ranking) == 2
    assert loaded.ranking["symbol"].tolist() == ["MSFT", "KO"]
    assert loaded.errors["symbol"].tolist() == ["EA"]
    assert loaded.metadata["incremental_refresh"]["symbols_refreshed"] == 2
    scoring.refresh_price_cache_incremental.assert_called_once_with()
    scoring.run_cached.assert_called_once_with(refresh_constituents=False)


def test_filters_ranking_by_query_quality_and_context() -> None:
    filtered = SP500UniverseRankingService.filter_ranking(
        _ranking(),
        query="micro",
        sectors=["Technology"],
        recommendations=["BUY_CANDIDATE"],
        regimes=["BULL"],
        min_final_score=70,
        max_risk_score=35,
    )

    assert filtered["symbol"].tolist() == ["MSFT"]


def test_filter_treats_search_as_plain_text() -> None:
    filtered = SP500UniverseRankingService.filter_ranking(_ranking(), query="[")

    assert filtered.empty


def test_load_returns_empty_snapshot_when_files_do_not_exist(tmp_path: Path) -> None:
    service = SP500UniverseRankingService(scoring_service=Mock(), config=_config(tmp_path))

    result = service.load()

    assert result.ranking.empty
    assert result.errors.empty
    assert result.metadata == {}
