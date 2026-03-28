from __future__ import annotations

from core.config import get_settings
from core.logger import configure_logging
from data.database import session_scope
from data.repositories.assets_repo import AssetsRepository
from services.market_data_service import MarketDataService


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    with session_scope() as session:
        assets_repo = AssetsRepository(session)
        market_service = MarketDataService(session)
        for asset in assets_repo.list_enabled():
            market_service.refresh_daily_prices(asset, force=False)


if __name__ == "__main__":
    main()
