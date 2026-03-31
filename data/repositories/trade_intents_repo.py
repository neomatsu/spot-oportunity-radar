from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from data.database import TradeIntentORM


class TradeIntentsRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_intent(self, payload: dict) -> TradeIntentORM:
        entity = TradeIntentORM(**payload)
        self.session.add(entity)
        self.session.flush()
        return entity

    def latest_open_for_asset(self, asset_id: int) -> TradeIntentORM | None:
        statement = (
            select(TradeIntentORM)
            .where(
                TradeIntentORM.asset_id == asset_id,
                TradeIntentORM.status.in_(["new", "reviewed", "approved"]),
            )
            .order_by(TradeIntentORM.created_at.desc())
            .limit(1)
        )
        return self.session.scalar(statement)

    def list_recent(self, limit: int = 200) -> list[TradeIntentORM]:
        statement = select(TradeIntentORM).order_by(TradeIntentORM.created_at.desc()).limit(limit)
        return list(self.session.scalars(statement))

    def update_status(self, intent_id: int, status: str) -> None:
        entity = self.session.get(TradeIntentORM, intent_id)
        if entity is None:
            return
        entity.status = status
        if status in {"reviewed", "approved", "rejected", "executed_manually"}:
            entity.reviewed_at = datetime.now(UTC)
        self.session.flush()
