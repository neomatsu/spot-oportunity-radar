from __future__ import annotations

from core.config import get_settings
from core.logger import configure_logging
from services.alert_service import AlertService


def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    AlertService().send_pending_alerts()


if __name__ == "__main__":
    main()
