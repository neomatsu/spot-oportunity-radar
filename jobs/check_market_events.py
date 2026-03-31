from __future__ import annotations

from core.config import get_settings
from core.logger import configure_logging
from data.database import session_scope
from services.alert_service import AlertService


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    with session_scope() as session:
        summary = AlertService(session).scan_market_events()
    print(
        {
            "scanned_assets": summary.scanned_assets,
            "events_detected": summary.events_detected,
            "alerts_created": summary.alerts_created,
            "alerts_deduplicated": summary.alerts_deduplicated,
            "trade_intents_created": summary.trade_intents_created,
            "expired_intents": summary.expired_intents,
        }
    )


if __name__ == "__main__":
    main()
