from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from core.models import PortfolioExposureModel, TechnicalSnapshotModel
from data.database import AssetORM
from data.repositories.historical_scores_repo import HistoricalScoresRepository
from data.repositories.prices_repo import PricesRepository
from services.rebalance_service import RebalanceService
from services.recommendation_service import RecommendationService
from services.risk_service import RiskService
from services.scoring_service import ScoringService
from services.support_detection_service import SupportDetectionService
from services.technical_service import TechnicalService


class HistoricalScoreService:
    SOURCE_VERSION = "historical_scores_v2"
    PORTFOLIO_CONTEXT = "neutral"

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = HistoricalScoresRepository(session)
        self.prices_repo = PricesRepository(session)
        self.technical_service = TechnicalService()
        self.support_detection = SupportDetectionService()
        self.scoring_service = ScoringService()
        self.risk_service = RiskService()
        self.rebalance_service = RebalanceService()
        self.recommendation_service = RecommendationService()

    def get_score_as_of(
        self,
        asset: AssetORM,
        *,
        as_of_date: date,
        use_cache: bool = True,
        force: bool = False,
    ) -> dict[str, Any] | None:
        if use_cache and not force:
            cached = self.repo.get_snapshot(
                asset_id=asset.id,
                snapshot_date=as_of_date,
                source_version=self.SOURCE_VERSION,
                portfolio_context=self.PORTFOLIO_CONTEXT,
            )
            if cached is not None:
                return self._serialize_cached_snapshot(cached)

        enriched = self._load_enriched_frame(asset.id)
        if enriched.empty:
            return None

        day_frame = enriched[enriched["date"].dt.date <= as_of_date].copy()
        if day_frame.empty:
            return None

        computed = self._compute_snapshot(asset, day_frame, as_of_date=as_of_date)
        self.repo.upsert_snapshot(computed)
        return self._serialize_payload(computed)

    def get_score_history(
        self,
        asset: AssetORM,
        *,
        start_date: date | None = None,
        end_date: date | None = None,
        use_cache: bool = True,
        force: bool = False,
    ) -> pd.DataFrame:
        enriched = self._load_enriched_frame(asset.id)
        if enriched.empty:
            return pd.DataFrame()

        if start_date is None:
            start_date = enriched["date"].min().date()
        if end_date is None:
            end_date = enriched["date"].max().date()

        target_dates = [
            current_date
            for current_date in enriched["date"].dt.date.tolist()
            if start_date <= current_date <= end_date
        ]
        if not target_dates:
            return pd.DataFrame()

        cached_rows = []
        cached_dates: set[date] = set()
        if use_cache and not force:
            cached_rows = self.repo.list_snapshots(
                asset_id=asset.id,
                start_date=start_date,
                end_date=end_date,
                source_version=self.SOURCE_VERSION,
                portfolio_context=self.PORTFOLIO_CONTEXT,
            )
            cached_dates = {row.date for row in cached_rows}

        missing_dates = [
            current_date for current_date in target_dates if current_date not in cached_dates
        ]
        if missing_dates:
            for current_date in missing_dates:
                day_frame = enriched[enriched["date"].dt.date <= current_date].copy()
                if day_frame.empty:
                    continue
                computed = self._compute_snapshot(asset, day_frame, as_of_date=current_date)
                self.repo.upsert_snapshot(computed)

            cached_rows = self.repo.list_snapshots(
                asset_id=asset.id,
                start_date=start_date,
                end_date=end_date,
                source_version=self.SOURCE_VERSION,
                portfolio_context=self.PORTFOLIO_CONTEXT,
            )

        serialized = [self._serialize_cached_snapshot(row) for row in cached_rows]
        return pd.DataFrame(serialized)

    def _compute_snapshot(
        self,
        asset: AssetORM,
        frame_as_of: pd.DataFrame,
        *,
        as_of_date: date,
    ) -> dict[str, Any]:
        latest = frame_as_of.iloc[-1]
        support = self.support_detection.detect_support_zone(frame_as_of)
        trend_score, trend_rationale = self.technical_service.trend_structure_score(latest)
        technical_score, scoring_rationale = self.scoring_service.compute_technical_score(
            latest,
            distance_to_support_pct=support.distance_to_support_pct,
            trend_score=trend_score,
        )
        technical_rationale = {**trend_rationale, **scoring_rationale}
        technical_rationale.update(
            {
                "support_detection_method": support.method,
                "support_zones": support.support_zones,
                "resistance_zones": support.resistance_zones,
                "nearest_support_zone": support.nearest_support_zone,
                "major_support_zone": support.major_support_zone,
                "structural_support_zone": support.structural_support_zone,
                "nearest_resistance_zone": support.nearest_resistance_zone,
                "major_resistance_zone": support.major_resistance_zone,
                "structural_resistance_zone": support.structural_resistance_zone,
            }
        )

        technical_snapshot = TechnicalSnapshotModel(
            asset_id=asset.id,
            date=as_of_date,
            rsi14=self._to_optional_float(latest.get("rsi14")),
            sma50=self._to_optional_float(latest.get("sma50")),
            sma200=self._to_optional_float(latest.get("sma200")),
            ema20=self._to_optional_float(latest.get("ema20")),
            atr14=self._to_optional_float(latest.get("atr14")),
            week_52_low=self._to_optional_float(latest.get("week_52_low")),
            week_52_high=self._to_optional_float(latest.get("week_52_high")),
            week_52_position=self._to_optional_float(
                scoring_rationale.get("week_52_position")
            ),
            distance_52w_high_pct=self._to_optional_float(latest.get("distance_52w_high_pct")),
            distance_52w_low_pct=self._to_optional_float(latest.get("distance_52w_low_pct")),
            support_low=support.support_zone_low,
            support_high=support.support_zone_high,
            distance_to_support_pct=support.distance_to_support_pct,
            technical_score=technical_score,
            rationale=technical_rationale,
        )

        exposure = self._neutral_exposure()
        portfolio_fit_score, portfolio_rationale = self.rebalance_service.portfolio_fit_score(
            asset,
            exposure,
        )
        risk = self.risk_service.assess_risk(
            frame_as_of,
            asset_type=asset.asset_type,
            current_asset_weight=0.0,
            current_sector_weight=0.0,
            current_asset_type_weight=0.0,
        )
        score_breakdown = self.scoring_service.compute_final_score_details(
            technical_score=technical_score,
            risk_score=risk.risk_score,
            portfolio_fit_score=portfolio_fit_score,
        )
        recommendation = self.recommendation_service.build_recommendation(
            asset_id=asset.id,
            as_of_date=as_of_date,
            technical_snapshot=technical_snapshot,
            risk=risk,
            final_score=float(score_breakdown["final"]),
            portfolio_fit_score=portfolio_fit_score,
            score_breakdown=score_breakdown,
        )

        signal_payload = {
            **recommendation.rationale_json,
            "portfolio_rationale": portfolio_rationale,
            "technical_rationale": technical_snapshot.rationale,
            "risk_rationale": risk.rationale,
            "score_breakdown": score_breakdown,
            "risk_level": recommendation.risk_level.value,
            "invalidation": recommendation.invalidation,
            "portfolio_context": self.PORTFOLIO_CONTEXT,
        }

        return {
            "asset_id": asset.id,
            "date": as_of_date,
            "technical_score": float(technical_score),
            "risk_score": float(risk.risk_score),
            "portfolio_fit_score": float(portfolio_fit_score),
            "final_score": float(score_breakdown["final"]),
            "recommendation": recommendation.recommendation.value,
            "support_low": technical_snapshot.support_low,
            "support_high": technical_snapshot.support_high,
            "distance_to_support_pct": technical_snapshot.distance_to_support_pct,
            "technical_payload_json": {
                "rsi14": technical_snapshot.rsi14,
                "sma50": technical_snapshot.sma50,
                "sma200": technical_snapshot.sma200,
                "ema20": technical_snapshot.ema20,
                "atr14": technical_snapshot.atr14,
                "week_52_low": technical_snapshot.week_52_low,
                "week_52_high": technical_snapshot.week_52_high,
                "week_52_position": technical_snapshot.week_52_position,
                "support_low": technical_snapshot.support_low,
                "support_high": technical_snapshot.support_high,
                "distance_to_support_pct": technical_snapshot.distance_to_support_pct,
                "technical_score": technical_snapshot.technical_score,
                "breakdown": technical_snapshot.rationale.get("breakdown", {}),
                "reasons": technical_snapshot.rationale.get("reasons", []),
                "rationale": technical_snapshot.rationale,
            },
            "signal_payload_json": {
                "recommendation": recommendation.recommendation.value,
                "final_score": recommendation.final_score,
                "suggested_buy_low": recommendation.suggested_buy_low,
                "suggested_buy_high": recommendation.suggested_buy_high,
                "suggested_weight_add": recommendation.suggested_weight_add,
                "risk_score": recommendation.risk_score,
                "rationale": signal_payload,
            },
            "computed_at": datetime.now(UTC).replace(tzinfo=None),
            "source_version": self.SOURCE_VERSION,
            "portfolio_context": self.PORTFOLIO_CONTEXT,
        }

    def _load_enriched_frame(self, asset_id: int) -> pd.DataFrame:
        frame = self.prices_repo.get_asset_prices(asset_id)
        if frame.empty:
            return frame
        working = frame.copy()
        working["date"] = pd.to_datetime(working["date"])
        working = working.sort_values("date").reset_index(drop=True)
        return self.technical_service.compute_indicators(working)

    @staticmethod
    def _neutral_exposure() -> PortfolioExposureModel:
        return PortfolioExposureModel(
            total_invested_weight=0.0,
            by_asset={},
            by_sector={},
            by_asset_type={},
        )

    @staticmethod
    def _to_optional_float(value: object) -> float | None:
        if value is None:
            return None
        try:
            if value != value:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _serialize_cached_snapshot(entity: Any) -> dict[str, Any]:
        return {
            "asset_id": entity.asset_id,
            "date": entity.date,
            "technical_score": entity.technical_score,
            "risk_score": entity.risk_score,
            "portfolio_fit_score": entity.portfolio_fit_score,
            "final_score": entity.final_score,
            "recommendation": entity.recommendation,
            "support_low": entity.support_low,
            "support_high": entity.support_high,
            "distance_to_support_pct": entity.distance_to_support_pct,
            "technical_payload_json": entity.technical_payload_json,
            "signal_payload_json": entity.signal_payload_json,
            "computed_at": entity.computed_at,
            "source_version": entity.source_version,
            "portfolio_context": entity.portfolio_context,
        }

    @staticmethod
    def _serialize_payload(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            **payload,
            "date": payload["date"],
        }
