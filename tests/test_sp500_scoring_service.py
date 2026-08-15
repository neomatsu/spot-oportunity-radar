from __future__ import annotations

from datetime import date
from pathlib import Path

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
