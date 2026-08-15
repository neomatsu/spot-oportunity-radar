from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from data.database import (
    AppConfigORM,
    ExternalAssetMappingORM,
    PortfolioPositionORM,
    PortfolioTransactionORM,
)


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

    def set_total_capital(self, total_capital: float) -> None:
        config = self.session.get(AppConfigORM, "portfolio")
        payload = {"total_capital": float(total_capital)}
        if config is None:
            self.session.add(AppConfigORM(key="portfolio", value_json=payload))
        else:
            existing = dict(config.value_json or {})
            existing.update(payload)
            config.value_json = existing
        self.session.flush()

    def get_total_capital(self, default: float = 0.0) -> float:
        config = self.session.get(AppConfigORM, "portfolio")
        if config is None or not config.value_json:
            return default
        return float(config.value_json.get("total_capital") or default)

    def add_transaction(
        self,
        *,
        asset_id: int,
        transaction_type: str,
        transaction_date: date,
        quantity: float,
        price: float,
        gross_amount: float,
        fees: float = 0.0,
        taxes: float = 0.0,
        transaction_currency: str = "EUR",
        price_source: str = "manual",
        notes: str | None = None,
        external_source: str | None = None,
        external_transaction_id: str | None = None,
        external_payload_json: dict | None = None,
    ) -> PortfolioTransactionORM:
        entity = PortfolioTransactionORM(
            asset_id=asset_id,
            transaction_type=transaction_type.upper(),
            transaction_date=transaction_date,
            quantity=float(quantity),
            price=float(price),
            gross_amount=float(gross_amount),
            fees=float(fees),
            taxes=float(taxes),
            transaction_currency=transaction_currency.upper(),
            price_source=price_source,
            notes=notes,
            external_source=external_source,
            external_transaction_id=external_transaction_id,
            external_payload_json=external_payload_json,
            created_at=datetime.now(UTC).replace(tzinfo=None),
        )
        self.session.add(entity)
        self.session.flush()
        return entity

    def existing_external_transaction_ids(
        self,
        external_source: str,
        transaction_ids: list[str],
    ) -> set[str]:
        if not transaction_ids:
            return set()
        statement = select(PortfolioTransactionORM.external_transaction_id).where(
            PortfolioTransactionORM.external_source == external_source,
            PortfolioTransactionORM.external_transaction_id.in_(transaction_ids),
        )
        return {str(value) for value in self.session.scalars(statement) if value}

    def get_external_asset_mapping(
        self,
        external_source: str,
        external_asset_id: str,
    ) -> ExternalAssetMappingORM | None:
        statement = select(ExternalAssetMappingORM).where(
            ExternalAssetMappingORM.external_source == external_source,
            ExternalAssetMappingORM.external_asset_id == external_asset_id,
        )
        return self.session.scalar(statement)

    def upsert_external_asset_mapping(
        self,
        *,
        external_source: str,
        external_asset_id: str,
        asset_id: int,
        external_symbol: str | None = None,
        external_name: str | None = None,
    ) -> ExternalAssetMappingORM:
        entity = self.get_external_asset_mapping(external_source, external_asset_id)
        now = datetime.now(UTC).replace(tzinfo=None)
        if entity is None:
            entity = ExternalAssetMappingORM(
                external_source=external_source,
                external_asset_id=external_asset_id,
                external_symbol=external_symbol,
                external_name=external_name,
                asset_id=asset_id,
                created_at=now,
                updated_at=now,
            )
            self.session.add(entity)
        else:
            entity.asset_id = asset_id
            entity.external_symbol = external_symbol or entity.external_symbol
            entity.external_name = external_name or entity.external_name
            entity.updated_at = now
        self.session.flush()
        return entity

    def delete_transaction(self, transaction_id: int) -> bool:
        entity = self.session.get(PortfolioTransactionORM, transaction_id)
        if entity is None:
            return False
        self.session.delete(entity)
        self.session.flush()
        return True

    def list_transactions(
        self,
        *,
        asset_id: int | None = None,
    ) -> list[PortfolioTransactionORM]:
        statement = select(PortfolioTransactionORM)
        if asset_id is not None:
            statement = statement.where(PortfolioTransactionORM.asset_id == asset_id)
        statement = statement.order_by(
            PortfolioTransactionORM.transaction_date,
            PortfolioTransactionORM.id,
        )
        return list(self.session.scalars(statement))

    def replace_positions_from_aggregates(
        self,
        aggregates: dict[int, dict[str, float]],
        *,
        target_weights: dict[int, float] | None = None,
    ) -> None:
        target_weights = target_weights or {}
        self.session.execute(delete(PortfolioPositionORM))
        for asset_id, values in aggregates.items():
            if values["quantity"] <= 0:
                continue
            self.session.add(
                PortfolioPositionORM(
                    asset_id=asset_id,
                    quantity=values["quantity"],
                    avg_cost=values["avg_cost"],
                    current_weight=values["current_weight"],
                    target_weight=target_weights.get(asset_id, 0.0),
                )
            )
        self.session.flush()
