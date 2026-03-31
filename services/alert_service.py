from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.config import load_yaml_config
from core.logger import get_logger
from data.database import AssetORM
from data.repositories.alerts_repo import AlertsRepository
from data.repositories.assets_repo import AssetsRepository
from data.repositories.signals_repo import SignalsRepository
from services.notification_service import NotificationService
from services.portfolio_service import PortfolioService
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
        self.alerts_repo = AlertsRepository(session)
        self.assets_repo = AssetsRepository(session)
        self.signals_repo = SignalsRepository(session)
        self.watchlist_service = WatchlistService(session)
        self.portfolio_service = PortfolioService(session)
        self.notification_service = NotificationService()
        self.trade_intent_service = TradeIntentService(session)

    def scan_market_events(self) -> AlertRunSummary:
        summary = AlertRunSummary()
        rows = self.watchlist_service.get_watchlist_rows()
        rows_by_symbol = {row["symbol"]: row for row in rows}
        assets = {asset.symbol: asset for asset in self.assets_repo.list_enabled()}
        exposure = self.portfolio_service.get_exposures()

        for row in rows:
            asset = assets.get(row["symbol"])
            if asset is None:
                continue
            summary.scanned_assets += 1
            try:
                events = self._detect_events(asset, row, exposure)
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
                )
            )

        if rules["enable_risk_alerts"]:
            if row.get("risk_score") is not None and float(row["risk_score"]) >= float(
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
                    )
                )
            if (
                previous_signal is not None
                and previous_signal.recommendation != row.get("recommendation")
            ):
                if row.get("recommendation") == "AVOID":
                    events.append(
                        self._event_payload(
                            asset=asset,
                            event_type="risk_deterioration",
                            row=row,
                            severity="high",
                            title=f"{asset.symbol} empeora la recomendacion",
                            message="La recomendacion ha pasado a AVOID o ha perdido calidad.",
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
                )
            )

        if rules["enable_portfolio_alerts"] and row.get("portfolio_fit_score") is not None:
            if float(row["portfolio_fit_score"]) <= float(
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
    ) -> dict[str, Any]:
        return {
            "asset_id": asset.id,
            "symbol": asset.symbol,
            "event_type": event_type,
            "event_payload": {
                "severity": severity,
                "title": title,
                "message": message,
                "dedupe_key": f"{asset.symbol}:{event_type}",
                "material_change": self._material_change(asset, row),
                "alert_payload": {
                    "symbol": asset.symbol,
                    "name": asset.name,
                    "asset_type": asset.asset_type,
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
                },
            },
        }

    def _material_change(self, asset: AssetORM, row: dict[str, Any]) -> bool:
        latest = self.alerts_repo.latest_by_dedupe_key(f"{asset.symbol}:entry_signal")
        if latest is None or not latest.payload_json:
            return True
        previous_score = latest.payload_json.get("final_score")
        if previous_score is None or row.get("final_opportunity_score") is None:
            return False
        delta = abs(float(row["final_opportunity_score"]) - float(previous_score))
        return delta >= float(self.config["rules"]["score_material_change_delta"])

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
