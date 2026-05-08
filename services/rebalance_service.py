from __future__ import annotations

from typing import Any

from core.config import load_yaml_config
from core.models import PortfolioExposureModel
from data.database import AssetORM


class RebalanceService:
    def __init__(self, rules: dict | None = None) -> None:
        self.rules = rules or load_yaml_config("portfolio_rules.yaml")

    def portfolio_fit_score(
        self,
        asset: AssetORM,
        exposure: PortfolioExposureModel,
    ) -> tuple[float, dict[str, Any]]:
        limits = self.rules["limits"]
        targets = self.rules["targets"]
        cfg = self.rules["portfolio_fit"]

        asset_weight = exposure.by_asset.get(asset.symbol, 0.0)
        sector_weight = exposure.by_sector.get(asset.sector, 0.0)
        asset_type_weight = exposure.by_asset_type.get(asset.asset_type, 0.0)
        cash_weight = max(0.0, 1 - exposure.total_invested_weight)

        max_asset_weight = limits["max_asset_weight"]
        max_sector_weight = limits["max_sector_weight"]
        max_asset_type_weight = limits["max_asset_type_weight"].get(asset.asset_type, 1.0)
        asset_type_target = targets.get(asset.asset_type, 0.0)
        sector_target = max_sector_weight * 0.7

        score = float(cfg["base_score"])
        reasons: list[str] = []
        breakdown: dict[str, float] = {}

        if asset_weight < max_asset_weight * 0.5:
            score += cfg["asset_underweight_bonus"]
            breakdown["asset_positioning"] = float(cfg["asset_underweight_bonus"])
            reasons.append("Activo infraponderado en cartera")
        elif asset_weight >= max_asset_weight * 0.85:
            score -= cfg["asset_near_limit_penalty"]
            breakdown["asset_positioning"] = -float(cfg["asset_near_limit_penalty"])
            reasons.append("Activo cerca del limite maximo")
        else:
            breakdown["asset_positioning"] = 0.0

        if sector_weight < sector_target:
            score += cfg["sector_underweight_bonus"]
            breakdown["sector_positioning"] = float(cfg["sector_underweight_bonus"])
            reasons.append("Sector relativamente infraponderado")
        elif sector_weight >= max_sector_weight:
            score -= cfg["sector_overweight_penalty"]
            breakdown["sector_positioning"] = -float(cfg["sector_overweight_penalty"])
            reasons.append("Sector saturado")
        else:
            breakdown["sector_positioning"] = 0.0

        if asset_type_weight < asset_type_target:
            score += cfg["asset_type_underweight_bonus"]
            breakdown["asset_type_positioning"] = float(cfg["asset_type_underweight_bonus"])
            reasons.append("Clase de activo infraponderada")
        elif asset_type_weight >= max_asset_type_weight:
            score -= cfg["asset_type_overweight_penalty"]
            breakdown["asset_type_positioning"] = -float(cfg["asset_type_overweight_penalty"])
            reasons.append("Clase de activo sobreponderada")
        else:
            breakdown["asset_type_positioning"] = 0.0

        if len(exposure.by_sector) >= 3 and len(exposure.by_asset_type) >= 2:
            score += cfg["diversification_bonus"]
            breakdown["diversification"] = float(cfg["diversification_bonus"])
            reasons.append("La cartera mantiene cierta diversificacion")
        else:
            breakdown["diversification"] = 0.0

        if cash_weight < limits["min_cash_target"]:
            score -= cfg["low_cash_penalty"]
            breakdown["cash_buffer"] = -float(cfg["low_cash_penalty"])
            reasons.append("Cash por debajo del objetivo")
        else:
            breakdown["cash_buffer"] = 0.0

        total_positions = sum(1 for weight in exposure.by_asset.values() if weight > 0)
        cold_start_penalty = self._cold_start_penalty(
            asset_type=asset.asset_type,
            total_positions=total_positions,
        )
        if cold_start_penalty > 0:
            score -= cold_start_penalty
            breakdown["cold_start_adjustment"] = -float(cold_start_penalty)
            reasons.append("Ajuste prudente de cold start para cartera aun muy vacia")
        else:
            breakdown["cold_start_adjustment"] = 0.0

        final_score = max(0.0, min(100.0, score))
        rationale = {
            "breakdown": {key: round(value, 2) for key, value in breakdown.items()},
            "reasons": reasons,
            "portfolio_fit_score": round(final_score, 2),
            "cash_weight": round(cash_weight, 4),
            "portfolio_fit_adjustment": {
                "cold_start_penalty": round(float(cold_start_penalty), 2),
                "total_positions": total_positions,
            },
        }
        return final_score, rationale

    def _cold_start_penalty(self, *, asset_type: str, total_positions: int) -> float:
        cfg = self.rules.get("portfolio_fit", {})
        threshold = int(cfg.get("cold_start_position_threshold", 3))
        if total_positions > threshold:
            return 0.0

        penalties = cfg.get("cold_start_penalties", {})
        penalty = penalties.get(asset_type, penalties.get(asset_type.upper(), 0.0))
        return float(penalty or 0.0)
