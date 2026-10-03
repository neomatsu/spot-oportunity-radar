from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
from sqlalchemy.orm import Session

from core.config import load_data_sources_config
from data.database import AssetLiveQuoteORM, AssetORM
from data.providers.base_provider import LiveQuote, ProviderError
from data.providers.binance_provider import BinanceProvider
from data.providers.yfinance_provider import YFinanceProvider
from data.repositories.assets_repo import AssetsRepository
from data.repositories.live_quotes_repo import LiveQuotesRepository


@dataclass(frozen=True, slots=True)
class LiveQuoteRefreshSummary:
    refreshed: int
    failed: tuple[str, ...]


class LiveQuoteService:
    """On-demand quotes isolated from persisted closed daily bars."""

    def __init__(self, session: Session) -> None:
        self.session = session
        config = load_data_sources_config()
        self.repo = LiveQuotesRepository(session)
        self.providers = {
            "equity": YFinanceProvider(
                symbol_aliases=config.provider_symbol_aliases.get("yfinance", {})
            ),
            "crypto": BinanceProvider(),
        }

    def get(self, asset_id: int) -> AssetLiveQuoteORM | None:
        return self.repo.get(asset_id)

    def refresh_asset(self, asset: AssetORM) -> AssetLiveQuoteORM:
        provider = (
            self.providers["crypto"]
            if asset.asset_type == "crypto"
            else self.providers["equity"]
        )
        quote = provider.fetch_current_quote(asset)
        if quote is None:
            raise ProviderError(f"No current quote is available for {asset.symbol}")
        return self.repo.upsert(asset.id, quote)

    def refresh_enabled(self) -> LiveQuoteRefreshSummary:
        refreshed = 0
        failed: list[str] = []
        for asset in AssetsRepository(self.session).list_enabled():
            try:
                self.refresh_asset(asset)
                refreshed += 1
            except (ProviderError, ValueError):
                failed.append(asset.symbol)
        return LiveQuoteRefreshSummary(refreshed=refreshed, failed=tuple(failed))

    @staticmethod
    def append_provisional_bar(
        closed_frame: pd.DataFrame,
        quote: AssetLiveQuoteORM | LiveQuote | None,
    ) -> pd.DataFrame:
        frame = closed_frame.copy()
        frame["is_provisional"] = False
        if quote is None:
            return frame
        latest_closed = (
            pd.to_datetime(frame["date"]).max().date() if not frame.empty else None
        )
        if latest_closed is not None and quote.session_date <= latest_closed:
            return frame
        provisional = pd.DataFrame(
            [
                {
                    "date": quote.session_date,
                    "open": quote.open,
                    "high": quote.high,
                    "low": quote.low,
                    "close": quote.price,
                    "volume": quote.volume,
                    "provider": quote.provider,
                    "quote_currency": quote.quote_currency,
                    "is_adjusted": False,
                    "inserted_at": quote.as_of,
                    "is_provisional": True,
                }
            ]
        )
        return pd.concat([frame, provisional], ignore_index=True)
