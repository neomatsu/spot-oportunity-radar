from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field

from core.enums import (
    AssetType,
    DataMode,
    FreshnessStatus,
    RecommendationStatus,
    RiskLevel,
)


class AssetConfigModel(BaseModel):
    symbol: str
    name: str
    asset_type: AssetType
    sector: str
    region: str
    enabled: bool = True
    supports_fundamentals: bool = False


class AppAssetList(BaseModel):
    assets: list[AssetConfigModel]


class PriceBarModel(BaseModel):
    date: date
    open: float
    high: float
    low: float
    close: float
    volume: float


class SupportZoneModel(BaseModel):
    support_zone_low: float | None = None
    support_zone_high: float | None = None
    distance_to_support_pct: float | None = None


class TechnicalSnapshotModel(BaseModel):
    asset_id: int
    date: date
    rsi14: float | None = None
    sma50: float | None = None
    sma200: float | None = None
    ema20: float | None = None
    atr14: float | None = None
    distance_52w_high_pct: float | None = None
    distance_52w_low_pct: float | None = None
    support_low: float | None = None
    support_high: float | None = None
    distance_to_support_pct: float | None = None
    technical_score: float | None = None
    rationale: dict[str, Any] = Field(default_factory=dict)


class FundamentalSnapshotModel(BaseModel):
    asset_id: int
    date: date
    revenue_growth: float | None = None
    eps_growth: float | None = None
    debt_to_ebitda: float | None = None
    fcf_margin: float | None = None
    pe: float | None = None
    ps: float | None = None
    ev_ebitda: float | None = None
    fundamental_score: float | None = None
    valuation_score: float | None = None


class PositionInputModel(BaseModel):
    asset_id: int
    quantity: float
    avg_cost: float
    current_weight: float = 0.0
    target_weight: float = 0.0


class RiskAssessmentModel(BaseModel):
    risk_score: float
    risk_level: RiskLevel
    rationale: dict[str, Any]


class RecommendationModel(BaseModel):
    asset_id: int
    date: date
    final_score: float
    recommendation: RecommendationStatus
    suggested_buy_low: float | None
    suggested_buy_high: float | None
    suggested_weight_add: float
    risk_score: float
    risk_level: RiskLevel
    invalidation: str
    rationale_json: dict[str, Any] = Field(default_factory=dict)


class PortfolioExposureModel(BaseModel):
    total_invested_weight: float
    by_asset: dict[str, float]
    by_sector: dict[str, float]
    by_asset_type: dict[str, float]


class DataSourcesConfigModel(BaseModel):
    prefer_cached_data: bool = True
    refresh_on_app_start: bool = False
    equities_refresh_interval_hours: int = 24
    crypto_refresh_interval_minutes: int = 180
    max_staleness_days: int = 5
    allow_demo_fallback: bool = True
    preserve_real_data_on_provider_failure: bool = True
    providers_priority: dict[str, list[str]] = Field(
        default_factory=lambda: {
            "stock": ["fmp", "alphavantage"],
            "etf": ["fmp", "alphavantage"],
            "crypto": ["binance"],
        }
    )


class AssetDataStatusModel(BaseModel):
    asset_id: int
    last_available_bar_date: date | None = None
    last_refresh_attempt_at: datetime | None = None
    last_successful_refresh_at: datetime | None = None
    last_refresh_status: str | None = None
    last_refresh_source: str | None = None
    data_mode: DataMode = DataMode.UNKNOWN
    freshness_status: FreshnessStatus = FreshnessStatus.MISSING
    last_error_message: str | None = None
