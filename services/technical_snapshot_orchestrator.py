from __future__ import annotations

from datetime import date

from sqlalchemy.orm import Session

from core.models import TechnicalSnapshotModel
from data.database import AssetORM
from data.repositories.signals_repo import TechnicalSnapshotsRepository
from services.market_data_service import MarketDataService
from services.scoring_service import ScoringService
from services.support_detection_service import SupportDetectionService
from services.technical_service import TechnicalService


class TechnicalSnapshotOrchestrator:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.market_data = MarketDataService(session)
        self.repo = TechnicalSnapshotsRepository(session)
        self.technical = TechnicalService()
        self.support_detection = SupportDetectionService()
        self.scoring = ScoringService()

    def refresh_for_asset(self, asset: AssetORM) -> TechnicalSnapshotModel | None:
        frame = self.market_data.load_price_frame(asset.id, min_rows=30)
        if frame.empty:
            return None

        enriched = self.technical.compute_indicators(frame)
        latest = enriched.iloc[-1]
        support = self.support_detection.detect_support_zone(enriched)
        trend_score, trend_rationale = self.technical.trend_structure_score(latest)
        technical_score, scoring_rationale = self.scoring.compute_technical_score(
            latest,
            distance_to_support_pct=support.distance_to_support_pct,
            trend_score=trend_score,
        )
        rationale = {**trend_rationale, **scoring_rationale}
        rationale.update(
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

        snapshot = TechnicalSnapshotModel(
            asset_id=asset.id,
            date=date.today(),
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
            rationale=rationale,
        )
        self.repo.upsert_snapshot(
            {
                "asset_id": snapshot.asset_id,
                "date": snapshot.date,
                "rsi14": snapshot.rsi14,
                "sma50": snapshot.sma50,
                "sma200": snapshot.sma200,
                "ema20": snapshot.ema20,
                "atr14": snapshot.atr14,
                "support_low": snapshot.support_low,
                "support_high": snapshot.support_high,
                "distance_to_support_pct": snapshot.distance_to_support_pct,
                "technical_score": snapshot.technical_score,
                "rationale_json": snapshot.rationale,
            }
        )
        return snapshot

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
