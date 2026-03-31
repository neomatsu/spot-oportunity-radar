from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from data.database import ScheduledJobRunORM


class JobRunsRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_run(self, *, job_name: str) -> ScheduledJobRunORM:
        entity = ScheduledJobRunORM(job_name=job_name, status="running")
        self.session.add(entity)
        self.session.flush()
        return entity

    def finalize_run(
        self,
        run_id: int,
        *,
        status: str,
        summary_json: dict | None = None,
        error_message: str | None = None,
    ) -> ScheduledJobRunORM | None:
        entity = self.session.get(ScheduledJobRunORM, run_id)
        if entity is None:
            return None
        entity.status = status
        entity.finished_at = datetime.now(UTC)
        entity.summary_json = summary_json
        entity.error_message = error_message
        self.session.flush()
        return entity

    def list_recent(self, job_name: str | None = None, limit: int = 50) -> list[ScheduledJobRunORM]:
        statement = select(ScheduledJobRunORM)
        if job_name is not None:
            statement = statement.where(ScheduledJobRunORM.job_name == job_name)
        statement = statement.order_by(ScheduledJobRunORM.started_at.desc()).limit(limit)
        return list(self.session.scalars(statement))
