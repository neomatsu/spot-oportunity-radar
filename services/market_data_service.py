from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

import pandas as pd
from sqlalchemy.orm import Session

from core.config import get_settings, load_data_sources_config
from core.enums import DataMode, FreshnessStatus
from core.logger import get_logger
from data.database import AssetORM
from data.providers.alphavantage_provider import AlphaVantageProvider
from data.providers.base_provider import MarketDataProvider, ProviderError
from data.providers.binance_provider import BinanceProvider
from data.providers.demo_provider import DemoMarketDataProvider
from data.providers.fmp_provider import FinancialModelingPrepProvider
from data.repositories.data_status_repo import (
    AssetDataStatusRepository,
    DataRefreshLogRepository,
)
from data.repositories.prices_repo import PricesRepository

logger = get_logger(__name__)


def utc_now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@dataclass
class PriceRefreshResult:
    asset_id: int
    symbol: str
    status: str
    provider_name: str | None = None
    rows_stored: int = 0
    message: str | None = None
    data_mode: str = DataMode.UNKNOWN.value
    freshness_status: str = FreshnessStatus.MISSING.value
    last_available_bar_date: date | None = None
    api_called: bool = False


class MarketDataService:
    def __init__(
        self,
        session: Session,
        *,
        settings=None,
        data_config=None,
        providers: dict[str, MarketDataProvider] | None = None,
        demo_provider: DemoMarketDataProvider | None = None,
    ) -> None:
        self.session = session
        self.settings = settings or get_settings()
        self.data_config = data_config or load_data_sources_config()
        self.prices_repo = PricesRepository(session)
        self.status_repo = AssetDataStatusRepository(session)
        self.refresh_log_repo = DataRefreshLogRepository(session)
        self.providers = providers or {
            "binance": BinanceProvider(),
            "fmp": FinancialModelingPrepProvider(),
            "alphavantage": AlphaVantageProvider(),
        }
        self.demo_provider = demo_provider or DemoMarketDataProvider()

    def refresh_daily_prices(self, asset: AssetORM, force: bool = False) -> PriceRefreshResult:
        now = utc_now()
        status = self.status_repo.get_or_create(asset.id)
        latest_date = self.prices_repo.latest_date(asset.id)
        cached_count = self.prices_repo.row_count(asset.id)
        data_mode = self._normalize_data_mode(status.data_mode)
        freshness = self._determine_freshness(asset, latest_date, status.last_successful_refresh_at)

        if (
            cached_count
            and self.data_config.prefer_cached_data
            and not force
            and freshness == "fresh"
        ):
            message = f"{asset.symbol} served from cached {data_mode} data; no refresh needed."
            logger.info(message)
            self.status_repo.update_status(
                asset.id,
                last_available_bar_date=latest_date,
                freshness_status=freshness,
                last_refresh_status="cache_hit",
            )
            self.refresh_log_repo.add_log(
                asset_id=asset.id,
                provider="cache",
                started_at=now,
                finished_at=now,
                status="cache_hit",
                rows_inserted=0,
            )
            return PriceRefreshResult(
                asset_id=asset.id,
                symbol=asset.symbol,
                status="cache_hit",
                provider_name=status.last_refresh_source,
                rows_stored=0,
                message=message,
                data_mode=data_mode,
                freshness_status=freshness,
                last_available_bar_date=latest_date,
                api_called=False,
            )

        provider_candidates = self._provider_candidates(asset)
        if not provider_candidates:
            return self._handle_no_provider(
                asset=asset,
                now=now,
                latest_date=latest_date,
                cached_count=cached_count,
                data_mode=data_mode,
                freshness=freshness,
            )

        errors: list[str] = []
        for provider in provider_candidates:
            attempt_started_at = utc_now()
            self.status_repo.update_status(
                asset.id,
                last_refresh_attempt_at=attempt_started_at,
                last_refresh_source=provider.name,
                last_refresh_status="refreshing",
                last_available_bar_date=latest_date,
                freshness_status=freshness,
            )
            try:
                frame = provider.fetch_daily_prices(asset)
                if frame.empty:
                    raise ProviderError(
                        f"{provider.name} returned an empty daily series for {asset.symbol}."
                    )

                replace_history, refresh_note = self._should_replace_with_real_history(
                    asset_id=asset.id,
                    existing_mode=data_mode,
                    last_refresh_source=status.last_refresh_source,
                    real_frame=frame,
                )
                if replace_history:
                    self.prices_repo.replace_asset_prices(asset.id, frame)
                    inserted_rows = len(frame)
                    next_mode = DataMode.REAL.value
                else:
                    inserted_rows = self.prices_repo.upsert_asset_prices(asset.id, frame)
                    next_mode = self._merge_data_mode(data_mode, DataMode.REAL.value)
                latest_after_refresh = self.prices_repo.latest_date(asset.id)
                self.status_repo.update_status(
                    asset.id,
                    last_available_bar_date=latest_after_refresh,
                    last_refresh_attempt_at=attempt_started_at,
                    last_successful_refresh_at=utc_now(),
                    last_refresh_status="success",
                    last_refresh_source=provider.name,
                    data_mode=next_mode,
                    freshness_status=FreshnessStatus.FRESH.value,
                    last_error_message=None,
                )
                self.refresh_log_repo.add_log(
                    asset_id=asset.id,
                    provider=provider.name,
                    started_at=attempt_started_at,
                    finished_at=utc_now(),
                    status="success",
                    rows_inserted=inserted_rows,
                )
                logger.info(
                    "%s refreshed from %s; stored %s rows.%s",
                    asset.symbol,
                    provider.name,
                    inserted_rows,
                    f" {refresh_note}" if refresh_note else "",
                )
                return PriceRefreshResult(
                    asset_id=asset.id,
                    symbol=asset.symbol,
                    status="refreshed",
                    provider_name=provider.name,
                    rows_stored=inserted_rows,
                    message=refresh_note,
                    data_mode=next_mode,
                    freshness_status=FreshnessStatus.FRESH.value,
                    last_available_bar_date=latest_after_refresh,
                    api_called=True,
                )
            except ProviderError as exc:
                error_message = str(exc)
                logger.warning(
                    "Refresh failed for %s via %s: %s",
                    asset.symbol,
                    provider.name,
                    error_message,
                )
                errors.append(f"{provider.name}: {error_message}")
                self.refresh_log_repo.add_log(
                    asset_id=asset.id,
                    provider=provider.name,
                    started_at=attempt_started_at,
                    finished_at=utc_now(),
                    status="provider_error",
                    rows_inserted=0,
                    error_message=error_message,
                )
            except Exception as exc:
                error_message = str(exc)
                logger.exception(
                    "Unexpected refresh failure for %s via %s: %s",
                    asset.symbol,
                    provider.name,
                    exc,
                )
                errors.append(f"{provider.name}: {error_message}")
                self.refresh_log_repo.add_log(
                    asset_id=asset.id,
                    provider=provider.name,
                    started_at=attempt_started_at,
                    finished_at=utc_now(),
                    status="unexpected_error",
                    rows_inserted=0,
                    error_message=error_message,
                )

        combined_error = " | ".join(errors) if errors else "Unknown provider failure."
        return self._handle_provider_failure(
            asset=asset,
            now=now,
            latest_date=latest_date,
            cached_count=cached_count,
            data_mode=data_mode,
            last_successful_refresh_at=status.last_successful_refresh_at,
            error_message=combined_error,
        )

    def load_price_frame(self, asset_id: int, min_rows: int = 260) -> pd.DataFrame:
        frame = self.prices_repo.get_asset_prices(asset_id)
        if frame.empty:
            return frame
        frame["date"] = pd.to_datetime(frame["date"])
        frame = frame.sort_values("date").reset_index(drop=True)
        if len(frame) < min_rows:
            return frame
        return frame

    def _provider_candidates(self, asset: AssetORM) -> list[MarketDataProvider]:
        configured_priority = self.data_config.providers_priority.get(asset.asset_type, [])
        ordered: list[MarketDataProvider] = []
        seen: set[str] = set()

        for provider_name in configured_priority:
            provider = self.providers.get(provider_name)
            if (
                provider
                and provider.supports(asset)
                and not self._should_skip_provider_for_asset(asset.id, provider.name)
            ):
                ordered.append(provider)
                seen.add(provider_name)

        for provider_name, provider in self.providers.items():
            if provider_name in seen:
                continue
            if provider.supports(asset) and not self._should_skip_provider_for_asset(
                asset.id, provider_name
            ):
                ordered.append(provider)

        return ordered

    def _should_skip_provider_for_asset(self, asset_id: int, provider_name: str) -> bool:
        latest_log = self.refresh_log_repo.latest_for_asset_provider(asset_id, provider_name)
        if latest_log is None or not latest_log.error_message:
            return False

        error_text = latest_log.error_message.lower()
        if provider_name == "fmp" and (
            "subscription restriction" in error_text
            or "special endpoint" in error_text
            or "current subscription" in error_text
            or "premium query parameter" in error_text
        ):
            logger.info(
                "Skipping %s for asset_id=%s due to prior subscription restriction.",
                provider_name,
                asset_id,
            )
            return True

        return False

    def _handle_no_provider(
        self,
        *,
        asset: AssetORM,
        now: datetime,
        latest_date: date | None,
        cached_count: int,
        data_mode: str,
        freshness: str,
    ) -> PriceRefreshResult:
        if cached_count:
            message = f"{asset.symbol} served from cached data; no provider configured."
            logger.info(message)
            self.status_repo.update_status(
                asset.id,
                last_available_bar_date=latest_date,
                freshness_status=freshness,
                last_refresh_status="provider_unavailable_cached",
            )
            self.refresh_log_repo.add_log(
                asset_id=asset.id,
                provider="cache",
                started_at=now,
                finished_at=now,
                status="provider_unavailable_cached",
                rows_inserted=0,
            )
            return PriceRefreshResult(
                asset_id=asset.id,
                symbol=asset.symbol,
                status="provider_unavailable_cached",
                message=message,
                data_mode=data_mode,
                freshness_status=freshness,
                last_available_bar_date=latest_date,
            )

        if self._allow_demo_fallback():
            return self._store_demo_fallback(
                asset=asset,
                started_at=now,
                reason="no real provider is configured",
                existing_mode=data_mode,
            )

        logger.warning("No provider configured for %s.", asset.symbol)
        self.status_repo.update_status(
            asset.id,
            last_refresh_attempt_at=now,
            last_refresh_status="provider_unavailable",
            freshness_status=FreshnessStatus.MISSING.value,
            last_error_message="No provider configured for this asset.",
        )
        self.refresh_log_repo.add_log(
            asset_id=asset.id,
            provider="none",
            started_at=now,
            finished_at=now,
            status="provider_unavailable",
            rows_inserted=0,
            error_message="No provider configured for this asset.",
        )
        return PriceRefreshResult(
            asset_id=asset.id,
            symbol=asset.symbol,
            status="provider_unavailable",
            message="No provider configured for this asset.",
        )

    def _handle_provider_failure(
        self,
        *,
        asset: AssetORM,
        now: datetime,
        latest_date: date | None,
        cached_count: int,
        data_mode: str,
        last_successful_refresh_at: datetime | None,
        error_message: str,
    ) -> PriceRefreshResult:
        freshness = self._determine_freshness(asset, latest_date, last_successful_refresh_at)
        if cached_count and self.data_config.preserve_real_data_on_provider_failure:
            preserved_mode = (
                data_mode if data_mode != DataMode.UNKNOWN.value else DataMode.REAL.value
            )
            logger.warning(
                "Refresh failed for %s; preserving cached %s data.",
                asset.symbol,
                preserved_mode,
            )
            self.status_repo.update_status(
                asset.id,
                last_refresh_attempt_at=now,
                last_available_bar_date=latest_date,
                last_refresh_status="provider_error_preserved_cache",
                data_mode=preserved_mode,
                freshness_status=freshness,
                last_error_message=error_message,
            )
            self.refresh_log_repo.add_log(
                asset_id=asset.id,
                provider="cache",
                started_at=now,
                finished_at=now,
                status="preserved_cached_data",
                rows_inserted=0,
                error_message=error_message,
            )
            return PriceRefreshResult(
                asset_id=asset.id,
                symbol=asset.symbol,
                status="preserved_cached_data",
                message=error_message,
                data_mode=preserved_mode,
                freshness_status=freshness,
                last_available_bar_date=latest_date,
                api_called=True,
            )

        if self._allow_demo_fallback():
            return self._store_demo_fallback(
                asset=asset,
                started_at=now,
                reason=f"provider error: {error_message}",
                existing_mode=data_mode,
            )

        self.status_repo.update_status(
            asset.id,
            last_refresh_attempt_at=now,
            last_available_bar_date=latest_date,
            last_refresh_status="provider_error",
            freshness_status=freshness,
            last_error_message=error_message,
        )
        return PriceRefreshResult(
            asset_id=asset.id,
            symbol=asset.symbol,
            status="provider_error",
            message=error_message,
            data_mode=data_mode,
            freshness_status=freshness,
            last_available_bar_date=latest_date,
            api_called=True,
        )

    def _store_demo_fallback(
        self,
        *,
        asset: AssetORM,
        started_at: datetime,
        reason: str,
        existing_mode: str,
    ) -> PriceRefreshResult:
        frame = self.demo_provider.fetch_daily_prices(asset)
        inserted_rows = self.prices_repo.upsert_asset_prices(asset.id, frame)
        latest_date = self.prices_repo.latest_date(asset.id)
        next_mode = self._merge_data_mode(existing_mode, DataMode.DEMO.value)
        self.status_repo.update_status(
            asset.id,
            last_available_bar_date=latest_date,
            last_refresh_attempt_at=started_at,
            last_successful_refresh_at=utc_now(),
            last_refresh_status="demo_fallback",
            last_refresh_source=self.demo_provider.name,
            data_mode=next_mode,
            freshness_status=FreshnessStatus.FRESH.value,
            last_error_message=reason,
        )
        self.refresh_log_repo.add_log(
            asset_id=asset.id,
            provider=self.demo_provider.name,
            started_at=started_at,
            finished_at=utc_now(),
            status="demo_fallback",
            rows_inserted=inserted_rows,
            error_message=reason,
        )
        logger.warning("No real data available for %s; falling back to demo mode.", asset.symbol)
        return PriceRefreshResult(
            asset_id=asset.id,
            symbol=asset.symbol,
            status="demo_fallback",
            provider_name=self.demo_provider.name,
            rows_stored=inserted_rows,
            message=f"Demo fallback used because {reason}.",
            data_mode=next_mode,
            freshness_status=FreshnessStatus.FRESH.value,
            last_available_bar_date=latest_date,
        )

    def _determine_freshness(
        self,
        asset: AssetORM,
        latest_date: date | None,
        last_successful_refresh_at: datetime | None,
    ) -> str:
        if latest_date is None:
            return FreshnessStatus.MISSING.value

        today = date.today()
        age_days = max((today - latest_date).days, 0)
        if age_days > self.data_config.max_staleness_days:
            return FreshnessStatus.STALE.value

        if last_successful_refresh_at is not None:
            if utc_now() - last_successful_refresh_at <= self._refresh_interval(asset):
                return FreshnessStatus.FRESH.value

        recent_age_threshold = 1 if asset.asset_type == "crypto" else 3
        if age_days <= recent_age_threshold:
            return FreshnessStatus.FRESH.value
        return FreshnessStatus.STALE.value

    def _refresh_interval(self, asset: AssetORM) -> timedelta:
        if asset.asset_type == "crypto":
            return timedelta(minutes=self.data_config.crypto_refresh_interval_minutes)
        return timedelta(hours=self.data_config.equities_refresh_interval_hours)

    def _should_replace_with_real_history(
        self,
        *,
        asset_id: int,
        existing_mode: str,
        last_refresh_source: str | None,
        real_frame: pd.DataFrame,
    ) -> tuple[bool, str | None]:
        if existing_mode in {DataMode.DEMO.value, DataMode.MIXED.value}:
            return True, "Existing history replaced to remove demo/mixed contamination."
        if last_refresh_source == self.demo_provider.name:
            return True, "Existing history replaced after demo-to-real transition."

        existing_frame = self.prices_repo.get_asset_prices(asset_id, limit=90)
        if existing_frame.empty:
            return False, None

        existing_frame = existing_frame[["date", "close"]].copy()
        real_close_frame = real_frame[["date", "close"]].copy()
        existing_frame["date"] = pd.to_datetime(existing_frame["date"]).dt.date
        real_close_frame["date"] = pd.to_datetime(real_close_frame["date"]).dt.date
        merged = existing_frame.merge(
            real_close_frame,
            on="date",
            how="inner",
            suffixes=("_existing", "_real"),
        )
        if merged.empty:
            return False, None

        merged = merged.sort_values("date").reset_index(drop=True)
        latest_overlap = merged.iloc[-1]
        real_close = float(latest_overlap["close_real"])
        existing_close = float(latest_overlap["close_existing"])
        if real_close <= 0:
            return False, None

        relative_diff = abs(existing_close - real_close) / real_close
        if relative_diff > 0.25:
            return True, "Existing history replaced after conflicting overlap with real provider."

        return False, None

    def _merge_data_mode(self, existing_mode: str | None, new_mode: str) -> str:
        current = self._normalize_data_mode(existing_mode)
        if current == DataMode.UNKNOWN.value:
            return new_mode
        if current == new_mode:
            return current
        return DataMode.MIXED.value

    @staticmethod
    def _normalize_data_mode(data_mode: str | None) -> str:
        if data_mode in {DataMode.REAL.value, DataMode.DEMO.value, DataMode.MIXED.value}:
            return str(data_mode)
        return DataMode.UNKNOWN.value

    def _allow_demo_fallback(self) -> bool:
        return bool(self.settings.demo_mode and self.data_config.allow_demo_fallback)
