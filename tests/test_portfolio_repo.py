from __future__ import annotations

from data.database import AssetORM
from data.repositories.portfolio_repo import PortfolioRepository


def test_portfolio_repo_can_delete_existing_position(db_session) -> None:
    asset = AssetORM(
        symbol="MSFT",
        name="Microsoft",
        asset_type="stock",
        sector="Technology",
        region="US",
        enabled=True,
        supports_fundamentals=True,
    )
    db_session.add(asset)
    db_session.flush()

    repo = PortfolioRepository(db_session)
    repo.upsert_position(
        asset_id=asset.id,
        quantity=10,
        avg_cost=400,
        current_weight=0.1,
        target_weight=0.08,
    )

    deleted = repo.delete_position(asset.id)

    assert deleted is True
    assert repo.get_by_asset_id(asset.id) is None
