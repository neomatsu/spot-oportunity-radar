from __future__ import annotations

from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from data.database import (
    BacktestMetricORM,
    BacktestParameterSetORM,
    BacktestPortfolioEventORM,
    BacktestRunORM,
    BacktestTradeORM,
)


class BacktestRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def create_run(self, payload: dict) -> BacktestRunORM:
        entity = BacktestRunORM(**payload)
        self.session.add(entity)
        self.session.flush()
        return entity

    def create_parameter_set(self, payload: dict) -> BacktestParameterSetORM:
        entity = BacktestParameterSetORM(**payload)
        self.session.add(entity)
        self.session.flush()
        return entity

    def add_metrics(self, payloads: Iterable[dict]) -> None:
        self.session.add_all(BacktestMetricORM(**payload) for payload in payloads)
        self.session.flush()

    def add_trades(self, payloads: Iterable[dict]) -> None:
        self.session.add_all(BacktestTradeORM(**payload) for payload in payloads)
        self.session.flush()

    def add_portfolio_events(self, payloads: Iterable[dict]) -> None:
        self.session.add_all(BacktestPortfolioEventORM(**payload) for payload in payloads)
        self.session.flush()

    def latest_runs(self, limit: int = 10) -> list[BacktestRunORM]:
        statement = select(BacktestRunORM).order_by(BacktestRunORM.created_at.desc()).limit(limit)
        return list(self.session.scalars(statement))
