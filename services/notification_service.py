from __future__ import annotations

from dataclasses import dataclass

import httpx

from core.config import get_settings, load_yaml_config
from core.logger import get_logger
from data.database import AlertORM

logger = get_logger(__name__)


@dataclass(slots=True)
class NotificationResult:
    channel: str
    status: str
    error_message: str | None = None


class NotificationService:
    def __init__(self) -> None:
        self.settings = get_settings()
        self.config = load_yaml_config("notifications.yaml")

    def send_alert(self, alert: AlertORM) -> list[NotificationResult]:
        results: list[NotificationResult] = []
        channels = self.config.get("channels", {})
        if channels.get("console", True):
            logger.info("ALERT %s %s - %s", alert.severity.upper(), alert.symbol, alert.title)
            results.append(NotificationResult(channel="console", status="sent"))
        if channels.get("telegram", False):
            results.append(self._send_telegram(alert))
        return results

    def _send_telegram(self, alert: AlertORM) -> NotificationResult:
        severity_enabled = set(self.config.get("telegram", {}).get("enabled_for", []))
        if alert.severity not in severity_enabled:
            return NotificationResult(channel="telegram", status="skipped")
        if not self._telegram_allows_alert_type(alert.alert_type):
            return NotificationResult(channel="telegram", status="skipped")

        if (
            not self.settings.telegram_enabled
            or not self.settings.telegram_bot_token
            or not self.settings.telegram_chat_id
        ):
            return NotificationResult(
                channel="telegram",
                status="disabled",
                error_message="Telegram no configurado",
            )

        payload = {
            "chat_id": self.settings.telegram_chat_id,
            "text": self.build_telegram_message(alert),
        }
        url = f"https://api.telegram.org/bot{self.settings.telegram_bot_token}/sendMessage"
        try:
            response = httpx.post(url, json=payload, timeout=15.0)
            response.raise_for_status()
            return NotificationResult(channel="telegram", status="sent")
        except Exception as exc:  # pragma: no cover - tested via monkeypatch
            logger.warning("Fallo enviando alerta %s por Telegram: %s", alert.id, exc)
            return NotificationResult(channel="telegram", status="error", error_message=str(exc))

    def _telegram_allows_alert_type(self, alert_type: str) -> bool:
        telegram_cfg = self.config.get("telegram", {})
        enabled_alert_types = telegram_cfg.get("enabled_alert_types")
        if enabled_alert_types is not None:
            return alert_type in set(enabled_alert_types)

        type_flags = {
            "entry_signal": telegram_cfg.get("send_entry_alerts", True),
            "risk_deterioration": telegram_cfg.get("send_risk_alerts", True),
            "data_quality": telegram_cfg.get("send_data_alerts", True),
            "watch_signal": telegram_cfg.get("send_watch_alerts", True),
            "portfolio_constraint": telegram_cfg.get("send_portfolio_alerts", True),
            "overbought_warning": telegram_cfg.get("send_sell_alerts", False),
            "take_profit": telegram_cfg.get("send_sell_alerts", False),
            "trim_position": telegram_cfg.get("send_sell_alerts", False),
            "reduce_risk": telegram_cfg.get("send_sell_alerts", False),
            "exit_candidate": telegram_cfg.get("send_sell_alerts", False),
            "stop_loss_warning": telegram_cfg.get("send_sell_alerts", False),
            "rebalance_sell": telegram_cfg.get("send_sell_alerts", False),
            "buy_rsi_25": telegram_cfg.get("send_rsi_cycle_alerts", False),
            "buy_rsi_20": telegram_cfg.get("send_rsi_cycle_alerts", False),
            "buy_bullish_divergence": telegram_cfg.get("send_rsi_cycle_alerts", False),
            "sell_rsi_75": telegram_cfg.get("send_rsi_cycle_alerts", False),
            "sell_rsi_80": telegram_cfg.get("send_rsi_cycle_alerts", False),
            "sell_bearish_divergence": telegram_cfg.get("send_rsi_cycle_alerts", False),
        }
        return type_flags.get(alert_type, True)

    def build_telegram_message(self, alert: AlertORM) -> str:
        payload = alert.payload_json or {}
        lines = [
            f"[{alert.severity.upper()}] {alert.symbol} · {alert.alert_type}",
            alert.title,
            alert.message,
        ]
        if payload.get("alert_group"):
            lines.append(f"Grupo: {payload['alert_group']}")
        if "final_score" in payload:
            lines.append(f"Final score: {payload['final_score']}")
        if "risk_score" in payload:
            lines.append(f"Risk score: {payload['risk_score']}")
        if payload.get("last_price") is not None:
            lines.append(f"Precio: {payload['last_price']}")
        if payload.get("profit_pct") is not None:
            lines.append(f"P/L latente: {payload['profit_pct']}%")
        if payload.get("rsi14") is not None:
            lines.append(f"RSI14: {payload['rsi14']:.2f}")
        if payload.get("current_weight_pct") is not None:
            lines.append(
                f"Peso actual: {payload['current_weight_pct']}%"
                + (
                    f" / objetivo {payload['target_weight_pct']}%"
                    if payload.get("target_weight_pct") is not None
                    else ""
                )
            )
        if payload.get("buy_zone"):
            lines.append(f"Buy zone: {payload['buy_zone']}")
        if payload.get("suggested_weight_add") is not None:
            lines.append(f"Peso sugerido: {payload['suggested_weight_add']}%")
        if payload.get("action_suggestion"):
            lines.append(f"Accion sugerida: {payload['action_suggestion']}")
        if payload.get("strategy_name"):
            lines.append(f"Estrategia: {payload['strategy_name']}")
        if payload.get("invalidation"):
            lines.append(f"Invalidacion: {payload['invalidation']}")
        return "\n".join(lines)
