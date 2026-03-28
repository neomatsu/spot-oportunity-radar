from __future__ import annotations

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from data.database import AssetDataStatusORM, DataRefreshLogORM


class AssetDataStatusRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get(self, asset_id: int) -> AssetDataStatusORM | None:
        statement = select(AssetDataStatusORM).where(AssetDataStatusORM.asset_id == asset_id)
        return self.session.scalar(statement)

    def get_or_create(self, asset_id: int) -> AssetDataStatusORM:
        status = self.get(asset_id)
        if status is not None:
            return status

        status = AssetDataStatusORM(asset_id=asset_id)
        self.session.add(status)
        self.session.flush()
        return status

    def update_status(self, asset_id: int, **fields: object) -> AssetDataStatusORM:
        status = self.get_or_create(asset_id)
        for field_name, field_value in fields.items():
            setattr(status, field_name, field_value)
        self.session.flush()
        return status


class DataRefreshLogRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add_log(
        self,
        *,
        asset_id: int,
        provider: str,
        started_at: datetime,
        finished_at: datetime,
        status: str,
        rows_inserted: int = 0,
        error_message: str | None = None,
    ) -> DataRefreshLogORM:
        log = DataRefreshLogORM(
            asset_id=asset_id,
            provider=provider,
            started_at=started_at,
            finished_at=finished_at,
            status=status,
            rows_inserted=rows_inserted,
            error_message=error_message,
        )
        self.session.add(log)
        self.session.flush()
        return log

    def latest_for_asset_provider(
        self,
        asset_id: int,
        provider: str,
    ) -> DataRefreshLogORM | None:
        statement = (
            select(DataRefreshLogORM)
            .where(
                DataRefreshLogORM.asset_id == asset_id,
                DataRefreshLogORM.provider == provider,
            )
            .order_by(DataRefreshLogORM.finished_at.desc(), DataRefreshLogORM.id.desc())
            .limit(1)
        )
        return self.session.scalar(statement)
