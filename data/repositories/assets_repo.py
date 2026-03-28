from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from data.database import AssetORM


class AssetsRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def upsert_asset(
        self,
        *,
        symbol: str,
        name: str,
        asset_type: str,
        sector: str,
        region: str,
        enabled: bool,
        supports_fundamentals: bool,
    ) -> AssetORM:
        asset = self.get_by_symbol(symbol)
        if asset is None:
            asset = AssetORM(
                symbol=symbol,
                name=name,
                asset_type=asset_type,
                sector=sector,
                region=region,
                enabled=enabled,
                supports_fundamentals=supports_fundamentals,
            )
            self.session.add(asset)
            self.session.flush()
            return asset

        asset.name = name
        asset.asset_type = asset_type
        asset.sector = sector
        asset.region = region
        asset.enabled = enabled
        asset.supports_fundamentals = supports_fundamentals
        self.session.flush()
        return asset

    def get_by_symbol(self, symbol: str) -> AssetORM | None:
        statement = select(AssetORM).where(AssetORM.symbol == symbol)
        return self.session.scalar(statement)

    def list_enabled(self) -> list[AssetORM]:
        statement = select(AssetORM).where(AssetORM.enabled.is_(True)).order_by(AssetORM.symbol)
        return list(self.session.scalars(statement))

    def list_all(self) -> list[AssetORM]:
        statement = select(AssetORM).order_by(AssetORM.symbol)
        return list(self.session.scalars(statement))
