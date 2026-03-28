from __future__ import annotations

from core.logger import get_logger

logger = get_logger(__name__)


class AlertService:
    def send_pending_alerts(self) -> None:
        logger.info(
            "Alert dispatch is not implemented yet. Telegram integration "
            "is intentionally deferred."
        )
