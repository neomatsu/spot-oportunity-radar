from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path
from unittest.mock import Mock

import numpy as np
import pandas as pd

from services.sp500_scoring_service import SP500ScoringResult, SP500ScoringService


def _price_frame(rows: int = 300) -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-02", periods=rows)
    close = np.linspace(100, 135, rows) + np.sin(np.arange(rows) / 8) * 3
    return pd.DataFrame(
        {
            "date": dates,
            "open": close - 0.5,
            "high": close + 1.5,
            "low": close - 1.5,
            "close": close,
            "volume": np.full(rows, 1_000_000.0),
        }
    )


def _service(tmp_path: Path) -> SP500ScoringService:
    return SP500ScoringService(
        {
            "constituents": {
                "source_url": "https://example.invalid/constituents.csv",
                "cache_file": str(tmp_path / "constituents.csv"),
            },
            "market_data": {
                "cache_dir": str(tmp_path / "prices"),
                "cache_max_age_hours": 24,
                "min_history_rows": 220,
            },
            "scoring": {"include_market_regime": True},
            "output": {"directory": str(tmp_path)},
        }
    )


def test_normalizes_share_class_symbols_for_yahoo(tmp_path: Path) -> None:
    service = _service(tmp_path)
    frame = pd.DataFrame(
        {
            "Symbol": ["BRK.B", "MSFT"],
            "Security": ["Berkshire Hathaway", "Microsoft"],
            "GICS Sector": ["Financials", "Information Technology"],
            "GICS Sub-Industry": ["Multi-Sector Holdings", "Systems Software"],
        }
    )

    normalized = service._normalize_constituents(frame)

    assert normalized["yahoo_symbol"].tolist() == ["BRK-B", "MSFT"]


def test_scores_external_asset_without_database_session(tmp_path: Path) -> None:
    service = _service(tmp_path)
    constituent = pd.Series(
        {
            "symbol": "TEST",
            "yahoo_symbol": "TEST",
            "name": "Test Company",
            "sector": "Industrials",
            "sub_industry": "Machinery",
        }
    )

    row = service.score_asset(constituent, _price_frame(), synthetic_id=-1)

    assert 0 <= row["technical_score"] <= 100
    assert 0 <= row["risk_score"] <= 100
    assert 0 <= row["final_score"] <= 100
    assert row["portfolio_fit_score"] == 93
    assert row["recommendation"] in {"BUY_CANDIDATE", "WATCH", "AVOID"}
    assert row["data_provider"] == "yfinance"


def test_price_cache_is_reused_without_downloading(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service._write_price_cache("MSFT", _price_frame())

    frames, errors, hits = service.load_price_history(
        ["MSFT"], force=False, period="5y", batch_size=10
    )

    assert hits == 1
    assert errors == []
    assert len(frames["MSFT"]) == 300


def test_incremental_refresh_only_downloads_stale_symbols(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.now = datetime(2026, 8, 20, 12, tzinfo=UTC)
    constituents = pd.DataFrame(
        {
            "Symbol": ["MSFT", "AAPL"],
            "Security": ["Microsoft", "Apple"],
            "GICS Sector": ["Technology", "Technology"],
        }
    )
    constituents.to_csv(tmp_path / "constituents.csv", index=False)
    stale = _price_frame(220)
    stale["date"] = pd.bdate_range(end="2026-08-18", periods=len(stale))
    current = stale.copy()
    current.loc[current.index[-1], "date"] = pd.Timestamp("2026-08-19")
    service._write_price_cache("MSFT", stale)
    service._write_price_cache("AAPL", current)
    recent = stale.tail(1).copy()
    recent["date"] = pd.Timestamp("2026-08-19")
    service._download_batch_range = Mock(return_value={"MSFT": recent})

    result = service.refresh_price_cache_incremental()

    assert result.target_session == date(2026, 8, 19)
    assert result.symbols_total == 2
    assert result.symbols_current == 1
    assert result.symbols_refreshed == 1
    assert result.symbols_failed == 0
    assert service._read_price_cache_unchecked("MSFT")["date"].max().date() == date(
        2026, 8, 19
    )
    service._download_batch_range.assert_called_once()


def test_incremental_refresh_bootstraps_symbols_without_cache(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.now = datetime(2026, 8, 20, 12, tzinfo=UTC)
    pd.DataFrame(
        {
            "Symbol": ["MSFT"],
            "Security": ["Microsoft"],
            "GICS Sector": ["Technology"],
        }
    ).to_csv(tmp_path / "constituents.csv", index=False)
    history = _price_frame(300)
    history.loc[history.index[-1], "date"] = pd.Timestamp("2026-08-19")
    service._download_batch = Mock(return_value={"MSFT": history})
    service._download_batch_range = Mock()

    result = service.refresh_price_cache_incremental()

    assert result.symbols_refreshed == 1
    assert result.symbols_failed == 0
    assert len(service._read_price_cache_unchecked("MSFT")) == 300
    service._download_batch.assert_called_once_with(["MSFT"], period="2y")
    service._download_batch_range.assert_not_called()


def test_incremental_refresh_rebuilds_cache_with_insufficient_history(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.now = datetime(2026, 8, 20, 12, tzinfo=UTC)
    pd.DataFrame(
        {
            "Symbol": ["NEW"],
            "Security": ["New Company"],
            "GICS Sector": ["Industrials"],
        }
    ).to_csv(tmp_path / "constituents.csv", index=False)
    service._write_price_cache("NEW", _price_frame(20))
    history = _price_frame(300)
    service._download_batch = Mock(return_value={"NEW": history})
    service._download_batch_range = Mock()

    result = service.refresh_price_cache_incremental()

    assert result.symbols_refreshed == 1
    assert len(service._read_price_cache_unchecked("NEW")) == 300
    service._download_batch.assert_called_once_with(["NEW"], period="2y")
    service._download_batch_range.assert_not_called()


def test_run_cached_scores_without_downloading(tmp_path: Path) -> None:
    service = _service(tmp_path)
    pd.DataFrame(
        {
            "Symbol": ["MSFT"],
            "Security": ["Microsoft"],
            "GICS Sector": ["Technology"],
        }
    ).to_csv(tmp_path / "constituents.csv", index=False)
    service._write_price_cache("MSFT", _price_frame())
    service._download_batch = Mock()
    service._download_batch_range = Mock()

    result = service.run_cached()

    assert result.ranking["symbol"].tolist() == ["MSFT"]
    assert result.metadata["price_cache_hits"] == 1
    service._download_batch.assert_not_called()
    service._download_batch_range.assert_not_called()


def test_exports_ranking_errors_and_metadata(tmp_path: Path) -> None:
    service = _service(tmp_path)
    result = SP500ScoringResult(
        ranking=pd.DataFrame(
            [
                {
                    "rank": 1,
                    "symbol": "MSFT",
                    "technical_score": 70,
                    "risk_score": 30,
                    "final_score": 72,
                }
            ]
        ),
        errors=pd.DataFrame([{"symbol": "BAD", "error": "missing"}]),
        metadata={"assets_scored": 1, "as_of": date(2026, 7, 20)},
    )
    output = tmp_path / "study.xlsx"

    exported = service.export_excel(result, output_path=output)

    assert exported == output
    assert output.exists()
    workbook = pd.ExcelFile(output)
    assert workbook.sheet_names == ["Ranking", "Errors", "Metadata"]
