from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from data.database import FundamentalsSnapshotORM


class FundamentalsRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert_snapshot(self, payload: dict) -> FundamentalsSnapshotORM:
        statement = select(FundamentalsSnapshotORM).where(
            FundamentalsSnapshotORM.asset_id == payload["asset_id"],
            FundamentalsSnapshotORM.date == payload["date"],
        )
        entity = self.session.scalar(statement)
        if entity is None:
            entity = FundamentalsSnapshotORM(**payload)
            self.session.add(entity)
        else:
            for key, value in payload.items():
                setattr(entity, key, value)
        self.session.flush()
        return entity

    def latest_snapshot(self, asset_id: int) -> FundamentalsSnapshotORM | None:
        statement = (
            select(FundamentalsSnapshotORM)
            .where(FundamentalsSnapshotORM.asset_id == asset_id)
            .order_by(FundamentalsSnapshotORM.date.desc())
            .limit(1)
        )
        return self.session.scalar(statement)
