from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from data.database import PlannedEntryLevelORM


class PlannedEntriesRepository:
    MONITORABLE_STATUSES = ("active", "triggered")

    def __init__(self, session: Session) -> None:
        self.session = session

    def create(
        self,
        *,
        asset_id: int,
        target_price: float,
        price_currency: str | None,
        suggested_weight_pct: float | None,
        suggested_capital: float | None,
        tolerance_pct: float,
        rearm_distance_pct: float,
        notes: str | None = None,
        expires_at: date | None = None,
    ) -> PlannedEntryLevelORM:
        entity = PlannedEntryLevelORM(
            asset_id=asset_id,
            target_price=target_price,
            price_currency=price_currency,
            suggested_weight_pct=suggested_weight_pct,
            suggested_capital=suggested_capital,
            tolerance_pct=tolerance_pct,
            rearm_distance_pct=rearm_distance_pct,
            notes=notes,
            expires_at=expires_at,
            status="active",
        )
        self.session.add(entity)
        self.session.flush()
        return entity

    def get(self, level_id: int) -> PlannedEntryLevelORM | None:
        return self.session.get(PlannedEntryLevelORM, level_id)

    def list_for_asset(
        self, asset_id: int, *, include_inactive: bool = True
    ) -> list[PlannedEntryLevelORM]:
        statement = select(PlannedEntryLevelORM).where(
            PlannedEntryLevelORM.asset_id == asset_id
        )
        if not include_inactive:
            statement = statement.where(
                PlannedEntryLevelORM.status.in_(self.MONITORABLE_STATUSES)
            )
        statement = statement.order_by(PlannedEntryLevelORM.target_price.desc())
        return list(self.session.scalars(statement))

    def list_all(self) -> list[PlannedEntryLevelORM]:
        statement = (
            select(PlannedEntryLevelORM)
            .options(joinedload(PlannedEntryLevelORM.asset))
            .order_by(PlannedEntryLevelORM.updated_at.desc())
        )
        return list(self.session.scalars(statement))

    def update_status(self, level_id: int, status: str) -> PlannedEntryLevelORM | None:
        entity = self.get(level_id)
        if entity is None:
            return None
        entity.status = status
        entity.updated_at = datetime.now(UTC).replace(tzinfo=None)
        self.session.flush()
        return entity

    def delete(self, level_id: int) -> bool:
        entity = self.get(level_id)
        if entity is None:
            return False
        self.session.delete(entity)
        self.session.flush()
        return True
