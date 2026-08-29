from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pandas as pd

from core.config import get_settings, load_yaml_config
from services.sp500_scoring_service import SP500ScoringResult, SP500ScoringService


class SP500UniverseRankingService:
    """Maintains a consultative, cached ranking of the current S&P 500 universe."""

    def __init__(
        self,
        scoring_service: SP500ScoringService | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        self.config = config or load_yaml_config("sp500_scoring.yaml")
        self.scoring = scoring_service or SP500ScoringService(config=self.config)
        snapshot_cfg = self.config.get("snapshot", {})
        root = get_settings().root_dir
        self.ranking_path = self._path(root, snapshot_cfg.get("ranking_file"))
        self.errors_path = self._path(root, snapshot_cfg.get("errors_file"))
        self.metadata_path = self._path(root, snapshot_cfg.get("metadata_file"))

    def refresh(self, *, refresh_constituents: bool = False) -> SP500ScoringResult:
        refresh = self.scoring.refresh_price_cache_incremental()
        result = self.scoring.run_cached(refresh_constituents=refresh_constituents)
        result.metadata["incremental_refresh"] = asdict(refresh)
        self.save(result)
        return result

    def load(self) -> SP500ScoringResult:
        ranking = self._read_csv(self.ranking_path)
        errors = self._read_csv(self.errors_path)
        metadata: dict[str, Any] = {}
        if self.metadata_path.exists():
            metadata = json.loads(self.metadata_path.read_text(encoding="utf-8"))
        return SP500ScoringResult(ranking=ranking, errors=errors, metadata=metadata)

    def save(self, result: SP500ScoringResult) -> None:
        self._write_csv(self.ranking_path, result.ranking)
        self._write_csv(self.errors_path, result.errors)
        self.metadata_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.metadata_path.with_suffix(self.metadata_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(result.metadata, ensure_ascii=True, indent=2, default=str),
            encoding="utf-8",
        )
        temporary.replace(self.metadata_path)

    @staticmethod
    def filter_ranking(
        ranking: pd.DataFrame,
        *,
        query: str = "",
        sectors: list[str] | None = None,
        recommendations: list[str] | None = None,
        regimes: list[str] | None = None,
        min_final_score: float = 0.0,
        max_risk_score: float = 100.0,
    ) -> pd.DataFrame:
        if ranking.empty:
            return ranking.copy()
        result = ranking.copy()
        needle = query.strip().casefold()
        if needle:
            symbols = result["symbol"].fillna("").astype(str).str.casefold()
            companies = result["company"].fillna("").astype(str).str.casefold()
            result = result[
                symbols.str.contains(needle, regex=False)
                | companies.str.contains(needle, regex=False)
            ]
        if sectors:
            result = result[result["sector"].isin(sectors)]
        if recommendations:
            result = result[result["recommendation"].isin(recommendations)]
        if regimes:
            result = result[result["dominant_regime"].isin(regimes)]
        result = result[
            (pd.to_numeric(result["final_score"], errors="coerce") >= min_final_score)
            & (pd.to_numeric(result["risk_score"], errors="coerce") <= max_risk_score)
        ]
        return result.sort_values(
            ["final_score", "technical_score"], ascending=[False, False]
        ).reset_index(drop=True)

    @staticmethod
    def _path(root: Path, configured: Any) -> Path:
        value = str(configured or "cache/sp500_scoring/ranking_latest.csv")
        path = Path(value)
        return path if path.is_absolute() else root / path

    @staticmethod
    def _read_csv(path: Path) -> pd.DataFrame:
        if not path.exists():
            return pd.DataFrame()
        try:
            return pd.read_csv(path)
        except pd.errors.EmptyDataError:
            return pd.DataFrame()

    @staticmethod
    def _write_csv(path: Path, frame: pd.DataFrame) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        frame.to_csv(temporary, index=False)
        temporary.replace(path)
