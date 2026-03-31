from __future__ import annotations

from typing import Any

from core.config import load_yaml_config
from core.models import PortfolioExposureModel
from data.database import AssetORM


class ExecutionPrepService:
    def __init__(self) -> None:
        self.execution_rules = load_yaml_config("execution_rules.yaml")
        self.portfolio_rules = load_yaml_config("portfolio_rules.yaml")

    def prepare(
        self,
        *,
        asset: AssetORM,
        watchlist_row: dict[str, Any],
        exposure: PortfolioExposureModel,
    ) -> dict[str, Any]:
        assumed_value = float(self.execution_rules["defaults"]["assumed_portfolio_value"])
        suggested_weight_pct = float(watchlist_row.get("suggested_weight_add") or 0.0)
        suggested_weight = suggested_weight_pct / 100
        cash_weight = max(0.0, 1 - exposure.total_invested_weight)
        has_existing_position = (
            asset.symbol in exposure.by_asset and exposure.by_asset[asset.symbol] > 0
        )

        if (
            self.execution_rules["sizing"]["scale_down_if_existing_position"]
            and has_existing_position
        ):
            suggested_weight *= 0.5
        if self.execution_rules["sizing"]["scale_down_if_low_cash"] and cash_weight < 0.15:
            suggested_weight *= max(0.25, cash_weight / 0.15)

        max_pct = float(self.execution_rules["sizing"]["max_capital_per_intent_pct"])
        min_pct = float(self.execution_rules["sizing"]["min_capital_per_intent_pct"])
        final_weight = min(max_pct, max(min_pct, suggested_weight)) if suggested_weight > 0 else 0.0
        if cash_weight <= 0:
            final_weight = 0.0
        final_weight = min(final_weight, cash_weight)

        buy_high = watchlist_row.get("suggested_buy_high")
        last_price = watchlist_row.get("last_price")
        invalidation = self._invalidation_level(watchlist_row)
        reward_risk = None
        if buy_high and invalidation and last_price:
            downside = max(0.01, buy_high - invalidation)
            upside = max(0.0, (buy_high * 1.08) - last_price)
            reward_risk = round(upside / downside, 2)

        blockers = self._constraint_blockers(asset, watchlist_row, exposure, cash_weight)
        return {
            "suggested_weight_pct": round(final_weight * 100, 2),
            "suggested_capital": round(final_weight * assumed_value, 2),
            "cash_weight": round(cash_weight, 4),
            "has_existing_position": has_existing_position,
            "blockers": blockers,
            "reward_risk": reward_risk,
            "invalidation_level": invalidation,
        }

    def _constraint_blockers(
        self,
        asset: AssetORM,
        watchlist_row: dict[str, Any],
        exposure: PortfolioExposureModel,
        cash_weight: float,
    ) -> list[str]:
        blockers: list[str] = []
        limits = self.portfolio_rules["limits"]
        asset_weight = exposure.by_asset.get(asset.symbol, 0.0)
        sector_weight = exposure.by_sector.get(asset.sector, 0.0)
        asset_type_weight = exposure.by_asset_type.get(asset.asset_type, 0.0)

        if asset_weight >= limits["max_asset_weight"]:
            blockers.append("Activo ya en maximo de cartera")
        if sector_weight >= limits["max_sector_weight"]:
            blockers.append("Sector en limite maximo")
        if asset_type_weight >= limits["max_asset_type_weight"].get(asset.asset_type, 1.0):
            blockers.append("Clase de activo en limite maximo")
        if cash_weight < limits["min_cash_target"]:
            blockers.append("Cash por debajo del minimo objetivo")
        if watchlist_row.get("data_mode") != "real":
            blockers.append("Datos no reales")
        if watchlist_row.get("freshness_status") != "fresh":
            blockers.append("Datos no frescos")
        return blockers

    @staticmethod
    def _invalidation_level(watchlist_row: dict[str, Any]) -> float | None:
        support_low = watchlist_row.get("support_low")
        return round(float(support_low), 4) if support_low is not None else None
