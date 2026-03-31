from __future__ import annotations

from core.config import get_settings
from core.logger import configure_logging
from data.database import session_scope
from services.alert_service import AlertService
from services.recommendation_facade import RecommendationFacade


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    with session_scope() as session:
        recommendation_summary = RecommendationFacade(session).refresh_and_generate_all(force=False)
        alert_summary = AlertService(session).scan_market_events()
    print(
        {
            "signals_generated": recommendation_summary.generated_signals,
            "alerts_created": alert_summary.alerts_created,
            "alerts_deduplicated": alert_summary.alerts_deduplicated,
            "trade_intents_created": alert_summary.trade_intents_created,
        }
    )


if __name__ == "__main__":
    main()
