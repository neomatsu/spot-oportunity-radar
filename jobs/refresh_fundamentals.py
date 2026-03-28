from __future__ import annotations

from core.config import get_settings
from core.logger import configure_logging
from data.database import session_scope
from data.repositories.assets_repo import AssetsRepository
from services.fundamental_service import FundamentalService


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    with session_scope() as session:
        repo = AssetsRepository(session)
        service = FundamentalService(session)
        for asset in repo.list_enabled():
            service.refresh_for_asset(asset)


if __name__ == "__main__":
    main()
