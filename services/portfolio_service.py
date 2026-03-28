from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models import PortfolioExposureModel
from data.database import AssetORM
from data.repositories.portfolio_repo import PortfolioRepository


class PortfolioService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = PortfolioRepository(session)

    def get_exposures(self) -> PortfolioExposureModel:
        positions = self.repo.list_positions()
        assets = {asset.id: asset for asset in self.session.scalars(select(AssetORM)).all()}

        by_asset: dict[str, float] = {}
        by_sector: dict[str, float] = defaultdict(float)
        by_asset_type: dict[str, float] = defaultdict(float)
        total = 0.0

        for position in positions:
            asset = assets.get(position.asset_id)
            if asset is None:
                continue
            by_asset[asset.symbol] = position.current_weight
            by_sector[asset.sector] += position.current_weight
            by_asset_type[asset.asset_type] += position.current_weight
            total += position.current_weight

        return PortfolioExposureModel(
            total_invested_weight=total,
            by_asset=by_asset,
            by_sector=dict(by_sector),
            by_asset_type=dict(by_asset_type),
        )
