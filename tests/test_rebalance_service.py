from __future__ import annotations

from core.models import PortfolioExposureModel
from data.database import AssetORM
from services.rebalance_service import RebalanceService


def test_portfolio_fit_penalizes_overweight_asset() -> None:
    asset = AssetORM(
        symbol="MSFT",
        name="Microsoft",
        asset_type="stock",
        sector="Technology",
        region="US",
        enabled=True,
        supports_fundamentals=True,
    )
    exposure = PortfolioExposureModel(
        total_invested_weight=0.95,
        by_asset={"MSFT": 0.11},
        by_sector={"Technology": 0.38},
        by_asset_type={"stock": 0.7},
    )

    score, rationale = RebalanceService().portfolio_fit_score(asset, exposure)

    assert score < 50
    assert "Sector saturado" in rationale["reasons"]


def test_portfolio_fit_rewards_underweight_bucket() -> None:
    asset = AssetORM(
        symbol="QQQ",
        name="QQQ",
        asset_type="etf",
        sector="Broad Market",
        region="US",
        enabled=True,
        supports_fundamentals=True,
    )
    exposure = PortfolioExposureModel(
        total_invested_weight=0.55,
        by_asset={"MSFT": 0.08},
        by_sector={"Technology": 0.12},
        by_asset_type={"stock": 0.4, "etf": 0.1},
    )

    score, rationale = RebalanceService().portfolio_fit_score(asset, exposure)

    assert score > 60
    assert "Clase de activo infraponderada" in rationale["reasons"]
