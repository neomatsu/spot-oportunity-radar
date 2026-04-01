from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import streamlit as st

from core.config import get_settings, load_yaml_config  # noqa: E402
from data.database import init_db, session_scope  # noqa: E402
from data.repositories.alerts_repo import AlertsRepository  # noqa: E402
from data.repositories.trade_intents_repo import TradeIntentsRepository  # noqa: E402
from services.alert_service import AlertService  # noqa: E402

st.title("Alerts")
st.caption(
    "Centro operativo de alertas, estado de datos y trade intents. El sistema "
    "prepara decisiones, pero no ejecuta broker automáticamente."
)

init_db()
settings = get_settings()
notifications_cfg = load_yaml_config("notifications.yaml")

action_col1, action_col2 = st.columns(2)
scan_clicked = action_col1.button("Escanear eventos y generar alertas", use_container_width=True)
send_clicked = action_col2.button("Enviar alertas pendientes", use_container_width=True)

if scan_clicked:
    with session_scope() as session:
        summary = AlertService(session).scan_market_events()
    st.success(
        f"Scan completado. Eventos: {summary.events_detected}, alertas nuevas: "
        f"{summary.alerts_created}, deduplicadas: {summary.alerts_deduplicated}, "
        f"trade intents: {summary.trade_intents_created}."
    )

if send_clicked:
    with session_scope() as session:
        summary = AlertService(session).send_pending_alerts()
    st.success(f"Alertas enviadas: {summary.alerts_sent}")

with session_scope() as session:
    alerts_repo = AlertsRepository(session)
    intents_repo = TradeIntentsRepository(session)
    alerts = alerts_repo.list_recent()
    intents = intents_repo.list_recent()
    notification_logs = alerts_repo.list_notification_logs()

pending_alerts = sum(1 for alert in alerts if alert.status == "new")
open_intents = sum(
    1 for intent in intents if intent.status in {"new", "reviewed", "approved"}
)
telegram_state = (
    "on" if settings.telegram_enabled and settings.telegram_bot_token else "off"
)

summary_cols = st.columns(5)
summary_cols[0].metric("Alertas recientes", len(alerts))
summary_cols[1].metric("Alertas pendientes", pending_alerts)
summary_cols[2].metric("Trade intents abiertos", open_intents)
summary_cols[3].metric("Telegram", telegram_state)
summary_cols[4].metric("Modo demo", "on" if settings.demo_mode else "off")

tab_active, tab_history, tab_intents, tab_config = st.tabs(
    ["Alertas activas", "Historial", "Trade intents", "Config"]
)

alerts_df = pd.DataFrame(
    [
        {
            "id": alert.id,
            "symbol": alert.symbol,
            "alert_group": (alert.payload_json or {}).get("alert_group", "other"),
            "alert_type": alert.alert_type,
            "severity": alert.severity,
            "title": alert.title,
            "status": alert.status,
            "created_at": alert.created_at,
            "sent_at": alert.sent_at,
            "channels": ",".join(alert.delivery_channels or []),
        }
        for alert in alerts
    ]
)
intents_df = pd.DataFrame(
    [
        {
            "id": intent.id,
            "symbol": intent.symbol,
            "status": intent.status,
            "recommendation": intent.recommendation,
            "final_score": intent.final_score,
            "risk_score": intent.risk_score,
            "suggested_weight_add": intent.suggested_weight_add,
            "suggested_capital": intent.suggested_capital,
            "created_at": intent.created_at,
            "reviewed_at": intent.reviewed_at,
        }
        for intent in intents
    ]
)

alert_severity_options = (
    sorted(alerts_df["severity"].unique().tolist()) if not alerts_df.empty else []
)
alert_group_options = (
    sorted(alerts_df["alert_group"].unique().tolist()) if not alerts_df.empty else []
)
alert_type_options = (
    sorted(alerts_df["alert_type"].unique().tolist()) if not alerts_df.empty else []
)
alert_status_options = (
    sorted(alerts_df["status"].unique().tolist()) if not alerts_df.empty else []
)
default_alert_statuses = [
    status for status in ["new", "sent"] if status in alert_status_options
]

