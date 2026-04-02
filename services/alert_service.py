from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.config import load_yaml_config
from core.logger import get_logger
from data.database import AssetORM, PortfolioPositionORM
from data.repositories.alerts_repo import AlertsRepository
from data.repositories.assets_repo import AssetsRepository
from data.repositories.portfolio_repo import PortfolioRepository
from data.repositories.prices_repo import PricesRepository
from data.repositories.signals_repo import SignalsRepository
from services.notification_service import NotificationService
from services.portfolio_service import PortfolioService
from services.position_management_alerts_service import (
    PositionContext,
    PositionManagementAlertsService,
)
from services.rsi_cycle_alerts_service import RSICycleAlertsService
from services.trade_intent_service import TradeIntentService
from services.watchlist_service import WatchlistService

logger = get_logger(__name__)


@dataclass(slots=True)
class AlertRunSummary:
    scanned_assets: int = 0
    events_detected: int = 0
    alerts_created: int = 0
    alerts_deduplicated: int = 0
    alerts_sent: int = 0
    alerts_failed: int = 0
    alerts_skipped: int = 0
    trade_intents_created: int = 0
    expired_intents: int = 0
    errors: list[str] = field(default_factory=list)


class AlertService:
    def __init__(self, session) -> None:
        self.session = session
        self.config = load_yaml_config("alerts.yaml")
        self.portfolio_rules = load_yaml_config("portfolio_rules.yaml")
        self.alerts_repo = AlertsRepository(session)
        self.assets_repo = AssetsRepository(session)
        self.signals_repo = SignalsRepository(session)
        self.portfolio_repo = PortfolioRepository(session)
        self.prices_repo = PricesRepository(session)
        self.watchlist_service = WatchlistService(session)
        self.portfolio_service = PortfolioService(session)
        self.notification_service = NotificationService()
        self.trade_intent_service = TradeIntentService(session)
        self.position_management_alerts_service = PositionManagementAlertsService()
        self.rsi_cycle_alerts_service = RSICycleAlertsService()

    def scan_market_events(self) -> AlertRunSummary:
        summary = AlertRunSummary()
        rows = self.watchlist_service.get_watchlist_rows()
        rows_by_symbol = {row["symbol"]: row for row in rows}
        assets = {asset.symbol: asset for asset in self.assets_repo.list_enabled()}
        exposure = self.portfolio_service.get_exposures()
        positions_by_asset_id = {
            position.asset_id: position for position in self.portfolio_repo.list_positions()
        }

        for row in rows:
            asset = assets.get(row["symbol"])
            if asset is None:
                continue
            summary.scanned_assets += 1
            try:
                position = positions_by_asset_id.get(asset.id)
                events = self._detect_events(asset, row, exposure, position)
                summary.events_detected += len(events)
                for event in events:
                    self.alerts_repo.create_market_event(event)
            except Exception as exc:  # pragma: no cover
                logger.exception("Error detectando eventos para %s", row["symbol"])
                summary.errors.append(f"{row['symbol']}: {exc}")

        for event in self.alerts_repo.list_unprocessed_events():
            try:
                alert_result = self._process_event(event, exposure, rows_by_symbol)
                if alert_result == "deduplicated":
                    summary.alerts_deduplicated += 1
                elif alert_result == "created":
                    summary.alerts_created += 1
                elif alert_result == "intent_created":
                    summary.alerts_created += 1
                    summary.trade_intents_created += 1
            finally:
                self.alerts_repo.mark_event_processed(event.id)

        summary.expired_intents = self.trade_intent_service.expire_old_intents()
        return summary

    def send_pending_alerts(self) -> AlertRunSummary:
        summary = AlertRunSummary()
        for alert in self.alerts_repo.list_pending():
            results = self.notification_service.send_alert(alert)
            sent = False
            alert_failed = False
            for result in results:
                self.alerts_repo.log_notification(
                    alert_id=alert.id,
                    channel=result.channel,
                    status=result.status,
                    error_message=result.error_message,
                )
                if result.status == "sent":
                    sent = True
                elif result.status == "error":
                    alert_failed = True
            if sent:
                self.alerts_repo.mark_sent(alert.id)
                summary.alerts_sent += 1
            elif alert_failed:
                summary.alerts_failed += 1
            else:
                summary.alerts_skipped += 1
        return summary

    def _detect_events(
        self,
        asset: AssetORM,
        row: dict[str, Any],
        exposure,
        position: PortfolioPositionORM | None,
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        rules = self._rules_for_asset(asset.asset_type)
        previous_signals = self.signals_repo.latest_for_asset(asset.id, limit=2)
        previous_signal = previous_signals[1] if len(previous_signals) > 1 else None

        if row.get("recommendation") == "BUY_CANDIDATE" and float(
            row.get("final_opportunity_score") or 0.0
        ) >= float(rules["min_score_for_entry_alert"]):
            changed = previous_signal is None or previous_signal.recommendation != "BUY_CANDIDATE"
            if changed or self._price_in_buy_zone(row):
                events.append(
                    self._event_payload(
                        asset=asset,
                        event_type="entry_signal",
                        row=row,
                        severity=self._entry_severity(asset, row, rules),
                        title=f"{asset.symbol} activa setup de entrada",
                        message="Nuevo BUY_CANDIDATE con score y riesgo accionables.",
                        alert_group="entry",
                        material_value=self._as_float(row.get("final_opportunity_score")),
                    )
                )

        if (
            rules["enable_watch_alerts"]
            and row.get("recommendation") == "WATCH"
            and float(row.get("final_opportunity_score") or 0.0)
            >= float(rules["min_score_for_watch_alert"])
        ):
            events.append(
                self._event_payload(
                    asset=asset,
                    event_type="watch_signal",
                    row=row,
                    severity="info",
                    title=f"{asset.symbol} en vigilancia reforzada",
                    message="WATCH fuerte cerca de condiciones accionables.",
                    alert_group="entry",
                    material_value=self._as_float(row.get("final_opportunity_score")),
                )
            )

        if rules["enable_risk_alerts"]:
            risk_score = self._as_float(row.get("risk_score"))
            if risk_score is not None and risk_score >= float(
                rules["risk_deterioration_threshold"]
            ):
                events.append(
                    self._event_payload(
                        asset=asset,
                        event_type="risk_deterioration",
                        row=row,
                        severity="critical",
                        title=f"{asset.symbol} deterioro de riesgo",
                        message="El risk score ha entrado en zona exigente.",
                        alert_group="risk",
                        material_value=risk_score,
                    )
                )
            if (
                previous_signal is not None
                and previous_signal.recommendation != row.get("recommendation")
                and row.get("recommendation") == "AVOID"
            ):
                events.append(
                    self._event_payload(
                        asset=asset,
                        event_type="risk_deterioration",
                        row=row,
                        severity="high",
                        title=f"{asset.symbol} empeora la recomendacion",
                        message="La recomendacion ha pasado a AVOID o ha perdido calidad.",
                        alert_group="risk",
                        material_value=self._as_float(row.get("final_opportunity_score")),
                    )
                )

        if rules["enable_data_alerts"] and (
            row.get("freshness_status") != "fresh" or row.get("data_mode") != "real"
        ):
            severity = "warning" if row.get("freshness_status") == "stale" else "high"
            events.append(
                self._event_payload(
                    asset=asset,
                    event_type="data_quality",
                    row=row,
                    severity=severity,
                    title=f"{asset.symbol} con datos no ideales",
                    message="El activo no esta en modo real/fresh y requiere cautela.",
                    alert_group="data",
                    material_value=self._as_float(row.get("last_price")),
                )
            )

        if rules["enable_portfolio_alerts"] and row.get("portfolio_fit_score") is not None:
            portfolio_fit = self._as_float(row.get("portfolio_fit_score"))
            if portfolio_fit is not None and portfolio_fit <= float(
                rules["max_actionable_portfolio_fit_penalty"]
            ):
                events.append(
                    self._event_payload(
                        asset=asset,
                        event_type="portfolio_constraint",
                        row=row,
                        severity="warning",
                        title=f"{asset.symbol} limitado por cartera",
                        message="La cartera actual reduce el encaje de esta señal.",
                        alert_group="risk",
                        material_value=portfolio_fit,
                    )
                )

        if self._has_open_position(position) and rules["enable_sell_alerts"]:
            events.extend(
                self._detect_position_management_events(
                    asset=asset,
                    row=row,
                    position=position,
                    exposure=exposure,
                    rules=rules,
                )
            )
        if rules.get("enable_rsi_cycle_alerts", False):
            events.extend(self._detect_rsi_cycle_events(asset=asset, row=row))
        return events

    def _detect_rsi_cycle_events(
        self,
        *,
        asset: AssetORM,
        row: dict[str, Any],
    ) -> list[dict[str, Any]]:
        frame = self.prices_repo.get_asset_prices(asset.id)
        signals = self.rsi_cycle_alerts_service.detect_latest_signals(frame)
        events: list[dict[str, Any]] = []
        for signal in signals:
            events.append(
                self._event_payload(
                    asset=asset,
                    event_type=signal.event_type,
                    row=row,
                    severity=signal.severity,
                    title=f"{asset.symbol} · {signal.title}",
                    message=signal.message,
                    alert_group=signal.alert_group,
                    material_value=signal.rsi14,
                    position_metrics={
                        "rsi14": signal.rsi14,
                        "signal_price": signal.price,
                        "signal_date": str(signal.signal_date),
                        "strategy_name": "rsi_cycle_strategy",
                    },
                    action_suggestion="Seguir el evento RSI del modo especifico",
                )
            )
        return events

    def _detect_position_management_events(
        self,
        *,
        asset: AssetORM,
        row: dict[str, Any],
        position: PortfolioPositionORM,
        exposure,
        rules: dict[str, Any],
    ) -> list[dict[str, Any]]:
        events: list[dict[str, Any]] = []
        _ = exposure
        position_context = PositionContext(
            quantity=float(position.quantity),
            avg_cost=float(position.avg_cost),
            current_weight=float(position.current_weight),
            target_weight=float(position.target_weight),
        )
        alerts = self.position_management_alerts_service.detect_alerts(
            asset=asset,
            row=row,
            position=position_context,
            rules=rules,
        )
        for alert in alerts:
            events.append(
                self._event_payload(
                    asset=asset,
                    event_type=alert.event_type,
                    row=row,
                    severity=alert.severity,
                    title=alert.title,
                    message=alert.message,
                    alert_group=alert.alert_group,
                    material_value=alert.material_value,
                    position_metrics=alert.position_metrics,
                    action_suggestion=alert.action_suggestion,
                )
            )
        return events

    def _process_event(
        self,
        event,
        exposure,
        rows_by_symbol: dict[str, dict[str, Any]],
    ) -> str:
        asset = self.assets_repo.get_by_symbol(event.symbol)
        latest_row = rows_by_symbol.get(event.symbol)
        if latest_row is None:
            return "deduplicated"

        payload = event.event_payload or {}
        dedupe_key = payload["dedupe_key"]
        rules = self._rules_for_asset(asset.asset_type if asset is not None else "stock")
        material_change = bool(payload.get("material_change", False))
        cooldown_minutes = int(
            rules["cooldown_by_alert_type_minutes"].get(
                event.event_type,
                rules["min_minutes_between_duplicate_alerts"],
            )
        )
        if self.alerts_repo.should_suppress(
            dedupe_key=dedupe_key,
            cooldown_minutes=cooldown_minutes,
            severity=payload["severity"],
            material_change=material_change,
        ):
            logger.info("%s suppresssed by cooldown for %s", event.event_type, event.symbol)
            return "deduplicated"

        alert = self.alerts_repo.create_alert(
            {
                "asset_id": event.asset_id,
                "symbol": event.symbol,
                "alert_type": event.event_type,
                "severity": payload["severity"],
                "title": payload["title"],
                "message": payload["message"],
                "payload_json": payload["alert_payload"],
                "status": "new",
                "delivery_channels": ["ui", "telegram", "console"],
                "dedupe_key": dedupe_key,
            }
        )
        if asset is not None:
            intent = self.trade_intent_service.maybe_create_intent(
                asset=asset,
                alert=alert,
                watchlist_row=latest_row,
                exposure=exposure,
            )
            if intent is not None and getattr(intent, "id", None) is not None:
                return "intent_created"
        return "created"

    def _event_payload(
        self,
        *,
        asset: AssetORM,
        event_type: str,
        row: dict[str, Any],
        severity: str,
        title: str,
        message: str,
        alert_group: str,
        material_value: float | None = None,
        position_metrics: dict[str, Any] | None = None,
        action_suggestion: str | None = None,
    ) -> dict[str, Any]:
        dedupe_key = f"{asset.symbol}:{event_type}"
        return {
            "asset_id": asset.id,
            "symbol": asset.symbol,
            "event_type": event_type,
            "event_payload": {
                "severity": severity,
                "title": title,
                "message": message,
                "dedupe_key": dedupe_key,
                "material_change": self._material_change(
                    dedupe_key=dedupe_key,
                    event_type=event_type,
                    current_value=material_value,
                ),
                "alert_payload": {
                    "symbol": asset.symbol,
                    "name": asset.name,
                    "asset_type": asset.asset_type,
                    "alert_group": alert_group,
                    "final_score": row.get("final_opportunity_score"),
                    "risk_score": row.get("risk_score"),
                    "last_price": row.get("last_price"),
                    "buy_zone": [row.get("suggested_buy_low"), row.get("suggested_buy_high")],
                    "suggested_weight_add": row.get("suggested_weight_add"),
                    "invalidation": row.get("reasons", [""])[0] if row.get("reasons") else None,
                    "recommendation": row.get("recommendation"),
                    "data_mode": row.get("data_mode"),
                    "freshness_status": row.get("freshness_status"),
                    "portfolio_fit_score": row.get("portfolio_fit_score"),
                    "score_breakdown": row.get("score_breakdown", {}),
                    "reasons": row.get("reasons", []),
                    "material_value": material_value,
                    "action_suggestion": action_suggestion,
                    **(position_metrics or {}),
                },
            },
        }

    def _material_change(
        self,
        *,
        dedupe_key: str,
        event_type: str,
        current_value: float | None,
    ) -> bool:
        latest = self.alerts_repo.latest_by_dedupe_key(dedupe_key)
        if latest is None or not latest.payload_json:
            return True
        previous_value = latest.payload_json.get("material_value")
        if previous_value is None or current_value is None:
            return False
        delta = abs(float(current_value) - float(previous_value))
        threshold = float(
            self.config["rules"]
            .get("material_change_thresholds", {})
            .get(event_type, self.config["rules"]["score_material_change_delta"])
        )
        return delta >= threshold

    @staticmethod
    def _price_in_buy_zone(row: dict[str, Any]) -> bool:
        last_price = row.get("last_price")
        low = row.get("suggested_buy_low")
        high = row.get("suggested_buy_high")
        if last_price is None or low is None or high is None:
            return False
        return float(low) <= float(last_price) <= float(high)

    def _entry_severity(self, asset: AssetORM, row: dict[str, Any], rules: dict[str, Any]) -> str:
        high_priority = asset.asset_type in set(rules["preferred_universes_for_high_priority"])
        high_score = float(row.get("final_opportunity_score") or 0.0) >= float(
            rules["min_score_for_high_alert"]
        )
        low_risk = float(row.get("risk_score") or 100.0) <= float(
            rules["max_risk_for_actionable_alert"]
        )
        if high_priority and high_score and low_risk:
            return "high"
        return "warning"

    def _rules_for_asset(self, asset_type: str) -> dict[str, Any]:
        base = dict(self.config["rules"])
        overrides = base.get("universe_overrides", {}).get(asset_type, {})
        merged = {**base, **overrides}
        return merged

    @staticmethod
    def _has_open_position(position: PortfolioPositionORM | None) -> bool:
        if position is None:
            return False
        return position.quantity > 0 or position.current_weight > 0

    @staticmethod
    def _as_float(value: Any) -> float | None:
        if value is None:
            return None
        return float(value)
