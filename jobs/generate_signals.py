from __future__ import annotations

from core.config import get_settings
from core.logger import configure_logging
from data.database import session_scope
from services.recommendation_facade import RecommendationFacade


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    with session_scope() as session:
        facade = RecommendationFacade(session)
        facade.refresh_and_generate_all(force=False)


if __name__ == "__main__":
    main()
