from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from data.repositories.assets_repo import AssetsRepository
from services.market_data_service import MarketDataService
from services.signal_pipeline_service import SignalPipelineService


@dataclass
class RecommendationRunSummary:
    total_assets: int = 0
    refreshed_assets: int = 0
    cached_assets: int = 0
    preserved_assets: int = 0
    demo_fallback_assets: int = 0
    generated_signals: int = 0
    provider_unavailable_assets: list[str] = field(default_factory=list)
    provider_error_assets: list[str] = field(default_factory=list)
    demo_assets: list[str] = field(default_factory=list)
    signal_assets: list[str] = field(default_factory=list)


class RecommendationFacade:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.assets_repo = AssetsRepository(session)
        self.market_data_service = MarketDataService(session)
        self.signal_pipeline = SignalPipelineService(session)

    def refresh_and_generate_all(self, force: bool = False) -> RecommendationRunSummary:
        summary = RecommendationRunSummary()
        for asset in self.assets_repo.list_enabled():
            summary.total_assets += 1
            refresh_result = self.market_data_service.refresh_daily_prices(asset, force=force)
            if refresh_result.status == "refreshed":
                summary.refreshed_assets += 1
            elif refresh_result.status in {"cache_hit", "provider_unavailable_cached"}:
                summary.cached_assets += 1
            elif refresh_result.status == "preserved_cached_data":
                summary.preserved_assets += 1
            elif refresh_result.status == "demo_fallback":
                summary.demo_fallback_assets += 1
                summary.demo_assets.append(asset.symbol)
            elif refresh_result.status == "provider_unavailable":
                summary.provider_unavailable_assets.append(asset.symbol)
            elif refresh_result.status in {"provider_error", "unexpected_error", "empty_response"}:
                summary.provider_error_assets.append(asset.symbol)

            signal_result = self.signal_pipeline.generate_for_asset(asset)
            if signal_result is not None:
                summary.generated_signals += 1
                summary.signal_assets.append(asset.symbol)
        return summary
