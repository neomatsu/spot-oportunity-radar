from __future__ import annotations

import pandas as pd

from core.config import load_yaml_config
from core.enums import RiskLevel
from core.models import RiskAssessmentModel


class RiskService:
    def __init__(self, rules: dict | None = None) -> None:
        self.rules = rules or load_yaml_config("risk_rules.yaml")

    def assess_risk(
        self,
        price_frame: pd.DataFrame,
        *,
        asset_type: str,
        current_asset_weight: float = 0.0,
        current_sector_weight: float = 0.0,
        current_asset_type_weight: float = 0.0,
    ) -> RiskAssessmentModel:
        if price_frame.empty or len(price_frame) < 30:
            base_risk = float(self.rules["asset_type_base_risk"].get(asset_type, 60))
            return RiskAssessmentModel(
                risk_score=base_risk,
                risk_level=self._risk_level(base_risk),
                rationale={
                    "reason": "insufficient_price_history",
                    "breakdown": {
                        "volatility_component": 0.0,
                        "drawdown_component": 0.0,
                        "concentration_component": 0.0,
                        "asset_type_component": round(base_risk, 2),
                    },
                    "reasons": ["Historial insuficiente, usando riesgo base por tipo de activo"],
                },
            )

        returns = price_frame["close"].pct_change().dropna()
        annualized_vol = float(returns.std() * (252**0.5))
        drawdown = self._max_drawdown(price_frame["close"])

        breakdown = self._build_breakdown(
            annualized_vol=annualized_vol,
            drawdown=drawdown,
            asset_type=asset_type,
            current_asset_weight=current_asset_weight,
            current_sector_weight=current_sector_weight,
            current_asset_type_weight=current_asset_type_weight,
        )
        risk_score = round(sum(breakdown.values()), 2)
        risk_level = self._risk_level(risk_score)

        return RiskAssessmentModel(
            risk_score=risk_score,
            risk_level=risk_level,
            rationale={
                "annualized_volatility": round(annualized_vol, 4),
                "max_drawdown": round(drawdown, 4),
                "breakdown": {key: round(value, 2) for key, value in breakdown.items()},
                "reasons": self._build_reasons(
                    annualized_vol=annualized_vol,
                    drawdown=drawdown,
                    asset_type=asset_type,
                    current_asset_weight=current_asset_weight,
                    current_sector_weight=current_sector_weight,
                ),
            },
        )

    def _build_breakdown(
        self,
        *,
        annualized_vol: float,
        drawdown: float,
        asset_type: str,
        current_asset_weight: float,
        current_sector_weight: float,
        current_asset_type_weight: float,
    ) -> dict[str, float]:
        components = self.rules["components"]
        concentration_rules = self.rules["concentration"]

        volatility_component = self._scale(
            annualized_vol,
            self.rules["volatility"]["annualized_low"],
            self.rules["volatility"]["annualized_high"],
        ) * components["volatility_weight"]
        drawdown_component = self._scale(
            drawdown,
            self.rules["drawdown"]["moderate"],
            self.rules["drawdown"]["severe"],
        ) * components["drawdown_weight"]

        asset_penalty = self._scale(
            current_asset_weight,
            concentration_rules["asset_overweight"],
            concentration_rules["asset_overweight"] * 1.5,
        ) * concentration_rules["asset_max_penalty"]
        sector_penalty = self._scale(
            current_sector_weight,
            concentration_rules["sector_overweight"],
            concentration_rules["sector_overweight"] * 1.4,
        ) * concentration_rules["sector_max_penalty"]
        asset_type_penalty = self._scale(
            current_asset_type_weight,
            concentration_rules["asset_type_overweight"],
            concentration_rules["asset_type_overweight"] * 1.4,
        ) * concentration_rules["asset_type_max_penalty"]
        concentration_component = min(
            components["concentration_weight"],
            asset_penalty + sector_penalty + asset_type_penalty,
        )

        asset_type_component = (
            float(self.rules["asset_type_base_risk"].get(asset_type, 60)) / 100
        ) * components["asset_type_weight"]

        return {
            "volatility_component": volatility_component,
            "drawdown_component": drawdown_component,
            "concentration_component": concentration_component,
            "asset_type_component": asset_type_component,
        }

    @staticmethod
    def _build_reasons(
        *,
        annualized_vol: float,
        drawdown: float,
        asset_type: str,
        current_asset_weight: float,
        current_sector_weight: float,
    ) -> list[str]:
        reasons: list[str] = []
        if annualized_vol > 0.4:
            reasons.append("Volatilidad reciente elevada")
        elif annualized_vol > 0.22:
            reasons.append("Volatilidad moderada")
        else:
            reasons.append("Volatilidad contenida")

        if drawdown > 0.25:
            reasons.append("Drawdown historico exigente")
        elif drawdown > 0.12:
            reasons.append("Drawdown moderado")

        if asset_type == "crypto":
            reasons.append("Activo crypto con prima de riesgo estructural")
        elif asset_type == "stock":
            reasons.append("Accion individual con riesgo superior a ETF amplio")
        else:
            reasons.append("ETF amplio con riesgo base mas bajo")

        if current_asset_weight > 0.10:
            reasons.append("El activo ya pesa bastante en cartera")
        if current_sector_weight > 0.25:
            reasons.append("El sector ya tiene una exposicion relevante")
        return reasons

    @staticmethod
    def _max_drawdown(series: pd.Series) -> float:
        rolling_max = series.cummax()
        drawdown = (series / rolling_max) - 1
        return abs(float(drawdown.min()))

    @staticmethod
    def _scale(value: float, low: float, high: float) -> float:
        if value <= low:
            return 0.0
        if value >= high:
            return 1.0
        return (value - low) / (high - low)

    def _risk_level(self, score: float) -> RiskLevel:
        if score <= self.rules["risk_level_thresholds"]["low_max"]:
            return RiskLevel.LOW
        if score <= self.rules["risk_level_thresholds"]["medium_max"]:
            return RiskLevel.MEDIUM
        return RiskLevel.HIGH
