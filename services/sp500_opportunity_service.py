from __future__ import annotations

import bisect
import html
import re
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from io import BytesIO
from typing import Any

import httpx
import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from core.config import get_settings, load_yaml_config
from core.logger import get_logger
from data.repositories.sp500_opportunity_repo import SP500OpportunityRepository
from opportunity_detectors.base import (
    OpportunityComponent,
    classify_score,
    weighted_available_score,
)
from services.sp500_scoring_service import SP500ScoringService

FrameLoader = Callable[[date, date], pd.DataFrame]
logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class SP500OpportunityReport:
    score: float | None
    classification: str
    components: list[OpportunityComponent]
    updated_at: datetime
    sp500_price: float | None
    price_date: date | None
    available_components: int
    minimum_available_components: int
    normalized_weights: dict[str, float] = field(default_factory=dict)

    @property
    def actionable(self) -> bool:
        return (
            self.score is not None
            and self.available_components >= self.minimum_available_components
        )


class SP500OpportunityService:
    """Independent, point-in-time-conscious S&P 500 opportunity detector."""

    LABELS = {
        "valuation": "Valuation",
        "sentiment": "Sentiment / VIX",
        "momentum": "Momentum / Trend deviation",
        "drawdown": "Drawdown",
        "breadth": "Market breadth",
        "macro": "Macro / Liquidity",
    }

    def __init__(
        self,
        session: Session,
        *,
        config: dict[str, Any] | None = None,
        price_loader: FrameLoader | None = None,
        vix_loader: FrameLoader | None = None,
        valuation_loader: FrameLoader | None = None,
        real_yield_loader: FrameLoader | None = None,
        breadth_loader: FrameLoader | None = None,
        macro_loader: FrameLoader | None = None,
        breadth_market_service: SP500ScoringService | None = None,
        http_client: httpx.Client | None = None,
        now: datetime | None = None,
    ) -> None:
        self.session = session
        self.config = config or load_yaml_config("sp500_opportunity.yaml")
        self.repo = SP500OpportunityRepository(session)
        self.price_loader = price_loader
        self.vix_loader = vix_loader
        self.valuation_loader = valuation_loader
        self.real_yield_loader = real_yield_loader
        self.breadth_loader = breadth_loader
        self.macro_loader = macro_loader
        self.breadth_market_service = breadth_market_service
        self.http_client = http_client
        self.now = now or datetime.now(UTC)
        self.root_dir = get_settings().root_dir
        cache_dir = self.config.get("market_data", {}).get(
            "cache_dir", "cache/sp500_opportunity"
        )
        self.cache_dir = self.root_dir / str(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @property
    def source_version(self) -> str:
        return str(
            self.config.get("history", {}).get(
                "source_version", "sp500_opportunity_v1"
            )
        )

    def cached_history(self, start_date: date, end_date: date) -> pd.DataFrame:
        return self.repo.history(
            start_date=start_date,
            end_date=end_date,
            source_version=self.source_version,
        )

    def update_latest_history(self, lookback_days: int = 10) -> pd.DataFrame:
        """Refresh recent scores only when the expected prior session is absent."""
        end_date = self.now.date()
        start_date = end_date - timedelta(days=max(2, lookback_days))
        cached = self.cached_history(start_date, end_date)
        expected_session = (pd.Timestamp(end_date) - pd.offsets.BDay(1)).date()
        if not cached.empty:
            latest_date = pd.to_datetime(cached["date"]).max().date()
            if latest_date >= expected_session:
                return cached
        self._refresh_breadth_price_cache()
        return self.update_history(start_date, end_date, force=True)

    def refresh_current(self, *, force_sources: bool = False) -> pd.DataFrame:
        """Refresh recent sessions, or rebuild the configured range on demand."""
        self._refresh_breadth_price_cache()
        if not force_sources:
            end_date = self.now.date()
            start_date = end_date - timedelta(days=10)
            return self.update_history(start_date, end_date, force=True)

        start_date = pd.Timestamp(
            self.config.get("history", {}).get("default_start_date", "1990-01-02")
        ).date()
        return self.update_history(start_date, self.now.date(), force=True)

    def _refresh_breadth_price_cache(self) -> None:
        if not self.config.get("breadth", {}).get("enabled", True):
            return
        if self.breadth_loader is not None:
            return
        service = self.breadth_market_service or SP500ScoringService(now=self.now)
        summary = service.refresh_price_cache_incremental()
        logger.info(
            "S&P 500 breadth inputs: target=%s total=%s current=%s refreshed=%s failed=%s",
            summary.target_session,
            summary.symbols_total,
            summary.symbols_current,
            summary.symbols_refreshed,
            summary.symbols_failed,
        )

    def compute(self) -> SP500OpportunityReport:
        history_cfg = self.config.get("history", {})
        start_date = pd.Timestamp(
            history_cfg.get("default_start_date", "1990-01-02")
        ).date()
        end_date = self.now.date()
        history = self.update_history(start_date, end_date)
        if history.empty:
            return SP500OpportunityReport(
                score=None,
                classification="DATOS_INSUFICIENTES",
                components=self._unavailable_components("No hay histórico calculado"),
                updated_at=self.now,
                sp500_price=None,
                price_date=None,
                available_components=0,
                minimum_available_components=self._minimum_available(),
            )
        latest = history.sort_values("date").iloc[-1]
        components = self._components_from_payload(latest.get("components_json") or {})
        quality = latest.get("data_quality_json") or {}
        return SP500OpportunityReport(
            score=self._optional_float(latest.get("overall_score")),
            classification=str(latest["classification"]),
            components=components,
            updated_at=pd.Timestamp(latest["computed_at"]).to_pydatetime().replace(tzinfo=UTC),
            sp500_price=self._optional_float(latest.get("sp500_price")),
            price_date=pd.Timestamp(latest["date"]).date(),
            available_components=int(latest["available_components"]),
            minimum_available_components=self._minimum_available(),
            normalized_weights=quality.get("normalized_weights", {}),
        )

    def update_history(
        self, start_date: date, end_date: date, *, force: bool = False
    ) -> pd.DataFrame:
        if start_date >= end_date:
            raise ValueError("start_date must be earlier than end_date")
        cached = self.cached_history(start_date, end_date)
        if not force and self._cache_covers(cached, start_date, end_date):
            return cached

        warmup_years = int(self.config.get("history", {}).get("warmup_years", 10))
        source_start = (pd.Timestamp(start_date) - pd.DateOffset(years=warmup_years)).date()
        price = self._load_price(source_start, end_date, force=force)
        if price.empty:
            raise RuntimeError("No se pudo obtener histórico del S&P 500")
        price = price[(price["date"].dt.date >= source_start) & (price["date"].dt.date <= end_date)]
        price = price.sort_values("date").drop_duplicates("date", keep="last")

        vix = self._safe_load("vix", self._load_vix, source_start, end_date, force)
        valuation_cfg = self.config.get("valuation", {})
        valuation_start = pd.Timestamp(
            valuation_cfg.get("historical_reference_start", source_start)
        ).date()
        valuation = self._safe_load(
            "valuation", self._load_valuation, valuation_start, end_date, force
        )
        real_yield = self._safe_load(
            "real_yield", self._load_real_yield, source_start, end_date, force
        )
        breadth = self._safe_load(
            "breadth", self._load_breadth, source_start, end_date, force
        )
        macro = self._safe_load("macro", self._load_macro, source_start, end_date, force)
        scored = self._score_history(
            price, vix, valuation, real_yield, breadth, macro
        )
        scored = scored[
            (scored["date"].dt.date >= start_date)
            & (scored["date"].dt.date <= end_date)
        ].copy()
        rows = scored.to_dict("records")
        self.repo.upsert_many(rows, source_version=self.source_version)
        self.session.flush()
        return self.cached_history(start_date, end_date)

    def _score_history(
        self,
        price: pd.DataFrame,
        vix: pd.DataFrame,
        valuation: pd.DataFrame,
        real_yield: pd.DataFrame,
        breadth: pd.DataFrame,
        macro: pd.DataFrame,
    ) -> pd.DataFrame:
        frame = price[["date", "close"]].rename(columns={"close": "sp500_price"}).copy()
        frame = self._add_momentum(frame)
        frame["ath"] = frame["sp500_price"].cummax()
        frame["drawdown_pct"] = (frame["sp500_price"] / frame["ath"] - 1.0) * 100.0

        frame = self._merge_asof(
            frame,
            vix,
            "vix",
            self.config.get("sentiment", {}).get("maximum_staleness_days", 10),
        )
        frame = self._merge_asof(
            frame,
            valuation,
            "cape",
            self.config.get("valuation", {}).get("maximum_staleness_days", 120),
        )
        frame = self._merge_asof(
            frame,
            valuation,
            "cape_full_percentile",
            self.config.get("valuation", {}).get("maximum_staleness_days", 120),
        )
        real_yield_cfg = self.config.get("valuation", {}).get("real_yield", {})
        frame = self._merge_asof(
            frame,
            real_yield,
            "real_yield_10y",
            real_yield_cfg.get("maximum_staleness_days", 10),
        )
        frame = self._merge_asof(
            frame,
            breadth,
            "pct_above_sma200",
            self.config.get("breadth", {}).get("maximum_staleness_days", 10),
        )
        frame = self._merge_asof(
            frame,
            macro,
            "fed_funds_rate",
            self.config.get("macro", {}).get("maximum_staleness_days", 10),
        )

        history_cfg = self.config.get("history", {})
        min_obs = int(history_cfg.get("minimum_percentile_observations", 252))
        valuation_min = int(
            self.config.get("valuation", {}).get(
                "minimum_percentile_observations", 60
            )
        )
        frame["valuation_percentile"] = frame["cape_full_percentile"]
        frame["valuation_full_history_score"] = (
            1.0 - frame["valuation_percentile"]
        ) * 10.0
        valuation_cfg = self.config.get("valuation", {})
        rolling_sessions = int(valuation_cfg.get("rolling_window_years", 20)) * 252
        rolling_min = int(
            valuation_cfg.get("rolling_minimum_observations", valuation_min)
        )
        frame["valuation_rolling_20y_percentile"] = self._rolling_percentile(
            frame["cape"], rolling_sessions, rolling_min
        )
        frame["valuation_rolling_20y_score"] = (
            1.0 - frame["valuation_rolling_20y_percentile"]
        ) * 10.0
        frame["excess_cape_yield"] = (
            100.0 / frame["cape"].replace(0, np.nan) - frame["real_yield_10y"]
        )
        real_yield_min = int(
            real_yield_cfg.get("minimum_percentile_observations", min_obs)
        )
        frame["excess_cape_yield_percentile"] = self._expanding_percentile(
            frame["excess_cape_yield"], real_yield_min
        )
        frame["valuation_relative_real_yield_score"] = (
            frame["excess_cape_yield_percentile"] * 10.0
        )
        valuation_subweights = valuation_cfg.get("subweights", {})
        frame["valuation_hybrid_raw_score"] = self._weighted_available_columns(
            frame,
            {
                "valuation_full_history_score": float(
                    valuation_subweights.get("full_history", 0.50)
                ),
                "valuation_rolling_20y_score": float(
                    valuation_subweights.get("rolling_20y", 0.25)
                ),
                "valuation_relative_real_yield_score": float(
                    valuation_subweights.get("excess_cape_yield", 0.25)
                ),
            },
        )
        shrinkage_cfg = valuation_cfg.get("neutral_shrinkage", {})
        shrinkage_weight = (
            float(shrinkage_cfg.get("weight", 0.25))
            if shrinkage_cfg.get("enabled", False)
            else 0.0
        )
        shrinkage_weight = min(1.0, max(0.0, shrinkage_weight))
        neutral_anchor = float(shrinkage_cfg.get("anchor_score", 5.0))
        frame["valuation_neutral_shrinkage_weight"] = shrinkage_weight
        frame["valuation_neutral_anchor_score"] = neutral_anchor
        frame["valuation_score"] = (
            frame["valuation_hybrid_raw_score"] * (1.0 - shrinkage_weight)
            + neutral_anchor * shrinkage_weight
        ).clip(0.0, 10.0)

        sentiment_min = int(
            self.config.get("sentiment", {}).get(
                "minimum_percentile_observations", min_obs
            )
        )
        frame["sentiment_percentile"] = self._expanding_percentile(
            frame["vix"], sentiment_min
        )
        frame["sentiment_score"] = frame["sentiment_percentile"] * 10.0

        daily_pct = self._expanding_percentile(frame["rsi_daily"], min_obs)
        weekly_pct = self._expanding_percentile(frame["rsi_weekly"], min_obs)
        distance_pct = self._expanding_percentile(frame["distance_sma200_pct"], min_obs)
        momentum_weights = self.config.get("momentum", {}).get("subweights", {})
        frame["momentum_score"] = 10.0 * (
            (1.0 - daily_pct) * float(momentum_weights.get("rsi_daily", 0.25))
            + (1.0 - weekly_pct) * float(momentum_weights.get("rsi_weekly", 0.25))
            + (1.0 - distance_pct)
            * float(momentum_weights.get("distance_sma200", 0.50))
        )

        drawdown_magnitude = -frame["drawdown_pct"]
        if self.config.get("drawdown", {}).get("method", "percentile") == "thresholds":
            frame["drawdown_score"] = frame["drawdown_pct"].map(
                self._threshold_drawdown_score
            )
            frame["drawdown_percentile"] = np.nan
        else:
            frame["drawdown_percentile"] = self._expanding_percentile(
                drawdown_magnitude, min_obs
            )
            frame["drawdown_score"] = frame["drawdown_percentile"] * 10.0

        breadth_min = int(
            self.config.get("breadth", {}).get(
                "minimum_percentile_observations", 126
            )
        )
        frame["breadth_percentile"] = self._expanding_percentile(
            frame["pct_above_sma200"], breadth_min
        )
        frame["breadth_score"] = (1.0 - frame["breadth_percentile"]) * 10.0

        macro_cfg = self.config.get("macro", {})
        macro_min = int(macro_cfg.get("minimum_percentile_observations", min_obs))
        lookback = int(macro_cfg.get("change_lookback_sessions", 63))
        frame["fed_funds_change"] = frame["fed_funds_rate"].diff(lookback)
        rate_pct = self._expanding_percentile(frame["fed_funds_rate"], macro_min)
        change_pct = self._expanding_percentile(frame["fed_funds_change"], macro_min)
        frame["macro_percentile"] = rate_pct
        frame["macro_score"] = 10.0 * (
            (1.0 - rate_pct) * float(macro_cfg.get("level_weight", 0.60))
            + (1.0 - change_pct) * float(macro_cfg.get("change_weight", 0.40))
        )

        weights = self.config.get("weights", {})
        component_keys = list(weights)
        score_columns = {key: f"{key}_score" for key in component_keys}
        rows: list[dict[str, Any]] = []
        for item in frame.itertuples(index=False):
            row = item._asdict()
            components = self._row_components(row)
            score, normalized_weights = weighted_available_score(
                components, weights, minimum_available=self._minimum_available()
            )
            available_count = sum(component.available for component in components)
            rows.append(
                {
                    "date": row["date"],
                    "overall_score": score,
                    "classification": classify_score(
                        score, self.config.get("classification", [])
                    ),
                    "available_components": available_count,
                    "sp500_price": row["sp500_price"],
                    "components_json": {
                        component.key: component.to_dict() for component in components
                    },
                    "data_quality_json": {
                        "normalized_weights": normalized_weights,
                        "available_keys": [
                            component.key for component in components if component.available
                        ],
                        "unavailable_keys": [
                            component.key for component in components if not component.available
                        ],
                        "point_in_time_unsafe_keys": [
                            component.key
                            for component in components
                            if component.available and not component.point_in_time_safe
                        ],
                        "score_columns": score_columns,
                    },
                }
            )
        result = pd.DataFrame(rows)
        if result.empty:
            return result
        raw_scores = pd.to_numeric(result["overall_score"], errors="coerce")
        calibrated, baseline = self._calibrate_overall_scores(raw_scores)
        result["overall_score"] = calibrated
        result["classification"] = calibrated.map(
            lambda value: classify_score(
                self._optional_float(value), self.config.get("classification", [])
            )
        )
        calibration_cfg = self.config.get("score_calibration", {})
        for index in result.index:
            quality = dict(result.at[index, "data_quality_json"] or {})
            quality["raw_overall_score"] = self._optional_float(raw_scores.at[index])
            quality["calibration_baseline"] = self._optional_float(baseline.at[index])
            quality["score_calibration"] = {
                "enabled": bool(calibration_cfg.get("enabled", False)),
                "target_mean": float(calibration_cfg.get("target_mean", 50.0)),
                "trailing_years": int(calibration_cfg.get("trailing_years", 5)),
                "dispersion_scale": float(
                    calibration_cfg.get("dispersion_scale", 0.80)
                ),
            }
            result.at[index, "data_quality_json"] = quality
        return result

    def _row_components(self, row: dict[str, Any]) -> list[OpportunityComponent]:
        as_of = pd.Timestamp(row["date"]).date()
        valuation_safe = bool(
            self.config.get("valuation", {}).get("point_in_time_safe", False)
        )
        breadth_safe = bool(
            self.config.get("breadth", {}).get("point_in_time_safe", False)
        )
        macro_safe = bool(
            self.config.get("macro", {}).get("point_in_time_safe", False)
        )
        return [
            self._component(
                "valuation",
                row.get("valuation_score"),
                row.get("cape"),
                "CAPE histórico y móvil a 20 años, ajustado por tipos reales.",
                "Robert Shiller / Yale + Multpl + FRED DFII10",
                as_of,
                row.get("valuation_percentile"),
                valuation_safe,
                metadata={
                    "full_history_percentile": self._optional_float(
                        row.get("valuation_percentile")
                    ),
                    "rolling_20y_percentile": self._optional_float(
                        row.get("valuation_rolling_20y_percentile")
                    ),
                    "real_yield_10y": self._optional_float(
                        row.get("real_yield_10y")
                    ),
                    "excess_cape_yield": self._optional_float(
                        row.get("excess_cape_yield")
                    ),
                    "excess_cape_yield_percentile": self._optional_float(
                        row.get("excess_cape_yield_percentile")
                    ),
                    "subscores": {
                        "full_history": self._optional_float(
                            row.get("valuation_full_history_score")
                        ),
                        "rolling_20y": self._optional_float(
                            row.get("valuation_rolling_20y_score")
                        ),
                        "relative_real_yield": self._optional_float(
                            row.get("valuation_relative_real_yield_score")
                        ),
                    },
                    "hybrid_raw_score": self._optional_float(
                        row.get("valuation_hybrid_raw_score")
                    ),
                    "neutral_shrinkage_weight": self._optional_float(
                        row.get("valuation_neutral_shrinkage_weight")
                    ),
                    "neutral_anchor_score": self._optional_float(
                        row.get("valuation_neutral_anchor_score")
                    ),
                },
            ),
            self._component(
                "sentiment",
                row.get("sentiment_score"),
                row.get("vix"),
                "VIX; miedo elevado recibe mayor score contrarian.",
                "Cboe VIX",
                as_of,
                row.get("sentiment_percentile"),
                True,
            ),
            self._component(
                "momentum",
                row.get("momentum_score"),
                row.get("distance_sma200_pct"),
                "RSI diario/semanal y distancia a SMA200 sin datos futuros.",
                "S&P 500 / yfinance",
                as_of,
                None,
                True,
                metadata={
                    "rsi_daily": self._optional_float(row.get("rsi_daily")),
                    "rsi_weekly": self._optional_float(row.get("rsi_weekly")),
                    "distance_sma200_pct": self._optional_float(
                        row.get("distance_sma200_pct")
                    ),
                },
            ),
            self._component(
                "drawdown",
                row.get("drawdown_score"),
                row.get("drawdown_pct"),
                "Caída desde el máximo histórico conocido hasta la fecha.",
                "S&P 500 / yfinance",
                as_of,
                row.get("drawdown_percentile"),
                True,
                metadata={"ath": self._optional_float(row.get("ath"))},
            ),
            self._component(
                "breadth",
                row.get("breadth_score"),
                row.get("pct_above_sma200"),
                "Porcentaje de componentes actuales sobre SMA200; sesgo de supervivencia.",
                "Caché del universo S&P 500 actual",
                as_of,
                row.get("breadth_percentile"),
                breadth_safe,
            ),
            self._component(
                "macro",
                row.get("macro_score"),
                row.get("fed_funds_rate"),
                "Fed Funds efectivo con un día de lag y cambio a 63 sesiones.",
                "FRED DFF",
                as_of,
                row.get("macro_percentile"),
                macro_safe,
                metadata={
                    "change_63_sessions": self._optional_float(
                        row.get("fed_funds_change")
                    )
                },
            ),
        ]

    def _load_price(self, start: date, end: date, *, force: bool) -> pd.DataFrame:
        if self.price_loader is not None:
            return self._normalize_series(self.price_loader(start, end), "close")

        def fetch() -> pd.DataFrame:
            import yfinance as yf

            raw = yf.download(
                self.config.get("index_symbol", "^GSPC"),
                start=start.isoformat(),
                end=(pd.Timestamp(end) + pd.Timedelta(days=1)).date().isoformat(),
                interval="1d",
                auto_adjust=False,
                progress=False,
                threads=False,
                timeout=int(
                    self.config.get("market_data", {}).get("timeout_seconds", 45)
                ),
            )
            if isinstance(raw.columns, pd.MultiIndex):
                raw.columns = raw.columns.get_level_values(0)
            return raw.reset_index().rename(columns={"Date": "date", "Close": "close"})

        return self._cached_frame("sp500_price", fetch, "close", start, end, force)

    def _load_vix(self, start: date, end: date, force: bool) -> pd.DataFrame:
        if not self.config.get("sentiment", {}).get("enabled", True):
            return pd.DataFrame()
        if self.vix_loader is not None:
            return self._normalize_series(self.vix_loader(start, end), "vix")

        def fetch() -> pd.DataFrame:
            content = self._get_bytes(self.config["sentiment"]["source_url"])
            raw = pd.read_csv(BytesIO(content))
            return raw.rename(columns={"DATE": "date", "CLOSE": "vix"})

        return self._cached_frame("vix", fetch, "vix", start, end, force)

    def _load_valuation(self, start: date, end: date, force: bool) -> pd.DataFrame:
        cfg = self.config.get("valuation", {})
        if not cfg.get("enabled", True):
            return pd.DataFrame()
        if self.valuation_loader is not None:
            frame = self._normalize_series(self.valuation_loader(start, end), "cape")
            if frame.empty:
                return frame
            frame = frame.copy()
            frame["date"] = frame["date"] + pd.to_timedelta(
                int(cfg.get("publication_lag_days", 45)), unit="D"
            )
            return self._add_full_history_valuation_percentile(frame, cfg)
        else:
            def fetch() -> pd.DataFrame:
                content = self._get_bytes(cfg["source_url"])
                raw = pd.read_excel(BytesIO(content), sheet_name="Data", skiprows=7)
                raw.columns = [str(column).strip() for column in raw.columns]
                date_col = raw.columns[0]
                cape_col = next(
                    column for column in raw.columns if str(column).strip().upper() == "CAPE"
                )
                values = pd.to_numeric(raw[date_col], errors="coerce")
                valid = values.notna()
                values = values.loc[valid]
                years = values.astype(int)
                months = ((values - years) * 100).round().clip(1, 12).astype(int)
                dates = pd.to_datetime(
                    {"year": years, "month": months, "day": 1}, errors="coerce"
                )
                return pd.DataFrame(
                    {
                        "date": dates.to_numpy(),
                        "cape": raw.loc[valid, cape_col].to_numpy(),
                    }
                )

            primary = self._cached_frame("cape", fetch, "cape", start, end, force)
        if primary.empty:
            return primary
        extension = self._load_valuation_extension(start, end, force)
        merged = self._splice_valuation_series(primary, extension, cfg)
        merged = merged.copy()
        primary_last = primary["date"].max()
        publication_lag = int(cfg.get("publication_lag_days", 45))
        primary_mask = merged["date"] <= primary_last
        merged.loc[primary_mask, "date"] = merged.loc[primary_mask, "date"] + pd.to_timedelta(
            publication_lag, unit="D"
        )
        shifted_primary_last = primary_last + pd.Timedelta(days=publication_lag)
        transition_overlap = (
            (merged["date"] > primary_last)
            & (merged["date"] <= shifted_primary_last)
            & ~primary_mask
        )
        merged = merged.loc[~transition_overlap]
        merged = merged.sort_values("date").drop_duplicates("date", keep="last")
        return self._add_full_history_valuation_percentile(merged, cfg)

    @staticmethod
    def _add_full_history_valuation_percentile(
        frame: pd.DataFrame, valuation_cfg: dict[str, Any]
    ) -> pd.DataFrame:
        result = frame.copy().sort_values("date")
        minimum = int(valuation_cfg.get("minimum_percentile_observations", 60))
        result["cape_full_percentile"] = (
            SP500OpportunityService._expanding_percentile(result["cape"], minimum)
        )
        return result

    def _load_valuation_extension(
        self, start: date, end: date, force: bool
    ) -> pd.DataFrame:
        cfg = self.config.get("valuation", {}).get("extension", {})
        if not cfg.get("enabled", False):
            return pd.DataFrame(columns=["date", "cape"])

        def fetch() -> pd.DataFrame:
            headers = {"User-Agent": str(cfg.get("user_agent", "Mozilla/5.0"))}
            content = self._get_bytes(str(cfg["source_url"]), headers=headers)
            return self._parse_multpl_table(content.decode("utf-8", errors="replace"))

        try:
            return self._cached_frame(
                "cape_multpl", fetch, "cape", start, end, force
            )
        except Exception as exc:
            logger.warning("CAPE extension unavailable; preserving Yale only: %s", exc)
            return pd.DataFrame(columns=["date", "cape"])

    @staticmethod
    def _parse_multpl_table(document: str) -> pd.DataFrame:
        rows: list[dict[str, Any]] = []
        pattern = re.compile(
            r"<tr[^>]*>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>\s*</tr>",
            flags=re.IGNORECASE | re.DOTALL,
        )
        for raw_date, raw_value in pattern.findall(document):
            date_text = html.unescape(re.sub(r"<[^>]+>", "", raw_date)).strip()
            value_text = html.unescape(re.sub(r"<[^>]+>", "", raw_value))
            value_match = re.search(r"-?\d+(?:\.\d+)?", value_text.replace(",", ""))
            parsed_date = pd.to_datetime(date_text, format="%b %d, %Y", errors="coerce")
            if pd.isna(parsed_date) or value_match is None:
                continue
            rows.append({"date": parsed_date, "cape": float(value_match.group())})
        return pd.DataFrame(rows, columns=["date", "cape"])

    @staticmethod
    def _splice_valuation_series(
        primary: pd.DataFrame,
        extension: pd.DataFrame,
        valuation_cfg: dict[str, Any],
    ) -> pd.DataFrame:
        if extension.empty:
            return primary.copy()
        left = primary.copy()
        right = extension.copy()
        left["month"] = left["date"].dt.to_period("M")
        right["month"] = right["date"].dt.to_period("M")
        overlap = left[["month", "cape"]].merge(
            right[["month", "cape"]], on="month", suffixes=("_primary", "_extension")
        ).sort_values("month")
        extension_cfg = valuation_cfg.get("extension", {})
        overlap = overlap.tail(int(extension_cfg.get("overlap_months", 12)))
        if overlap.empty:
            logger.warning("CAPE extension ignored because no overlap with Yale was found")
            return primary.copy()
        differences = (
            (overlap["cape_extension"] - overlap["cape_primary"]).abs()
            / overlap["cape_primary"].abs().replace(0, np.nan)
            * 100.0
        )
        median_difference = float(differences.median())
        maximum_difference = float(
            extension_cfg.get("maximum_median_difference_pct", 3.0)
        )
        if not np.isfinite(median_difference) or median_difference > maximum_difference:
            logger.warning(
                "CAPE extension ignored: median overlap difference %.2f%% exceeds %.2f%%",
                median_difference,
                maximum_difference,
            )
            return primary.copy()
        primary_last_month = left["month"].max()
        extension_only = right[right["month"] > primary_last_month][["date", "cape"]]
        logger.info(
            "CAPE extension accepted: overlap median difference %.2f%%, new rows=%s",
            median_difference,
            len(extension_only),
        )
        return pd.concat([primary[["date", "cape"]], extension_only], ignore_index=True)

    def _load_macro(self, start: date, end: date, force: bool) -> pd.DataFrame:
        cfg = self.config.get("macro", {})
        if not cfg.get("enabled", True):
            return pd.DataFrame()
        if self.macro_loader is not None:
            frame = self._normalize_series(
                self.macro_loader(start, end), "fed_funds_rate"
            )
        else:
            def fetch() -> pd.DataFrame:
                raw = pd.read_csv(BytesIO(self._get_bytes(cfg["source_url"])))
                value_col = str(cfg.get("fred_series", "DFF"))
                return raw.rename(
                    columns={"observation_date": "date", value_col: "fed_funds_rate"}
                )

            frame = self._cached_frame(
                "fed_funds", fetch, "fed_funds_rate", start, end, force
            )
        if frame.empty:
            return frame
        frame = frame.copy()
        frame["date"] = frame["date"] + pd.to_timedelta(
            int(cfg.get("publication_lag_days", 1)), unit="D"
        )
        return frame

    def _load_real_yield(
        self, start: date, end: date, force: bool
    ) -> pd.DataFrame:
        cfg = self.config.get("valuation", {}).get("real_yield", {})
        if not cfg.get("enabled", True):
            return pd.DataFrame()
        if self.real_yield_loader is not None:
            frame = self._normalize_series(
                self.real_yield_loader(start, end), "real_yield_10y"
            )
        else:
            def fetch() -> pd.DataFrame:
                raw = pd.read_csv(BytesIO(self._get_bytes(cfg["source_url"])))
                value_col = str(cfg.get("fred_series", "DFII10"))
                return raw.rename(
                    columns={
                        "observation_date": "date",
                        value_col: "real_yield_10y",
                    }
                )

            frame = self._cached_frame(
                "real_yield_10y", fetch, "real_yield_10y", start, end, force
            )
        if frame.empty:
            return frame
        frame = frame.copy()
        frame["date"] = frame["date"] + pd.to_timedelta(
            int(cfg.get("publication_lag_days", 1)), unit="D"
        )
        return frame

    def _load_breadth(self, start: date, end: date, force: bool) -> pd.DataFrame:
        cfg = self.config.get("breadth", {})
        if not cfg.get("enabled", True):
            return pd.DataFrame()
        if self.breadth_loader is not None:
            return self._normalize_series(
                self.breadth_loader(start, end), "pct_above_sma200"
            )
        cache_path = self.cache_dir / "breadth.csv"
        if cache_path.exists() and not force:
            cached = self._normalize_series(pd.read_csv(cache_path), "pct_above_sma200")
            if not cached.empty:
                return cached
        constituents_path = self.root_dir / str(cfg.get("constituents_cache", ""))
        prices_dir = self.root_dir / str(cfg.get("prices_cache_dir", ""))
        if not constituents_path.exists() or not prices_dir.exists():
            return pd.DataFrame()
        constituents = pd.read_csv(constituents_path)
        symbol_col = "yahoo_symbol" if "yahoo_symbol" in constituents.columns else "symbol"
        series: list[pd.Series] = []
        for symbol in constituents[symbol_col].dropna().astype(str):
            path = prices_dir / f"{symbol.replace('/', '_')}.csv"
            if not path.exists():
                continue
            raw = pd.read_csv(path)
            raw.columns = [str(column).lower() for column in raw.columns]
            if not {"date", "close"}.issubset(raw.columns):
                continue
            raw["date"] = pd.to_datetime(raw["date"], errors="coerce")
            raw["close"] = pd.to_numeric(raw["close"], errors="coerce")
            raw = raw.dropna(subset=["date", "close"]).sort_values("date")
            sma200 = raw["close"].rolling(200, min_periods=200).mean()
            above = (raw["close"] > sma200).where(sma200.notna())
            series.append(pd.Series(above.astype(float).values, index=raw["date"], name=symbol))
        if len(series) < int(cfg.get("minimum_constituents", 300)):
            return pd.DataFrame()
        panel = pd.concat(series, axis=1)
        breadth = (panel.mean(axis=1, skipna=True) * 100.0).rename(
            "pct_above_sma200"
        ).reset_index()
        breadth.columns = ["date", "pct_above_sma200"]
        breadth.to_csv(cache_path, index=False)
        return self._normalize_series(breadth, "pct_above_sma200")

    def _cached_frame(
        self,
        name: str,
        fetcher: Callable[[], pd.DataFrame],
        value_column: str,
        start: date,
        end: date,
        force: bool,
    ) -> pd.DataFrame:
        path = self.cache_dir / f"{name}.csv"
        if path.exists() and not force:
            cached = self._normalize_series(pd.read_csv(path), value_column)
            if self._source_cache_covers(cached, start, end):
                return cached
        retries = max(1, int(self.config.get("market_data", {}).get("retries", 2)))
        last_error: Exception | None = None
        for attempt in range(retries):
            try:
                frame = self._normalize_series(fetcher(), value_column)
                if frame.empty:
                    raise RuntimeError(f"Fuente {name} sin datos")
                frame.to_csv(path, index=False)
                return frame
            except Exception as exc:
                last_error = exc
                if attempt + 1 < retries:
                    time.sleep(attempt + 1)
        raise RuntimeError(f"No se pudo cargar {name}: {last_error}")

    def _get_bytes(self, url: str, *, headers: dict[str, str] | None = None) -> bytes:
        timeout = float(
            self.config.get("market_data", {}).get("timeout_seconds", 45)
        )
        if self.http_client is not None:
            response = self.http_client.get(
                url, timeout=timeout, follow_redirects=True, headers=headers
            )
        else:
            response = httpx.get(
                url, timeout=timeout, follow_redirects=True, headers=headers
            )
        response.raise_for_status()
        return response.content

    def _add_momentum(self, frame: pd.DataFrame) -> pd.DataFrame:
        result = frame.copy().sort_values("date")
        cfg = self.config.get("momentum", {})
        result["rsi_daily"] = self._rsi(
            result["sp500_price"], int(cfg.get("rsi_daily_period", 14))
        )
        sma_period = int(cfg.get("sma_period", 200))
        result["sma200"] = result["sp500_price"].rolling(
            sma_period, min_periods=sma_period
        ).mean()
        result["distance_sma200_pct"] = (
            result["sp500_price"] / result["sma200"] - 1.0
        ) * 100.0
        weekly = (
            result.set_index("date")["sp500_price"]
            .resample("W-FRI")
            .last()
            .dropna()
            .rename("weekly_close")
            .reset_index()
        )
        weekly["rsi_weekly"] = self._rsi(
            weekly["weekly_close"], int(cfg.get("rsi_weekly_period", 14))
        )
        return pd.merge_asof(
            result.sort_values("date"),
            weekly[["date", "rsi_weekly"]].sort_values("date"),
            on="date",
            direction="backward",
        )

    def _threshold_drawdown_score(self, drawdown_pct: float) -> float:
        if pd.isna(drawdown_pct):
            return np.nan
        thresholds = self.config.get("drawdown", {}).get("thresholds", [])
        for row in sorted(thresholds, key=lambda item: float(item["max_drawdown_pct"])):
            if drawdown_pct <= float(row["max_drawdown_pct"]):
                return float(row["score"])
        return 0.0

    @staticmethod
    def _rsi(series: pd.Series, period: int) -> pd.Series:
        delta = series.diff()
        gain = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
        loss = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
        rs = gain / loss.replace(0, np.nan)
        return 100.0 - 100.0 / (1.0 + rs)

    @staticmethod
    def _expanding_percentile(series: pd.Series, min_periods: int) -> pd.Series:
        ordered: list[float] = []
        output: list[float] = []
        for value in pd.to_numeric(series, errors="coerce"):
            if pd.isna(value):
                output.append(np.nan)
                continue
            numeric = float(value)
            bisect.insort(ordered, numeric)
            if len(ordered) < min_periods:
                output.append(np.nan)
            else:
                output.append(bisect.bisect_right(ordered, numeric) / len(ordered))
        return pd.Series(output, index=series.index, dtype=float)

    @staticmethod
    def _rolling_percentile(
        series: pd.Series, window: int, min_periods: int
    ) -> pd.Series:
        values = pd.to_numeric(series, errors="coerce").tolist()
        ordered: list[float] = []
        queue: list[float | None] = []
        output: list[float] = []
        for index, value in enumerate(values):
            numeric = None if pd.isna(value) else float(value)
            queue.append(numeric)
            if numeric is not None:
                bisect.insort(ordered, numeric)
            if index >= window:
                expired = queue[index - window]
                if expired is not None:
                    ordered.pop(bisect.bisect_left(ordered, expired))
            if numeric is None or len(ordered) < min_periods:
                output.append(np.nan)
            else:
                output.append(bisect.bisect_right(ordered, numeric) / len(ordered))
        return pd.Series(output, index=series.index, dtype=float)

    @staticmethod
    def _weighted_available_columns(
        frame: pd.DataFrame, columns: dict[str, float]
    ) -> pd.Series:
        weighted_sum = pd.Series(0.0, index=frame.index)
        available_weight = pd.Series(0.0, index=frame.index)
        for column, weight in columns.items():
            values = pd.to_numeric(frame[column], errors="coerce")
            available = values.notna()
            weighted_sum = weighted_sum.add(values.fillna(0.0) * weight)
            available_weight = available_weight.add(available.astype(float) * weight)
        return weighted_sum.div(available_weight.replace(0.0, np.nan))

    def _calibrate_overall_scores(
        self, raw_scores: pd.Series
    ) -> tuple[pd.Series, pd.Series]:
        cfg = self.config.get("score_calibration", {})
        numeric = pd.to_numeric(raw_scores, errors="coerce")
        if not cfg.get("enabled", False):
            return numeric.clip(0.0, 100.0), pd.Series(np.nan, index=numeric.index)
        sessions_per_year = 252
        window = int(cfg.get("trailing_years", 5)) * sessions_per_year
        minimum = int(cfg.get("minimum_history_years", 2)) * sessions_per_year
        baseline = numeric.shift(1).rolling(window, min_periods=minimum).mean()
        target = float(cfg.get("target_mean", 50.0))
        scale = max(0.0, float(cfg.get("dispersion_scale", 0.80)))
        calibrated = target + scale * (numeric - baseline)
        calibrated = calibrated.where(baseline.notna(), numeric)
        return calibrated.clip(0.0, 100.0), baseline

    @staticmethod
    def _merge_asof(
        base: pd.DataFrame,
        source: pd.DataFrame,
        column: str,
        maximum_staleness_days: int | None = None,
    ) -> pd.DataFrame:
        if source.empty or column not in source.columns:
            result = base.copy()
            result[column] = np.nan
            return result
        return pd.merge_asof(
            base.sort_values("date"),
            source[["date", column]].sort_values("date"),
            on="date",
            direction="backward",
            tolerance=(
                pd.Timedelta(days=int(maximum_staleness_days))
                if maximum_staleness_days is not None
                else None
            ),
        )

    @staticmethod
    def _normalize_series(frame: pd.DataFrame, value_column: str) -> pd.DataFrame:
        if frame is None or frame.empty:
            return pd.DataFrame(columns=["date", value_column])
        result = frame.copy()
        result.columns = [str(column).strip().lower() for column in result.columns]
        aliases = {
            "datetime": "date",
            "observation_date": "date",
            "close": (
                value_column
                if value_column != "close" and value_column not in result
                else "close"
            ),
        }
        result = result.rename(columns=aliases)
        if "date" not in result.columns or value_column not in result.columns:
            return pd.DataFrame(columns=["date", value_column])
        result["date"] = pd.to_datetime(
            result["date"], errors="coerce", utc=True
        ).dt.tz_localize(None)
        result[value_column] = pd.to_numeric(result[value_column], errors="coerce")
        return (
            result[["date", value_column]]
            .dropna()
            .sort_values("date")
            .drop_duplicates("date", keep="last")
            .reset_index(drop=True)
        )

    @staticmethod
    def _component(
        key: str,
        score: Any,
        raw_value: Any,
        description: str,
        source: str,
        as_of: date,
        percentile: Any,
        point_in_time_safe: bool,
        metadata: dict[str, Any] | None = None,
    ) -> OpportunityComponent:
        numeric_score = SP500OpportunityService._optional_float(score)
        return OpportunityComponent(
            key=key,
            label=SP500OpportunityService.LABELS[key],
            score=numeric_score,
            raw_value=SP500OpportunityService._optional_float(raw_value),
            description=description,
            source=source,
            as_of=as_of,
            percentile=SP500OpportunityService._optional_float(percentile),
            available=numeric_score is not None,
            point_in_time_safe=point_in_time_safe,
            error=(
                None
                if numeric_score is not None
                else "Histórico insuficiente o fuente no disponible"
            ),
            metadata=metadata,
        )

    def _components_from_payload(self, payload: dict[str, Any]) -> list[OpportunityComponent]:
        components: list[OpportunityComponent] = []
        for key in self.config.get("weights", {}):
            row = payload.get(key)
            if row is None:
                components.append(
                    OpportunityComponent(
                        key=key,
                        label=self.LABELS[key],
                        score=None,
                        raw_value=None,
                        description="Componente no disponible",
                        source="N/A",
                        available=False,
                        error="Sin datos",
                    )
                )
                continue
            row = dict(row)
            as_of = row.get("as_of")
            row["as_of"] = pd.Timestamp(as_of).date() if as_of else None
            components.append(OpportunityComponent(**row))
        return components

    def _unavailable_components(self, error: str) -> list[OpportunityComponent]:
        return [
            OpportunityComponent(
                key=key,
                label=self.LABELS[key],
                score=None,
                raw_value=None,
                description="Componente no disponible",
                source="N/A",
                available=False,
                error=error,
            )
            for key in self.config.get("weights", {})
        ]

    def _minimum_available(self) -> int:
        return int(self.config.get("minimum_available_components", 3))

    @staticmethod
    def _safe_load(
        name: str,
        loader: Callable[[date, date, bool], pd.DataFrame],
        start: date,
        end: date,
        force: bool,
    ) -> pd.DataFrame:
        try:
            return loader(start, end, force)
        except Exception:
            return pd.DataFrame()

    @staticmethod
    def _cache_covers(frame: pd.DataFrame, start: date, end: date) -> bool:
        if frame.empty:
            return False
        dates = pd.to_datetime(frame["date"])
        return (
            dates.min().date() <= start + pd.Timedelta(days=7)
            and dates.max().date() >= end - pd.Timedelta(days=10)
        )

    @staticmethod
    def _source_cache_covers(frame: pd.DataFrame, start: date, end: date) -> bool:
        if frame.empty:
            return False
        dates = pd.to_datetime(frame["date"])
        return (
            dates.min().date() <= start + pd.Timedelta(days=90)
            and dates.max().date() >= end - pd.Timedelta(days=90)
        )

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        if value is None or pd.isna(value):
            return None
        return float(value)
