from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from data.database import AlertORM, MarketEventORM, NotificationLogORM


class AlertsRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_alert(self, payload: dict) -> AlertORM:
        entity = AlertORM(**payload)
        self.session.add(entity)
        self.session.flush()
        return entity

    def latest_by_dedupe_key(self, dedupe_key: str) -> AlertORM | None:
        statement = (
            select(AlertORM)
            .where(AlertORM.dedupe_key == dedupe_key)
            .order_by(AlertORM.last_triggered_at.desc())
            .limit(1)
        )
        return self.session.scalar(statement)

    def should_suppress(
        self,
        *,
        dedupe_key: str,
        cooldown_minutes: int,
        severity: str,
        material_change: bool,
    ) -> bool:
        latest = self.latest_by_dedupe_key(dedupe_key)
        if latest is None:
            return False
        if latest.severity != severity or material_change:
            return False
        cooldown_boundary = datetime.now(UTC) - timedelta(minutes=cooldown_minutes)
        last_triggered_at = latest.last_triggered_at
        if last_triggered_at.tzinfo is None:
            last_triggered_at = last_triggered_at.replace(tzinfo=UTC)
        return last_triggered_at >= cooldown_boundary

    def mark_sent(self, alert_id: int) -> None:
        entity = self.session.get(AlertORM, alert_id)
        if entity is None:
            return
        now = datetime.now(UTC)
        entity.status = "sent"
        entity.sent_at = now
        entity.last_triggered_at = now
        self.session.flush()

    def update_status(self, alert_id: int, status: str) -> None:
        entity = self.session.get(AlertORM, alert_id)
        if entity is None:
            return
        entity.status = status
        self.session.flush()

    def list_recent(self, limit: int = 200) -> list[AlertORM]:
        statement = select(AlertORM).order_by(AlertORM.created_at.desc()).limit(limit)
        return list(self.session.scalars(statement))

    def list_pending(self) -> list[AlertORM]:
        statement = (
            select(AlertORM)
            .where(AlertORM.status == "new")
            .order_by(AlertORM.created_at.asc())
        )
        return list(self.session.scalars(statement))

    def create_market_event(self, payload: dict) -> MarketEventORM:
        entity = MarketEventORM(**payload)
        self.session.add(entity)
        self.session.flush()
        return entity

    def list_unprocessed_events(self) -> list[MarketEventORM]:
        statement = (
            select(MarketEventORM)
            .where(MarketEventORM.processed.is_(False))
            .order_by(MarketEventORM.detected_at.asc())
        )
        return list(self.session.scalars(statement))

    def mark_event_processed(self, event_id: int) -> None:
        entity = self.session.get(MarketEventORM, event_id)
        if entity is None:
            return
        entity.processed = True
        self.session.flush()

    def log_notification(
        self,
        *,
        alert_id: int | None,
        channel: str,
        status: str,
        error_message: str | None = None,
    ) -> NotificationLogORM:
        entity = NotificationLogORM(
            alert_id=alert_id,
            channel=channel,
            status=status,
            error_message=error_message,
        )
        self.session.add(entity)
        self.session.flush()
        return entity

    def list_notification_logs(self, limit: int = 200) -> list[NotificationLogORM]:
        statement = (
            select(NotificationLogORM)
            .order_by(NotificationLogORM.attempted_at.desc())
            .limit(limit)
        )
        return list(self.session.scalars(statement))