with tab_active:
    filter_col1, filter_col2, filter_col3, filter_col4 = st.columns(4)
    severity_filter = filter_col1.multiselect(
        "Filtrar severidad",
        options=alert_severity_options,
        default=alert_severity_options,
    )
    group_filter = filter_col2.multiselect(
        "Filtrar grupo",
        options=alert_group_options,
        default=alert_group_options,
    )
    type_filter = filter_col3.multiselect(
        "Filtrar tipo",
        options=alert_type_options,
        default=alert_type_options,
    )
    status_filter = filter_col4.multiselect(
        "Filtrar estado",
        options=alert_status_options,
        default=default_alert_statuses,
    )
    active_df = alerts_df.copy()
    if not active_df.empty:
        if severity_filter:
            active_df = active_df[active_df["severity"].isin(severity_filter)]
        if group_filter:
            active_df = active_df[active_df["alert_group"].isin(group_filter)]
        if type_filter:
            active_df = active_df[active_df["alert_type"].isin(type_filter)]
        if status_filter:
            active_df = active_df[active_df["status"].isin(status_filter)]
        st.dataframe(active_df, use_container_width=True, hide_index=True)

        selected_alert_id = st.selectbox(
            "Detalle de alerta",
            options=active_df["id"].tolist(),
            format_func=lambda alert_id: f"Alerta #{alert_id}",
        )
        selected_alert = next(alert for alert in alerts if alert.id == selected_alert_id)
        st.json(selected_alert.payload_json or {})

        new_status = st.selectbox(
            "Cambiar estado de alerta",
            ["new", "sent", "acknowledged", "resolved", "ignored"],
            index=["new", "sent", "acknowledged", "resolved", "ignored"].index(
                selected_alert.status
            ),
        )
        if st.button("Actualizar estado alerta"):
            with session_scope() as session:
                AlertsRepository(session).update_status(selected_alert_id, new_status)
            st.success("Estado de alerta actualizado.")
            st.rerun()
    else:
        st.info("Todavia no hay alertas registradas.")

with tab_history:
    history_df = alerts_df.copy()
    if not history_df.empty:
        st.dataframe(history_df, use_container_width=True, hide_index=True)
        logs_df = pd.DataFrame(
            [
                {
                    "alert_id": log.alert_id,
                    "channel": log.channel,
                    "status": log.status,
                    "attempted_at": log.attempted_at,
                    "error_message": log.error_message,
                }
                for log in notification_logs
            ]
        )
        st.subheader("Notification log")
        st.dataframe(logs_df, use_container_width=True, hide_index=True)
    else:
        st.info("Sin historial de alertas todavía.")

with tab_intents:
    if not intents_df.empty:
        status_filter = st.multiselect(
            "Filtrar estados de intent",
            options=sorted(intents_df["status"].unique()),
            default=sorted(intents_df["status"].unique()),
        )
        filtered_intents = intents_df[intents_df["status"].isin(status_filter)]
        st.dataframe(filtered_intents, use_container_width=True, hide_index=True)

        selected_intent_id = st.selectbox(
            "Detalle de trade intent",
            options=filtered_intents["id"].tolist(),
            format_func=lambda intent_id: f"Intent #{intent_id}",
        )
        selected_intent = next(intent for intent in intents if intent.id == selected_intent_id)
        st.json(selected_intent.rationale_json or {})

        next_status = st.selectbox(
            "Cambiar estado intent",
            ["new", "reviewed", "approved", "rejected", "expired", "executed_manually"],
            index=[
                "new",
                "reviewed",
                "approved",
                "rejected",
                "expired",
                "executed_manually",
            ].index(selected_intent.status),
        )
        if st.button("Actualizar estado intent"):
            with session_scope() as session:
                TradeIntentsRepository(session).update_status(selected_intent_id, next_status)
            st.success("Estado del trade intent actualizado.")
            st.rerun()
    else:
        st.info("Todavia no hay trade intents.")

with tab_config:
    st.write("Resumen de configuracion de notificaciones y alertas")
    st.json(
        {
            "telegram_enabled": settings.telegram_enabled,
            "telegram_credentials_present": bool(
                settings.telegram_bot_token and settings.telegram_chat_id
            ),
            "notification_channels": notifications_cfg["channels"],
            "demo_mode": settings.demo_mode,
        }
    )
