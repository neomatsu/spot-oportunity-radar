from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
import numpy as np
import pandas as pd
from sqlalchemy.orm import Session

from core.config import load_yaml_config
from data.database import AssetORM
from data.providers.binance_provider import BinanceProvider
from data.repositories.assets_repo import AssetsRepository
from data.repositories.bitcoin_opportunity_repo import BitcoinOpportunityRepository
from data.repositories.data_status_repo import AssetDataStatusRepository
from data.repositories.prices_repo import PricesRepository
from services.market_data_service import MarketDataService, PriceRefreshResult
from services.technical_service import TechnicalService


@dataclass(frozen=True)
class BitcoinOpportunityComponent:
    key: str
    label: str
    score: float | None
    value: float | None
    value_label: str
    detail: str
    source: str
    as_of: date | None = None
    error: str | None = None

    @property
    def available(self) -> bool:
        return self.score is not None


@dataclass(frozen=True)
class BitcoinOpportunityReport:
    score: float | None
    classification: str
    components: list[BitcoinOpportunityComponent]
    updated_at: datetime
    bitcoin_price: float | None
    bitcoin_price_date: date | None
    available_components: int
    minimum_available_components: int
    price_history: pd.DataFrame = field(default_factory=pd.DataFrame)

    @property
    def actionable(self) -> bool:
        return (
            self.score is not None
            and self.available_components >= self.minimum_available_components
        )


