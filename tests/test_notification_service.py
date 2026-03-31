from __future__ import annotations

from data.database import AlertORM
from services.notification_service import NotificationService


def _build_alert() -> AlertORM:
    return AlertORM(
        symbol="MSFT",
        alert_type="entry_signal",
        severity="high",
        title="MSFT activa setup de entrada",
        message="Nuevo BUY_CANDIDATE con score y riesgo accionables.",
        payload_json={
            "final_score": 78.5,
            "risk_score": 34.2,
            "last_price": 420.15,
            "buy_zone": [410.0, 425.0],
            "suggested_weight_add": 4.5,
            "invalidation": "Perdida de soporte en 404.0",
        },
        status="new",
        dedupe_key="MSFT:entry_signal",
    )


def test_telegram_disabled_without_credentials() -> None:
    service = NotificationService()
    service.config["channels"]["telegram"] = True
    service.settings.telegram_enabled = True
    service.settings.telegram_bot_token = None
    service.settings.telegram_chat_id = None

    results = service.send_alert(_build_alert())
    telegram_result = next(result for result in results if result.channel == "telegram")

    assert telegram_result.status == "disabled"
    assert telegram_result.error_message == "Telegram no configurado"


def test_build_telegram_message_contains_key_fields() -> None:
    message = NotificationService().build_telegram_message(_build_alert())

    assert "[HIGH] MSFT" in message
    assert "Final score: 78.5" in message
    assert "Risk score: 34.2" in message
    assert "Buy zone: [410.0, 425.0]" in message


def test_telegram_failure_does_not_break_pipeline(monkeypatch) -> None:
    service = NotificationService()
    service.config["channels"]["telegram"] = True
    service.settings.telegram_enabled = True
    service.settings.telegram_bot_token = "token"
    service.settings.telegram_chat_id = "chat-id"

    def _boom(*args, **kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr("services.notification_service.httpx.post", _boom)
    results = service.send_alert(_build_alert())
    telegram_result = next(result for result in results if result.channel == "telegram")

    assert telegram_result.status == "error"
    assert "network down" in (telegram_result.error_message or "")


def test_telegram_can_filter_exact_alert_types() -> None:
    service = NotificationService()
    service.config["channels"]["telegram"] = True
    service.settings.telegram_enabled = True
    service.settings.telegram_bot_token = "token"
    service.settings.telegram_chat_id = "chat-id"
    service.config["telegram"]["enabled_for"] = ["high"]
    service.config["telegram"]["enabled_alert_types"] = ["entry_signal"]

    risk_alert = _build_alert()
    risk_alert.alert_type = "risk_deterioration"
    results = service.send_alert(risk_alert)
    telegram_result = next(result for result in results if result.channel == "telegram")

    assert telegram_result.status == "skipped"
