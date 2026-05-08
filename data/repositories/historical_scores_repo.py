from __future__ import annotations

from datetime import date

from sqlalchemy import select
from sqlalchemy.orm import Session

from data.database import HistoricalScoreSnapshotORM


class HistoricalScoresRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert_snapshot(self, payload: dict) -> HistoricalScoreSnapshotORM:
        statement = select(HistoricalScoreSnapshotORM).where(
            HistoricalScoreSnapshotORM.asset_id == payload["asset_id"],
            HistoricalScoreSnapshotORM.date == payload["date"],
            HistoricalScoreSnapshotORM.source_version == payload["source_version"],
            HistoricalScoreSnapshotORM.portfolio_context == payload["portfolio_context"],
        )
        entity = self.session.scalar(statement)
        if entity is None:
            entity = HistoricalScoreSnapshotORM(**payload)
            self.session.add(entity)
        else:
            for key, value in payload.items():
                setattr(entity, key, value)
        self.session.flush()
        return entity

    def get_snapshot(
        self,
        *,
        asset_id: int,
        snapshot_date: date,
        source_version: str,
        portfolio_context: str,
    ) -> HistoricalScoreSnapshotORM | None:
        statement = (
            select(HistoricalScoreSnapshotORM)
            .where(
                HistoricalScoreSnapshotORM.asset_id == asset_id,
                HistoricalScoreSnapshotORM.date == snapshot_date,
                HistoricalScoreSnapshotORM.source_version == source_version,
                HistoricalScoreSnapshotORM.portfolio_context == portfolio_context,
            )
            .limit(1)
        )
        return self.session.scalar(statement)

    def list_snapshots(
        self,
        *,
        asset_id: int,
        start_date: date | None = None,
        end_date: date | None = None,
        source_version: str,
        portfolio_context: str,
    ) -> list[HistoricalScoreSnapshotORM]:
        statement = select(HistoricalScoreSnapshotORM).where(
            HistoricalScoreSnapshotORM.asset_id == asset_id,
            HistoricalScoreSnapshotORM.source_version == source_version,
            HistoricalScoreSnapshotORM.portfolio_context == portfolio_context,
        )
        if start_date is not None:
            statement = statement.where(HistoricalScoreSnapshotORM.date >= start_date)
        if end_date is not None:
            statement = statement.where(HistoricalScoreSnapshotORM.date <= end_date)
        statement = statement.order_by(HistoricalScoreSnapshotORM.date.asc())
        return list(self.session.scalars(statement))
