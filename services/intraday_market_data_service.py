from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pandas as pd
from sqlalchemy.orm import Session

from data.database import AssetORM
from data.providers.base_provider import ProviderError
from data.providers.binance_provider import BinanceProvider
from data.repositories.intraday_prices_repo import IntradayPricesRepository


@dataclass(frozen=True, slots=True)
class IntradayBarsResult:
    frame: pd.DataFrame
    interval: str
    downloaded_rows: int
    cached_rows: int
    refresh_error: str | None = None

    @property
    def source_label(self) -> str:
        if self.downloaded_rows:
            return "Binance + cache SQLite"
        return "cache SQLite"


class IntradayMarketDataService:
    """Cache-first intraday data access isolated from the daily market pipeline."""

    INTERVAL_DURATION = {
        "5m": timedelta(minutes=5),
        "15m": timedelta(minutes=15),
        "1h": timedelta(hours=1),
        "4h": timedelta(hours=4),
    }

    def __init__(
        self,
        session: Session,
        *,
        provider: BinanceProvider | None = None,
        market: str = "spot",
    ) -> None:
        self.session = session
        self.provider = provider or BinanceProvider()
        self.market = market
        self.repo = IntradayPricesRepository(session)

    @staticmethod
    def interval_for_range(start_at: datetime, end_at: datetime) -> str:
        duration = end_at - start_at
        if duration <= timedelta(days=35):
            return "5m"
        if duration <= timedelta(days=190):
            return "15m"
        if duration <= timedelta(days=400):
            return "1h"
        return "4h"

    def get_crypto_bars(
        self,
        asset: AssetORM,
        *,
        start_at: datetime,
        end_at: datetime,
        force_refresh: bool = False,
        interval: str | None = None,
    ) -> IntradayBarsResult:
        if asset.asset_type != "crypto":
            raise ValueError("Intraday Binance cache is only available for crypto assets")

        normalized_start = self._naive_utc(start_at)
        normalized_end = min(
            self._naive_utc(end_at),
            datetime.now(UTC).replace(tzinfo=None),
        )
        if normalized_start >= normalized_end:
            raise ValueError("The intraday range must contain at least one completed interval")

        interval = interval or self.interval_for_range(normalized_start, normalized_end)
        if interval not in self.INTERVAL_DURATION:
            raise ValueError(f"Unsupported cached intraday interval: {interval}")
        step = self.INTERVAL_DURATION[interval]
        downloaded_rows = 0
        refresh_error: str | None = None

        try:
            if force_refresh:
                downloaded_rows += self._fetch_and_store(
                    asset,
                    interval=interval,
                    start_at=normalized_start,
                    end_at=normalized_end,
                )
            else:
                cached_start, cached_end = self.repo.coverage(
                    asset.id, interval, market=self.market
                )
                if cached_start is None or cached_end is None:
                    downloaded_rows += self._fetch_and_store(
                        asset,
                        interval=interval,
                        start_at=normalized_start,
                        end_at=normalized_end,
                    )
                else:
                    if cached_start > normalized_start:
                        downloaded_rows += self._fetch_and_store(
                            asset,
                            interval=interval,
                            start_at=normalized_start,
                            end_at=min(normalized_end, cached_start - step),
                        )
                    if cached_end < normalized_end - step:
                        downloaded_rows += self._fetch_and_store(
                            asset,
                            interval=interval,
                            start_at=max(normalized_start, cached_end + step),
                            end_at=normalized_end,
                        )
        except ProviderError as exc:
            refresh_error = str(exc)

        frame = self.repo.get_bars(
            asset.id,
            interval,
            market=self.market,
            start_at=normalized_start,
            end_at=normalized_end,
        )
        if frame.empty and refresh_error:
            raise ProviderError(refresh_error)
        return IntradayBarsResult(
            frame=frame,
            interval=interval,
            downloaded_rows=downloaded_rows,
            cached_rows=len(frame),
            refresh_error=refresh_error,
        )

    def _fetch_and_store(
        self,
        asset: AssetORM,
        *,
        interval: str,
        start_at: datetime,
        end_at: datetime,
    ) -> int:
        if start_at > end_at:
            return 0
        frame = self.provider.fetch_intraday_prices(
            asset,
            interval=interval,
            start_at=start_at,
            end_at=end_at,
        )
        self.repo.upsert_bars(
            asset.id,
            interval,
            frame,
            market=self.market,
            provider_name=self.provider.name,
            quote_currency=asset.quote_currency,
        )
        return len(frame)

    @staticmethod
    def _naive_utc(value: datetime) -> datetime:
        timestamp = pd.Timestamp(value)
        if timestamp.tzinfo is not None:
            timestamp = timestamp.tz_convert("UTC").tz_localize(None)
        return timestamp.to_pydatetime()
