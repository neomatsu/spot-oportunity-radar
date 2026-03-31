from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from data.database import SignalORM, TechnicalSnapshotORM


class TechnicalSnapshotsRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert_snapshot(self, payload: dict) -> TechnicalSnapshotORM:
        statement = select(TechnicalSnapshotORM).where(
            TechnicalSnapshotORM.asset_id == payload["asset_id"],
            TechnicalSnapshotORM.date == payload["date"],
        )
        entity = self.session.scalar(statement)
        if entity is None:
            entity = TechnicalSnapshotORM(**payload)
            self.session.add(entity)
        else:
            for key, value in payload.items():
                setattr(entity, key, value)
        self.session.flush()
        return entity

    def latest_snapshot(self, asset_id: int) -> TechnicalSnapshotORM | None:
        statement = (
            select(TechnicalSnapshotORM)
            .where(TechnicalSnapshotORM.asset_id == asset_id)
            .order_by(TechnicalSnapshotORM.date.desc())
            .limit(1)
        )
        return self.session.scalar(statement)


class SignalsRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert_signal(self, payload: dict) -> SignalORM:
        statement = select(SignalORM).where(
            SignalORM.asset_id == payload["asset_id"],
            SignalORM.date == payload["date"],
        )
        entity = self.session.scalar(statement)
        if entity is None:
            entity = SignalORM(**payload)
            self.session.add(entity)
        else:
            for key, value in payload.items():
                setattr(entity, key, value)
        self.session.flush()
        return entity

    def latest_signals(self) -> list[SignalORM]:
        statement = select(SignalORM).order_by(SignalORM.date.desc(), SignalORM.final_score.desc())
        return list(self.session.scalars(statement))

    def latest_for_asset(self, asset_id: int, limit: int = 2) -> list[SignalORM]:
        statement = (
            select(SignalORM)
            .where(SignalORM.asset_id == asset_id)
            .order_by(SignalORM.date.desc())
            .limit(limit)
        )
        return list(self.session.scalars(statement))
