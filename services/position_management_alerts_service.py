from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.config import load_yaml_config
from data.database import AssetORM


@dataclass(slots=True)
class PositionContext:
    quantity: float
    avg_cost: float
    current_weight: float
    target_weight: float


@dataclass(slots=True)
class PositionManagementAlert:
    event_type: str
    severity: str
    title: str
    message: str
    alert_group: str
    material_value: float | None
    action_suggestion: str
    position_metrics: dict[str, Any]


class PositionManagementAlertsService:
    """Reglas reutilizables de alertas para posiciones abiertas.

    Esta capa extrae la logica de gestion de posiciones para que pueda usarse
    tanto en alertas live como en backtesting historico sin duplicar reglas.
    """

    def __init__(self) -> None:
        self.config = load_yaml_config("alerts.yaml")
        self.portfolio_rules = load_yaml_config("portfolio_rules.yaml")

    def detect_alerts(
        self,
        *,
        asset: AssetORM,
        row: dict[str, Any],
        position: PositionContext,
        rules: dict[str, Any] | None = None,
    ) -> list[PositionManagementAlert]:
        active_rules = rules or dict(self.config["rules"])
        alerts: list[PositionManagementAlert] = []

        last_price = self._as_float(row.get("last_price"))
        avg_cost = self._as_float(position.avg_cost)
        current_weight = self._as_float(position.current_weight) or 0.0
        target_weight = self._as_float(position.target_weight) or 0.0
        rsi14 = self._as_float(row.get("rsi14"))
        sma50 = self._as_float(row.get("sma50"))
        support_low = self._as_float(row.get("support_low"))
        final_score = self._as_float(row.get("final_opportunity_score"))
        risk_score = self._as_float(row.get("risk_score"))
        recommendation = str(row.get("recommendation") or "")

        profit_pct = None
        if avg_cost is not None and avg_cost > 0 and last_price is not None:
            profit_pct = round(((last_price - avg_cost) / avg_cost) * 100, 2)

        extension_above_sma50_pct = None
        if sma50 is not None and sma50 > 0 and last_price is not None:
            extension_above_sma50_pct = (last_price - sma50) / sma50

        weight_excess_vs_target = current_weight - target_weight
        max_asset_weight = float(self.portfolio_rules["limits"]["max_asset_weight"])
        position_metrics = self._position_metrics(position, profit_pct)

        if (
            active_rules["enable_overbought_alerts"]
            and rsi14 is not None
            and rsi14 >= float(active_rules["overbought_rsi_threshold"])
            and (
                extension_above_sma50_pct is None
                or extension_above_sma50_pct
                >= float(active_rules["overbought_extension_pct_above_sma50"])
            )
        ):
            alerts.append(
                PositionManagementAlert(
                    event_type="overbought_warning",
                    severity="warning",
                    title=f"{asset.symbol} en sobrecompra",
                    message="La posicion esta extendida y podria requerir vigilancia.",
                    alert_group="position_management",
                    material_value=profit_pct if profit_pct is not None else rsi14,
                    position_metrics=position_metrics,
                    action_suggestion="Vigilar posible reduccion parcial si pierde momentum",
                )
            )

        if (
            active_rules["enable_take_profit_alerts"]
            and profit_pct is not None
            and profit_pct >= float(active_rules["take_profit_min_profit_pct"])
            and (
                (rsi14 is not None and rsi14 >= float(active_rules["take_profit_min_rsi14"]))
                or recommendation in {"WATCH", "AVOID"}
            )
        ):
            alerts.append(
                PositionManagementAlert(
                    event_type="take_profit",
                    severity="high",
                    title=f"{asset.symbol} candidata a toma de beneficios",
                    message=(
                        "El beneficio latente y la extension tecnica justifican "
                        "evaluar una toma parcial."
                    ),
                    alert_group="position_management",
                    material_value=profit_pct,
                    position_metrics=position_metrics,
                    action_suggestion="Valorar toma parcial de beneficios",
                )
            )

        if (
            active_rules["enable_trim_position_alerts"]
            and (
                current_weight >= max_asset_weight
                or weight_excess_vs_target >= float(active_rules["trim_weight_excess_pct"])
            )
        ):
            alerts.append(
                PositionManagementAlert(
                    event_type="trim_position",
                    severity="high" if current_weight >= max_asset_weight else "warning",
                    title=f"{asset.symbol} pesa demasiado en cartera",
                    message=(
                        "La posicion ha crecido por encima de lo deseable y podria "
                        "requerir recorte parcial."
                    ),
                    alert_group="position_management",
                    material_value=current_weight,
                    position_metrics=position_metrics,
                    action_suggestion="Reducir parcialmente para normalizar el peso",
                )
            )

        if (
            active_rules["enable_reduce_risk_alerts"]
            and (
                (
                    risk_score is not None
                    and risk_score >= float(active_rules["reduce_risk_score_threshold"])
                )
                or (sma50 is not None and last_price is not None and last_price < sma50)
            )
        ):
            alerts.append(
                PositionManagementAlert(
                    event_type="reduce_risk",
                    severity="high",
                    title=f"{asset.symbol} aconseja reducir riesgo",
                    message="El deterioro tecnico o de riesgo sugiere bajar exposicion.",
                    alert_group="position_management",
                    material_value=risk_score if risk_score is not None else final_score,
                    position_metrics=position_metrics,
                    action_suggestion="Reducir exposicion o endurecer seguimiento del stop",
                )
            )

        stop_buffer = float(active_rules["stop_loss_buffer_pct"])
        if (
            active_rules["enable_stop_loss_alerts"]
            and support_low is not None
            and last_price is not None
            and last_price <= support_low * (1 + stop_buffer)
        ):
            severity = "critical" if last_price < support_low else "high"
            alerts.append(
                PositionManagementAlert(
                    event_type="stop_loss_warning",
                    severity=severity,
                    title=f"{asset.symbol} cerca de invalidacion",
                    message="El precio esta cerca del nivel de invalidacion o ya lo ha perforado.",
                    alert_group="position_management",
                    material_value=(last_price - support_low) / support_low,
                    position_metrics=position_metrics,
                    action_suggestion="Revisar stop tecnico y posible salida",
                )
            )

        if (
            active_rules["enable_exit_candidate_alerts"]
            and (
                recommendation == "AVOID"
                or (
                    final_score is not None
                    and final_score <= float(active_rules["exit_final_score_threshold"])
                )
                or (
                    risk_score is not None
                    and risk_score >= float(active_rules["exit_risk_score_threshold"])
                )
                or (support_low is not None and last_price is not None and last_price < support_low)
            )
        ):
            alerts.append(
                PositionManagementAlert(
                    event_type="exit_candidate",
                    severity="critical",
                    title=f"{asset.symbol} salida candidata",
                    message=(
                        "La posicion ha perdido calidad suficiente como para "
                        "evaluar una salida seria."
                    ),
                    alert_group="position_management",
                    material_value=final_score if final_score is not None else risk_score,
                    position_metrics=position_metrics,
                    action_suggestion="Evaluar salida total o reduccion agresiva",
                )
            )

        if (
            active_rules["enable_rebalance_sell_alerts"]
            and current_weight
            >= max_asset_weight + float(active_rules["rebalance_weight_excess_pct"])
        ):
            alerts.append(
                PositionManagementAlert(
                    event_type="rebalance_sell",
                    severity="warning",
                    title=f"{asset.symbol} pide rebalanceo",
                    message=(
                        "La posicion esta sobreponderada frente al limite de cartera "
                        "aunque el activo no este roto."
                    ),
                    alert_group="position_management",
                    material_value=current_weight,
                    position_metrics=position_metrics,
                    action_suggestion="Rebalancear vendiendo una parte de la posicion",
                )
            )

        return alerts

    @staticmethod
    def _position_metrics(
        position: PositionContext,
        profit_pct: float | None,
    ) -> dict[str, Any]:
        return {
            "quantity": position.quantity,
            "avg_cost": position.avg_cost,
            "profit_pct": profit_pct,
            "current_weight_pct": round(position.current_weight * 100, 2),
            "target_weight_pct": round(position.target_weight * 100, 2),
        }

    @staticmethod
    def _as_float(value: Any) -> float | None:
        if value is None:
            return None
        return float(value)