class BitcoinOpportunityService:
    """Independent, contrarian Bitcoin opportunity dashboard."""

    LABELS = {
        "fear_greed": "Fear & Greed",
        "rsi_daily": "RSI diario",
        "ema200": "EMA 200",
        "liquidity": "Liquidez",
        "dxy": "Dollar DXY",
        "public_interest": "Interes publico",
    }

    def __init__(
        self,
        session: Session,
        *,
        config: dict[str, Any] | None = None,
        http_client: httpx.Client | None = None,
        dxy_loader: Callable[[], pd.DataFrame] | None = None,
        dxy_history_loader: Callable[[date, date], pd.DataFrame] | None = None,
        bitcoin_history_loader: Callable[[AssetORM, date, date], pd.DataFrame]
        | None = None,
        market_data_service: MarketDataService | None = None,
        now: datetime | None = None,
    ) -> None:
        self.session = session
        self.config = config or load_yaml_config("bitcoin_opportunity.yaml")
        self.http_client = http_client
        self.dxy_loader = dxy_loader or self._download_dxy
        self.dxy_history_loader = dxy_history_loader or self._download_dxy_history
        self.bitcoin_history_loader = (
            bitcoin_history_loader or self._download_bitcoin_history
        )
        self.now = now or datetime.now(UTC)
        self.assets_repo = AssetsRepository(session)
        self.prices_repo = PricesRepository(session)
        self.data_status_repo = AssetDataStatusRepository(session)
        self.history_repo = BitcoinOpportunityRepository(session)
        self.technical_service = TechnicalService()
        self.market_data_service = market_data_service or MarketDataService(session)

    @property
    def history_source_version(self) -> str:
        return str(
            self.config.get("history", {}).get(
                "source_version", "bitcoin_opportunity_v1"
            )
        )

    def cached_history(self, start_date: date, end_date: date) -> pd.DataFrame:
        """Read persisted daily scores without contacting external providers."""
        return self.history_repo.history(
            start_date=start_date,
            end_date=end_date,
            source_version=self.history_source_version,
        )

    def update_history(self, start_date: date, end_date: date) -> pd.DataFrame:
        """Compute only uncached dates and return the complete cached range."""
        if start_date > end_date:
            raise ValueError("start_date must be before end_date")
        self._ensure_bitcoin_price_history(start_date, end_date)
        local = self._historical_local_frame(start_date, end_date)
        if local.empty:
            return self.cached_history(start_date, end_date)

        cached = self.cached_history(start_date, end_date)
        cached_dates = (
            set(pd.to_datetime(cached["date"]).dt.date) if not cached.empty else set()
        )
        incomplete_dates = (
            set(
                pd.to_datetime(
                    cached.loc[
                        cached["classification"] == "DATOS_INSUFICIENTES", "date"
                    ]
                ).dt.date
            )
            if not cached.empty
            else set()
        )
        missing_dates = [
            pd.Timestamp(value).date()
            for value in local["date"]
            if pd.Timestamp(value).date() not in cached_dates
            or pd.Timestamp(value).date() in incomplete_dates
        ]
        if not missing_dates:
            return cached

        fear = self._safe_history(
            self._fear_greed_history, columns=["date", "fear_greed"]
        )
        dxy_lookback_days = max(
            45, int(self.config.get("dxy", {}).get("lookback_sessions", 20)) * 2
        )
        dxy = self._safe_history(
            lambda: self._dxy_history(
                start_date - timedelta(days=dxy_lookback_days), end_date
            ),
            columns=["date", "dxy_close"],
        )
        interest = self._safe_history(
            lambda: self._public_interest_history(start_date, end_date),
            columns=["date", "views"],
        )
        rows = self._build_historical_rows(local, fear, dxy, interest, set(missing_dates))
        self.history_repo.upsert_many(rows, source_version=self.history_source_version)
        return self.cached_history(start_date, end_date)

    def update_latest_history(self, lookback_days: int = 2) -> pd.DataFrame:
        """Refresh the latest cached score dates after the daily BTC price refresh."""
        symbol = str(self.config.get("bitcoin_symbol", "BTCUSDT"))
        asset = self.assets_repo.get_by_symbol(symbol)
        if asset is None:
            return pd.DataFrame()
        latest_date = self.prices_repo.latest_date(asset.id)
        if latest_date is None:
            return pd.DataFrame()
        start_date = latest_date - timedelta(days=max(1, lookback_days))
        return self.update_history(start_date, latest_date)

    def refresh_market_data(self) -> PriceRefreshResult:
        """Force a BTC refresh before rebuilding the latest opportunity readings."""
        symbol = str(self.config.get("bitcoin_symbol", "BTCUSDT"))
        asset = self.assets_repo.get_by_symbol(symbol)
        if asset is None:
            raise ValueError(f"Bitcoin asset {symbol} is not configured.")

        result = self.market_data_service.refresh_daily_prices(asset, force=True)
        if result.status == "refreshed":
            self.update_latest_history()
        return result

    def _ensure_bitcoin_price_history(self, start_date: date, end_date: date) -> int:
        """Backfill only the missing prefix required by the requested score range."""
        symbol = str(self.config.get("bitcoin_symbol", "BTCUSDT"))
        asset = self.assets_repo.get_by_symbol(symbol)
        if asset is None:
            return 0

        history_cfg = self.config.get("history", {})
        if not bool(history_cfg.get("enable_price_backfill", False)):
            return 0
        warmup_days = int(history_cfg.get("price_warmup_days", 220))
        configured_source_start = pd.Timestamp(
            history_cfg.get("price_source_start", "2017-08-17")
        ).date()
        required_start = max(
            configured_source_start,
            start_date - timedelta(days=warmup_days),
        )
        earliest = self.prices_repo.earliest_date(asset.id)
        if earliest is not None and earliest <= required_start:
            return 0

        fetch_end = (
            end_date
            if earliest is None
            else min(end_date, earliest - timedelta(days=1))
        )
        if required_start > fetch_end:
            return 0

        frame = self.bitcoin_history_loader(asset, required_start, fetch_end)
        inserted = self.prices_repo.upsert_asset_prices(
            asset.id,
            frame,
            provider_name="binance",
            is_adjusted=False,
            quote_currency=asset.quote_currency,
        )
        self.data_status_repo.update_status(
            asset.id,
            historical_coverage_start=self.prices_repo.earliest_date(asset.id),
            historical_coverage_end=self.prices_repo.latest_date(asset.id),
            historical_provider_baseline="binance",
        )
        return inserted

    @staticmethod
    def _download_bitcoin_history(
        asset: AssetORM,
        start_date: date,
        end_date: date,
    ) -> pd.DataFrame:
        return BinanceProvider().fetch_daily_prices_for_history(
            asset,
            start_date=start_date,
            end_date=end_date,
        )

    def compute(self) -> BitcoinOpportunityReport:
        frame = self._bitcoin_frame()
        local_components, price, price_date, chart_frame = self._local_components(frame)
        external_components = [
            self._safe_external("fear_greed", self._fear_greed_component),
            self._safe_external("dxy", self._dxy_component),
            self._safe_external("public_interest", self._public_interest_component),
        ]
        components_by_key = {
            component.key: component for component in [*local_components, *external_components]
        }
        ordered_components = [
            components_by_key[key]
            for key in self.LABELS
            if key in components_by_key
        ]
        score, available = self._weighted_score(ordered_components)
        minimum = int(self.config.get("minimum_available_components", 4))
        classification = self._classification(score, available, minimum)
        return BitcoinOpportunityReport(
            score=score,
            classification=classification,
            components=ordered_components,
            updated_at=self.now,
            bitcoin_price=price,
            bitcoin_price_date=price_date,
            available_components=available,
            minimum_available_components=minimum,
            price_history=chart_frame,
        )

    def _bitcoin_frame(self) -> pd.DataFrame:
        symbol = str(self.config.get("bitcoin_symbol", "BTCUSDT"))
        asset = self.assets_repo.get_by_symbol(symbol)
        if asset is None:
            return pd.DataFrame()
        frame = self.prices_repo.get_asset_prices(asset.id)
        if frame.empty:
            return frame
        frame = frame.copy()
        frame["date"] = pd.to_datetime(frame["date"])
        return frame.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)

    def _historical_local_frame(self, start_date: date, end_date: date) -> pd.DataFrame:
        frame = self._bitcoin_frame()
        if frame.empty:
            return frame
        enriched = self.technical_service.compute_indicators(frame)
        enriched["ema200"] = enriched["close"].ewm(span=200, adjust=False).mean()
        cfg = self.config.get("liquidity", {})
        recent_days = int(cfg.get("recent_days", 7))
        baseline_days = int(cfg.get("baseline_days", 30))
        volume = pd.to_numeric(enriched["volume"], errors="coerce")
        recent = volume.rolling(recent_days, min_periods=recent_days).mean()
        baseline = volume.shift(recent_days).rolling(
            baseline_days, min_periods=baseline_days
        ).mean()
        enriched["liquidity_change_pct"] = (recent / baseline - 1) * 100
        dates = pd.to_datetime(enriched["date"]).dt.date
        return enriched.loc[(dates >= start_date) & (dates <= end_date)].copy()

    def _fear_greed_history(self) -> pd.DataFrame:
        cfg = self.config.get("fear_greed", {})
        payload = self._get_json(str(cfg.get("history_url", cfg["url"])))
        records = []
        for row in payload.get("data") or []:
            timestamp = row.get("timestamp")
            if timestamp is None:
                continue
            records.append(
                {
                    "date": datetime.fromtimestamp(int(timestamp), tz=UTC).date(),
                    "fear_greed": float(row["value"]),
                }
            )
        return self._clean_external_history(pd.DataFrame(records), "fear_greed")

    def _dxy_history(self, start_date: date, end_date: date) -> pd.DataFrame:
        frame = self.dxy_history_loader(start_date, end_date)
        if frame.empty or "close" not in frame.columns:
            return pd.DataFrame(columns=["date", "dxy_close"])
        working = frame[["date", "close"]].rename(columns={"close": "dxy_close"})
        return self._clean_external_history(working, "dxy_close")

    def _public_interest_history(self, start_date: date, end_date: date) -> pd.DataFrame:
        cfg = self.config.get("public_interest", {})
        history_days = int(cfg.get("history_days", 365))
        fetch_start = start_date - timedelta(days=history_days)
        fetch_end = min(end_date, self.now.date() - timedelta(days=1))
        if fetch_start > fetch_end:
            return pd.DataFrame(columns=["date", "views"])
        chunk_days = int(
            self.config.get("history", {}).get("public_interest_chunk_days", 365)
        )
        records: list[dict[str, Any]] = []
        chunk_start = fetch_start
        while chunk_start <= fetch_end:
            chunk_end = min(chunk_start + timedelta(days=chunk_days - 1), fetch_end)
            url = str(cfg["url_template"]).format(
                start=chunk_start.strftime("%Y%m%d"),
                end=chunk_end.strftime("%Y%m%d"),
            ).replace(" ", "")
            payload = self._get_json(
                url, headers={"User-Agent": str(cfg.get("user_agent", ""))}
            )
            for item in payload.get("items") or []:
                timestamp = str(item.get("timestamp", ""))[:8]
                if timestamp:
                    records.append(
                        {
                            "date": pd.to_datetime(timestamp, format="%Y%m%d").date(),
                            "views": float(item["views"]),
                        }
                    )
            chunk_start = chunk_end + timedelta(days=1)
        return self._clean_external_history(pd.DataFrame(records), "views")

    def _build_historical_rows(
        self,
        local: pd.DataFrame,
        fear: pd.DataFrame,
        dxy: pd.DataFrame,
        interest: pd.DataFrame,
        missing_dates: set[date],
    ) -> list[dict[str, Any]]:
        local = local.copy()
        local["date"] = pd.to_datetime(local["date"])
        local = local.sort_values("date").set_index("date")
        index = local.index
        aligned_fear = self._align_external(fear, index, "fear_greed")
        dxy_lookback = int(self.config.get("dxy", {}).get("lookback_sessions", 20))
        if dxy.empty:
            dxy_change = pd.Series(index=index, dtype="float64")
        else:
            dxy_series = dxy.set_index("date")["dxy_close"].sort_index()
            dxy_change = ((dxy_series / dxy_series.shift(dxy_lookback) - 1) * 100).reindex(
                index, method="ffill"
            )
        interest_cfg = self.config.get("public_interest", {})
        recent_days = int(interest_cfg.get("recent_days", 7))
        history_days = int(interest_cfg.get("history_days", 365))
        if interest.empty:
            interest_percentile = pd.Series(index=index, dtype="float64")
        else:
            views = interest.set_index("date")["views"].sort_index()
            rolling_percentile = views.rolling(
                history_days, min_periods=max(30, recent_days)
            ).apply(
                lambda values: float(
                    (values <= np.mean(values[-recent_days:])).mean() * 100
                ),
                raw=True,
            )
            interest_percentile = rolling_percentile.reindex(index, method="ffill")

        rows: list[dict[str, Any]] = []
        minimum = int(self.config.get("minimum_available_components", 4))
        for timestamp, row in local.iterrows():
            row_date = timestamp.date()
            if row_date not in missing_dates:
                continue
            components = self._historical_components(
                row=row,
                row_date=row_date,
                fear_value=self._series_value(aligned_fear, timestamp),
                dxy_change=self._series_value(dxy_change, timestamp),
                interest_percentile=self._series_value(interest_percentile, timestamp),
            )
            score, available = self._weighted_score(components)
            rows.append(
                {
                    "date": row_date,
                    "overall_score": score,
                    "classification": self._classification(score, available, minimum),
                    "available_components": available,
                    "bitcoin_price": self._optional_float(row.get("close")),
                    "components_json": {
                        component.key: {
                            "score": component.score,
                            "value": component.value,
                            "available": component.available,
                        }
                        for component in components
                    },
                }
            )
        return rows

    def _historical_components(
        self,
        *,
        row: pd.Series,
        row_date: date,
        fear_value: float | None,
        dxy_change: float | None,
        interest_percentile: float | None,
    ) -> list[BitcoinOpportunityComponent]:
        components = [
            self._rsi_component(self._optional_float(row.get("rsi14")), row_date),
            self._ema_component(
                self._optional_float(row.get("close")),
                self._optional_float(row.get("ema200")),
                row_date,
            ),
            self._historical_liquidity_component(
                self._optional_float(row.get("liquidity_change_pct")), row_date
            ),
            self._historical_fear_component(fear_value, row_date),
            self._historical_dxy_component(dxy_change, row_date),
            self._historical_interest_component(interest_percentile, row_date),
        ]
        by_key = {component.key: component for component in components}
        return [by_key[key] for key in self.LABELS]

    def _local_components(
        self, frame: pd.DataFrame
    ) -> tuple[list[BitcoinOpportunityComponent], float | None, date | None, pd.DataFrame]:
        if frame.empty:
            return (
                [
                    self._unavailable(key, "Sin historico local de BTCUSDT")
                    for key in ("rsi_daily", "ema200", "liquidity")
                ],
                None,
                None,
                pd.DataFrame(),
            )

        enriched = self.technical_service.compute_indicators(frame)
        enriched["ema200"] = enriched["close"].ewm(span=200, adjust=False).mean()
        latest = enriched.iloc[-1]
        price = self._optional_float(latest.get("close"))
        price_date = pd.Timestamp(latest["date"]).date()
        rsi = self._optional_float(latest.get("rsi14"))
        ema200 = self._optional_float(latest.get("ema200"))

        rsi_component = self._rsi_component(rsi, price_date)
        ema_component = self._ema_component(price, ema200, price_date)
        liquidity_component = self._liquidity_component(enriched, price_date)
        chart_frame = enriched[["date", "close", "ema200", "rsi14", "volume"]].tail(730).copy()
        return [rsi_component, ema_component, liquidity_component], price, price_date, chart_frame

    def _rsi_component(self, rsi: float | None, as_of: date) -> BitcoinOpportunityComponent:
        if rsi is None:
            return self._unavailable("rsi_daily", "RSI14 no disponible")
        cfg = self.config.get("rsi_daily", {})
        center = float(cfg.get("opportunity_center", 70))
        divisor = max(float(cfg.get("points_per_score", 4)), 0.01)
        score = self._clip((center - rsi) / divisor)
        return BitcoinOpportunityComponent(
            key="rsi_daily",
            label=self.LABELS["rsi_daily"],
            score=round(score, 2),
            value=round(rsi, 2),
            value_label=f"RSI14 {rsi:.1f}",
            detail="RSI bajo recibe mayor puntuacion de oportunidad contrarian.",
            source="Historico local BTCUSDT",
            as_of=as_of,
        )

    def _ema_component(
        self, price: float | None, ema200: float | None, as_of: date
    ) -> BitcoinOpportunityComponent:
        if price is None or ema200 is None or ema200 <= 0:
            return self._unavailable("ema200", "EMA200 no disponible")
        distance = (price / ema200 - 1) * 100
        score = 0.0
        thresholds = self.config.get("ema200", {}).get("thresholds", [])
        for threshold in thresholds:
            if distance <= float(threshold["max_distance_pct"]):
                score = float(threshold["score"])
                break
        return BitcoinOpportunityComponent(
            key="ema200",
            label=self.LABELS["ema200"],
            score=round(self._clip(score), 2),
            value=round(distance, 2),
            value_label=f"{distance:+.1f}% vs EMA200",
            detail=f"Precio {price:,.0f}; EMA200 {ema200:,.0f}.",
            source="Historico local BTCUSDT",
            as_of=as_of,
        )

    def _liquidity_component(
        self, frame: pd.DataFrame, as_of: date
    ) -> BitcoinOpportunityComponent:
        cfg = self.config.get("liquidity", {})
        recent_days = int(cfg.get("recent_days", 7))
        baseline_days = int(cfg.get("baseline_days", 30))
        volume = pd.to_numeric(frame["volume"], errors="coerce").dropna()
        if len(volume) < baseline_days + recent_days:
            return self._unavailable("liquidity", "Historial de volumen insuficiente")
        recent = float(volume.tail(recent_days).mean())
        baseline = float(volume.iloc[-(baseline_days + recent_days) : -recent_days].mean())
        if baseline <= 0:
            return self._unavailable("liquidity", "Volumen base no valido")
        change_pct = (recent / baseline - 1) * 100
        neutral = float(cfg.get("neutral_score", 5))
        sensitivity = float(cfg.get("score_sensitivity", 0.10))
        score = self._clip(neutral + change_pct * sensitivity)
        return BitcoinOpportunityComponent(
            key="liquidity",
            label=self.LABELS["liquidity"],
            score=round(score, 2),
            value=round(change_pct, 2),
            value_label=f"{change_pct:+.1f}% vs base 30d",
            detail="Proxy: volumen medio 7d comparado con las 30 sesiones anteriores.",
            source="Volumen local BTCUSDT",
            as_of=as_of,
        )

    def _historical_liquidity_component(
        self, change_pct: float | None, as_of: date
    ) -> BitcoinOpportunityComponent:
        if change_pct is None:
            return self._unavailable("liquidity", "Historial de volumen insuficiente")
        cfg = self.config.get("liquidity", {})
        score = self._clip(
            float(cfg.get("neutral_score", 5))
            + change_pct * float(cfg.get("score_sensitivity", 0.10))
        )
        return BitcoinOpportunityComponent(
            key="liquidity",
            label=self.LABELS["liquidity"],
            score=round(score, 2),
            value=round(change_pct, 2),
            value_label=f"{change_pct:+.1f}% vs base",
            detail="Proxy historico de volumen sin look-ahead.",
            source="Volumen local BTCUSDT",
            as_of=as_of,
        )

    def _historical_fear_component(
        self, value: float | None, as_of: date
    ) -> BitcoinOpportunityComponent:
        if value is None:
            return self._unavailable("fear_greed", "Fear & Greed no disponible")
        cfg = self.config.get("fear_greed", {})
        score = self._clip(
            (float(cfg.get("opportunity_center", 70)) - value)
            / max(float(cfg.get("points_per_score", 6)), 0.01)
        )
        return BitcoinOpportunityComponent(
            key="fear_greed",
            label=self.LABELS["fear_greed"],
            score=round(score, 2),
            value=round(value, 2),
            value_label=f"{value:.0f}/100",
            detail="Lectura historica de Alternative.me.",
            source="Alternative.me Fear & Greed API",
            as_of=as_of,
        )

    def _historical_dxy_component(
        self, change_pct: float | None, as_of: date
    ) -> BitcoinOpportunityComponent:
        if change_pct is None:
            return self._unavailable("dxy", "DXY historico insuficiente")
        cfg = self.config.get("dxy", {})
        score = self._clip(
            float(cfg.get("neutral_score", 5))
            - change_pct * float(cfg.get("score_per_pct_decline", 2.5))
        )
        return BitcoinOpportunityComponent(
            key="dxy",
            label=self.LABELS["dxy"],
            score=round(score, 2),
            value=round(change_pct, 2),
            value_label=f"{change_pct:+.2f}%",
            detail="Variacion historica de 20 sesiones.",
            source="Yahoo Finance DXY (DX-Y.NYB)",
            as_of=as_of,
        )

    def _historical_interest_component(
        self, percentile: float | None, as_of: date
    ) -> BitcoinOpportunityComponent:
        if percentile is None:
            return self._unavailable("public_interest", "Interes publico insuficiente")
        return BitcoinOpportunityComponent(
            key="public_interest",
            label=self.LABELS["public_interest"],
            score=round(self._clip((100 - percentile) / 10), 2),
            value=round(percentile, 2),
            value_label=f"Percentil {percentile:.0f}/100",
            detail="Percentil movil calculado solo con datos anteriores.",
            source="Wikimedia Pageviews, articulo Bitcoin",
            as_of=as_of,
        )

    def _fear_greed_component(self) -> BitcoinOpportunityComponent:
        cfg = self.config.get("fear_greed", {})
        payload = self._get_json(str(cfg["url"]))
        rows = payload.get("data") or []
        if not rows:
            raise ValueError("Fear & Greed sin datos")
        current = float(rows[0]["value"])
        values = [float(row["value"]) for row in rows[:7]]
        average = float(np.mean(values))
        center = float(cfg.get("opportunity_center", 70))
        divisor = max(float(cfg.get("points_per_score", 6)), 0.01)
        score = self._clip((center - current) / divisor)
        timestamp = rows[0].get("timestamp")
        as_of = datetime.fromtimestamp(int(timestamp), tz=UTC).date() if timestamp else None
        classification = str(rows[0].get("value_classification") or "")
        return BitcoinOpportunityComponent(
            key="fear_greed",
            label=self.LABELS["fear_greed"],
            score=round(score, 2),
            value=current,
            value_label=f"{current:.0f}/100 ({classification})",
            detail=f"Media de los ultimos 7 registros: {average:.1f}.",
            source="Alternative.me Fear & Greed API",
            as_of=as_of,
        )

    def _dxy_component(self) -> BitcoinOpportunityComponent:
        cfg = self.config.get("dxy", {})
        frame = self.dxy_loader()
        if frame.empty or "close" not in frame.columns:
            raise ValueError("DXY sin historico")
        close = pd.to_numeric(frame["close"], errors="coerce").dropna()
        lookback = int(cfg.get("lookback_sessions", 20))
        if len(close) <= lookback:
            raise ValueError("DXY con historial insuficiente")
        change_pct = (float(close.iloc[-1]) / float(close.iloc[-lookback - 1]) - 1) * 100
        neutral = float(cfg.get("neutral_score", 5))
        multiplier = float(cfg.get("score_per_pct_decline", 2.5))
        score = self._clip(neutral - change_pct * multiplier)
        as_of = None
        if "date" in frame.columns:
            as_of = pd.Timestamp(frame["date"].iloc[-1]).date()
        return BitcoinOpportunityComponent(
            key="dxy",
            label=self.LABELS["dxy"],
            score=round(score, 2),
            value=round(change_pct, 2),
            value_label=f"{change_pct:+.2f}% en {lookback} sesiones",
            detail="Un dolar debilitandose recibe mayor puntuacion para activos de riesgo.",
            source="Yahoo Finance DXY (DX-Y.NYB)",
            as_of=as_of,
        )

    def _public_interest_component(self) -> BitcoinOpportunityComponent:
        cfg = self.config.get("public_interest", {})
        history_days = int(cfg.get("history_days", 365))
        end_date = self.now.date() - timedelta(days=1)
        start_date = end_date - timedelta(days=history_days)
        url = str(cfg["url_template"]).format(
            start=start_date.strftime("%Y%m%d"), end=end_date.strftime("%Y%m%d")
        ).replace(" ", "")
        payload = self._get_json(url, headers={"User-Agent": str(cfg.get("user_agent", ""))})
        items = payload.get("items") or []
        views = pd.Series([float(item["views"]) for item in items], dtype="float64")
        recent_days = int(cfg.get("recent_days", 7))
        if len(views) < max(recent_days, 30):
            raise ValueError("Interes publico con historial insuficiente")
        recent_average = float(views.tail(recent_days).mean())
        percentile = float((views <= recent_average).mean() * 100)
        score = self._clip((100 - percentile) / 10)
        return BitcoinOpportunityComponent(
            key="public_interest",
            label=self.LABELS["public_interest"],
            score=round(score, 2),
            value=round(percentile, 2),
            value_label=f"Percentil {percentile:.0f}/100",
            detail=f"Media reciente: {recent_average:,.0f} visitas/dia; interes bajo puntua mas.",
            source="Wikimedia Pageviews, articulo Bitcoin",
            as_of=end_date,
        )

    def _download_dxy(self) -> pd.DataFrame:
        import yfinance as yf

        cfg = self.config.get("dxy", {})
        history = yf.Ticker(str(cfg.get("yahoo_symbol", "DX-Y.NYB"))).history(
            period=str(cfg.get("period", "6mo")),
            interval="1d",
            auto_adjust=False,
            actions=False,
            timeout=float(self.config.get("http", {}).get("timeout_seconds", 15)),
        )
        if history is None or history.empty:
            return pd.DataFrame()
        history = history.reset_index().rename(columns={"Date": "date", "Close": "close"})
        return history[["date", "close"]]

    def _download_dxy_history(self, start_date: date, end_date: date) -> pd.DataFrame:
        import yfinance as yf

        cfg = self.config.get("dxy", {})
        history = yf.download(
            str(cfg.get("yahoo_symbol", "DX-Y.NYB")),
            start=start_date - timedelta(days=60),
            end=end_date + timedelta(days=1),
            interval="1d",
            auto_adjust=False,
            actions=False,
            progress=False,
            timeout=float(self.config.get("http", {}).get("timeout_seconds", 15)),
        )
        if history is None or history.empty:
            return pd.DataFrame()
        history = history.reset_index()
        close = history["Close"]
        if isinstance(close, pd.DataFrame):
            close = close.iloc[:, 0]
        return pd.DataFrame({"date": history["Date"], "close": close})

    def _get_json(self, url: str, headers: dict[str, str] | None = None) -> dict[str, Any]:
        timeout = float(self.config.get("http", {}).get("timeout_seconds", 15))
        if self.http_client is not None:
            response = self.http_client.get(url, headers=headers, timeout=timeout)
        else:
            response = httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)
        response.raise_for_status()
        return response.json()

    def _safe_external(
        self, key: str, loader: Callable[[], BitcoinOpportunityComponent]
    ) -> BitcoinOpportunityComponent:
        try:
            return loader()
        except Exception as exc:
            return self._unavailable(key, str(exc))

    @staticmethod
    def _safe_history(loader: Callable[[], pd.DataFrame], *, columns: list[str]) -> pd.DataFrame:
        try:
            return loader()
        except Exception:
            return pd.DataFrame(columns=columns)

    @staticmethod
    def _clean_external_history(frame: pd.DataFrame, value_column: str) -> pd.DataFrame:
        if frame.empty:
            return pd.DataFrame(columns=["date", value_column])
        working = frame[["date", value_column]].copy()
        working["date"] = (
            pd.to_datetime(working["date"], utc=True).dt.tz_localize(None).dt.normalize()
        )
        working[value_column] = pd.to_numeric(working[value_column], errors="coerce")
        return (
            working.dropna(subset=["date", value_column])
            .sort_values("date")
            .drop_duplicates("date", keep="last")
            .reset_index(drop=True)
        )

    @staticmethod
    def _align_external(frame: pd.DataFrame, index: pd.DatetimeIndex, column: str) -> pd.Series:
        if frame.empty:
            return pd.Series(index=index, dtype="float64")
        series = frame.set_index("date")[column].sort_index()
        return series.reindex(index, method="ffill")

    @staticmethod
    def _series_value(series: pd.Series, timestamp: pd.Timestamp) -> float | None:
        if timestamp not in series.index:
            return None
        value = series.loc[timestamp]
        if value is None or pd.isna(value):
            return None
        return float(value)

    def _weighted_score(
        self, components: list[BitcoinOpportunityComponent]
    ) -> tuple[float | None, int]:
        weights = self.config.get("weights", {})
        available = [component for component in components if component.available]
        total_weight = sum(float(weights.get(component.key, 0)) for component in available)
        if not available or total_weight <= 0:
            return None, 0
        weighted = sum(
            float(component.score) * float(weights.get(component.key, 0))
            for component in available
            if component.score is not None
        )
        return round(weighted / total_weight * 10, 2), len(available)

    def _classification(self, score: float | None, available: int, minimum: int) -> str:
        if score is None or available < minimum:
            return "DATOS_INSUFICIENTES"
        cfg = self.config.get("classification", {})
        if score >= float(cfg.get("exceptional_min", 80)):
            return "OPORTUNIDAD_EXCEPCIONAL"
        if score >= float(cfg.get("good_min", 65)):
            return "BUENA_OPORTUNIDAD"
        if score >= float(cfg.get("neutral_min", 45)):
            return "NEUTRAL"
        if score >= float(cfg.get("caution_min", 30)):
            return "PRECAUCION"
        return "SOBRECALENTADO"

    def _unavailable(self, key: str, error: str) -> BitcoinOpportunityComponent:
        return BitcoinOpportunityComponent(
            key=key,
            label=self.LABELS[key],
            score=None,
            value=None,
            value_label="N/A",
            detail="Fuente no disponible; el peso se excluye temporalmente.",
            source="N/A",
            error=error,
        )

    @staticmethod
    def _clip(value: float) -> float:
        return float(np.clip(value, 0, 10))

    @staticmethod
    def _optional_float(value: Any) -> float | None:
        if value is None or pd.isna(value):
            return None
        return float(value)
