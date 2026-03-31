from __future__ import annotations

from core.config import get_settings
from core.logger import configure_logging
from data.database import session_scope
from services.alert_service import AlertService


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    with session_scope() as session:
        summary = AlertService(session).send_pending_alerts()
    print({"alerts_sent": summary.alerts_sent})


if __name__ == "__main__":
    main()
