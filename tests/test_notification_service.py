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


def test_bitcoin_opportunity_message_contains_threshold_and_percentage() -> None:
    alert = _build_alert()
    alert.symbol = "BTCUSDT"
    alert.alert_type = "bitcoin_opportunity_buy"
    alert.payload_json = {
        "opportunity_score": 82.4,
        "crossed_thresholds": [70, 75, 80],
        "recommended_trade_pct": 90.0,
        "recommended_trade_basis": "capital base",
        "action_suggestion": "Comprar 90% del capital base",
    }

    message = NotificationService().build_telegram_message(alert)

    assert "Bitcoin Opportunity: 82.40/100" in message
    assert "Umbrales cruzados: 70/75/80" in message
    assert "Porcentaje sugerido: 90% del capital base" in message


def test_sp500_opportunity_message_contains_threshold_and_percentage() -> None:
    alert = _build_alert()
    alert.symbol = "^GSPC"
    alert.alert_type = "sp500_opportunity_buy"
    alert.payload_json = {
        "sp500_opportunity_score": 63.1,
        "crossed_thresholds": [60, 62.5],
        "recommended_trade_pct": 100.0,
        "recommended_trade_basis": "cash disponible",
        "action_suggestion": "Invertir 100% del cash disponible",
    }

    message = NotificationService().build_telegram_message(alert)

    assert "S&P 500 Opportunity: 63.10/100" in message
    assert "Umbrales cruzados: 60/62.5" in message
    assert "Porcentaje sugerido: 100% del cash disponible" in message


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


def test_portfolio_asset_respects_exact_alert_type_filter() -> None:
    service = NotificationService()
    service.config["channels"]["telegram"] = True
    service.config["telegram"]["enabled_for"] = ["high"]
    service.config["telegram"]["enabled_alert_types"] = ["entry_signal"]
    alert = _build_alert()
    alert.alert_type = "risk_deterioration"
    alert.payload_json = {"is_portfolio_asset": True}

    results = service.send_alert(alert)
    telegram_result = next(result for result in results if result.channel == "telegram")

    assert telegram_result.status == "skipped"


def test_planned_entry_alert_is_allowed_and_message_lists_level() -> None:
    service = NotificationService()
    service.config["telegram"]["enabled_alert_types"] = ["manual_buy_level_near"]
    alert = _build_alert()
    alert.alert_type = "manual_buy_level_near"
    alert.payload_json = {
        "last_price": 100.5,
        "planned_entry_levels": [
            {"target_price": 100.0, "distance_pct": 0.5},
        ],
        "recommended_trade_pct": 7.5,
        "recommended_trade_basis": "capital",
    }

    assert service._telegram_allows_alert_type(alert.alert_type)
    message = service.build_telegram_message(alert)
    assert "Niveles planificados: 100.00 (+0.50%)" in message
    assert "Porcentaje sugerido: 7.5%" in message


def test_telegram_can_send_take_profit_when_type_is_allowed(monkeypatch) -> None:
    service = NotificationService()
    service.config["channels"]["telegram"] = True
    service.settings.telegram_enabled = True
    service.settings.telegram_bot_token = "token"
    service.settings.telegram_chat_id = "chat-id"
    service.config["telegram"]["enabled_for"] = ["high"]
    service.config["telegram"]["enabled_alert_types"] = ["take_profit"]

    sent_payload: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

    def _fake_post(url, json, timeout):
        sent_payload["url"] = url
        sent_payload["json"] = json
        return FakeResponse()

    monkeypatch.setattr("services.notification_service.httpx.post", _fake_post)
    alert = _build_alert()
    alert.alert_type = "take_profit"
    alert.payload_json["profit_pct"] = 22.4
    alert.payload_json["current_weight_pct"] = 14.0
    alert.payload_json["target_weight_pct"] = 8.0
    alert.payload_json["action_suggestion"] = "Valorar toma parcial"

    results = service.send_alert(alert)
    telegram_result = next(result for result in results if result.channel == "telegram")

    assert telegram_result.status == "sent"
    assert "take_profit" in sent_payload["json"]["text"]


def test_telegram_can_send_rsi_cycle_alert_when_type_is_allowed(monkeypatch) -> None:
    service = NotificationService()
    service.config["channels"]["telegram"] = True
    service.settings.telegram_enabled = True
    service.settings.telegram_bot_token = "token"
    service.settings.telegram_chat_id = "chat-id"
    service.config["telegram"]["enabled_for"] = ["high"]
    service.config["telegram"]["enabled_alert_types"] = ["buy_rsi_25"]

    sent_payload: dict[str, object] = {}

    class FakeResponse:
        def raise_for_status(self) -> None:
            return None

    def _fake_post(url, json, timeout):
        sent_payload["json"] = json
        return FakeResponse()

    monkeypatch.setattr("services.notification_service.httpx.post", _fake_post)
    alert = _build_alert()
    alert.alert_type = "buy_rsi_25"
    alert.payload_json["rsi14"] = 24.12
    alert.payload_json["strategy_name"] = "rsi_cycle_strategy"

    results = service.send_alert(alert)
    telegram_result = next(result for result in results if result.channel == "telegram")

    assert telegram_result.status == "sent"
    assert "buy_rsi_25" in sent_payload["json"]["text"]
    assert "RSI14: 24.12" in sent_payload["json"]["text"]
