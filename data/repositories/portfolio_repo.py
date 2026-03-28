from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from data.database import PortfolioPositionORM


class PortfolioRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def get_by_asset_id(self, asset_id: int) -> PortfolioPositionORM | None:
        statement = select(PortfolioPositionORM).where(PortfolioPositionORM.asset_id == asset_id)
        return self.session.scalar(statement)

    def upsert_position(
        self,
        *,
        asset_id: int,
        quantity: float,
        avg_cost: float,
        current_weight: float,
        target_weight: float,
    ) -> PortfolioPositionORM:
        statement = select(PortfolioPositionORM).where(PortfolioPositionORM.asset_id == asset_id)
        entity = self.session.scalar(statement)
        if entity is None:
            entity = PortfolioPositionORM(
                asset_id=asset_id,
                quantity=quantity,
                avg_cost=avg_cost,
                current_weight=current_weight,
                target_weight=target_weight,
            )
            self.session.add(entity)
        else:
            entity.quantity = quantity
            entity.avg_cost = avg_cost
            entity.current_weight = current_weight
            entity.target_weight = target_weight
        self.session.flush()
        return entity

    def delete_position(self, asset_id: int) -> bool:
        entity = self.get_by_asset_id(asset_id)
        if entity is None:
            return False
        self.session.delete(entity)
        self.session.flush()
        return True

    def list_positions(self) -> list[PortfolioPositionORM]:
        statement = select(PortfolioPositionORM).order_by(PortfolioPositionORM.asset_id)
        return list(self.session.scalars(statement))
