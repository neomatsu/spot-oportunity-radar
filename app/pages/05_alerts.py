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
from data.repositories.planned_entries_repo import PlannedEntriesRepository  # noqa: E402
from data.repositories.trade_intents_repo import TradeIntentsRepository  # noqa: E402
from services.alert_service import AlertService  # noqa: E402
from services.planned_entry_service import PlannedEntryService  # noqa: E402

st.title("Alerts")
st.caption(
    "Centro operativo de alertas, estado de datos y trade intents. "
    "El sistema prepara decisiones, pero no ejecuta broker automáticamente."
)

init_db()
settings = get_settings()
notifications_cfg = load_yaml_config("notifications.yaml")

action_col1, action_col2 = st.columns(2)
scan_clicked = action_col1.button(
    "Escanear eventos y generar alertas", use_container_width=True, type="primary"
)
send_clicked = action_col2.button("Enviar alertas pendientes", use_container_width=True)

if scan_clicked:
    with session_scope() as session:
        summary = AlertService(session).scan_market_events()
    st.success(
        f"Scan completado — {summary.events_detected} eventos, "
        f"{summary.alerts_created} alertas nuevas, "
        f"{summary.alerts_deduplicated} deduplicadas, "
        f"{summary.trade_intents_created} trade intents."
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
    planned_levels = PlannedEntriesRepository(session).list_all()

pending_alerts = sum(1 for alert in alerts if alert.status == "new")
open_intents = sum(1 for intent in intents if intent.status in {"new", "reviewed", "approved"})
telegram_state = "on" if settings.telegram_enabled and settings.telegram_bot_token else "off"

summary_cols = st.columns(5)
summary_cols[0].metric("Alertas recientes", len(alerts))
summary_cols[1].metric("Pendientes", pending_alerts)
summary_cols[2].metric("Trade intents abiertos", open_intents)
summary_cols[3].metric("Telegram", telegram_state)
summary_cols[4].metric("Modo demo", "on" if settings.demo_mode else "off")

alerts_df = pd.DataFrame(
    [
        {
            "id": alert.id,
            "Símbolo": alert.symbol,
            "Grupo": (alert.payload_json or {}).get("alert_group", "other"),
            "Tipo": alert.alert_type,
            "Severidad": alert.severity,
            "Título": alert.title,
            "Estado": alert.status,
            "Creada": alert.created_at,
            "Enviada": alert.sent_at,
            "Canales": ",".join(alert.delivery_channels or []),
        }
        for alert in alerts
    ]
)
intents_df = pd.DataFrame(
    [
        {
            "id": intent.id,
            "Símbolo": intent.symbol,
            "Estado": intent.status,
            "Recomendación": intent.recommendation,
            "Score final": intent.final_score,
            "Risk score": intent.risk_score,
            "Peso sugerido": intent.suggested_weight_add,
            "Capital sugerido": intent.suggested_capital,
            "Creado": intent.created_at,
            "Revisado": intent.reviewed_at,
        }
        for intent in intents
    ]
)

tab_active, tab_history, tab_intents, tab_plans, tab_config = st.tabs(
    ["Alertas activas", "Historial", "Trade intents", "Planes de compra", "Configuración"]
)

# ---- Tab: Alertas activas ----
with tab_active:
    if alerts_df.empty:
        st.info("Todavía no hay alertas registradas.")
    else:
        severity_options = sorted(alerts_df["Severidad"].unique().tolist())
        group_options = sorted(alerts_df["Grupo"].unique().tolist())
        type_options = sorted(alerts_df["Tipo"].unique().tolist())
        status_options = sorted(alerts_df["Estado"].unique().tolist())
        default_statuses = [s for s in ["new", "sent"] if s in status_options]

        filter_col1, filter_col2, filter_col3, filter_col4 = st.columns(4)
        severity_filter = filter_col1.multiselect("Severidad", severity_options, default=severity_options)
        group_filter = filter_col2.multiselect("Grupo", group_options, default=group_options)
        type_filter = filter_col3.multiselect("Tipo", type_options, default=type_options)
        active_status_filter = filter_col4.multiselect("Estado", status_options, default=default_statuses)

        active_df = alerts_df.copy()
        if severity_filter:
            active_df = active_df[active_df["Severidad"].isin(severity_filter)]
        if group_filter:
            active_df = active_df[active_df["Grupo"].isin(group_filter)]
        if type_filter:
            active_df = active_df[active_df["Tipo"].isin(type_filter)]
        if active_status_filter:
            active_df = active_df[active_df["Estado"].isin(active_status_filter)]

        st.dataframe(active_df.drop(columns=["id"]), use_container_width=True, hide_index=True)

        if not active_df.empty:
            st.divider()
            selected_alert_id = st.selectbox(
                "Seleccionar alerta para ver detalle o cambiar estado",
                options=active_df["id"].tolist(),
                format_func=lambda aid: f"#{aid} — {next((a.title for a in alerts if a.id == aid), '')}",
            )
            selected_alert = next(alert for alert in alerts if alert.id == selected_alert_id)

            detail_col, action_col = st.columns([2, 1])
            with action_col:
                new_status = st.selectbox(
                    "Cambiar estado",
                    ["new", "sent", "acknowledged", "resolved", "ignored"],
                    index=["new", "sent", "acknowledged", "resolved", "ignored"].index(
                        selected_alert.status
                    ),
                    key="alert_status_select",
                )
                if st.button("Actualizar estado", use_container_width=True, key="alert_status_btn"):
                    with session_scope() as session:
                        AlertsRepository(session).update_status(selected_alert_id, new_status)
                    st.success("Estado actualizado.")
                    st.rerun()
            with detail_col:
                payload = selected_alert.payload_json or {}
                key_fields = ["alert_group", "symbol", "score", "rsi", "distance_to_support_pct", "recommendation"]
                inline_items = {k: payload[k] for k in key_fields if k in payload}
                if inline_items:
                    kv_pairs = " · ".join(f"**{k}**: {v}" for k, v in inline_items.items())
                    st.markdown(kv_pairs)
                with st.expander("Payload completo", expanded=False):
                    st.json(payload)

# ---- Tab: Historial ----
with tab_history:
    if alerts_df.empty:
        st.info("Sin historial de alertas todavía.")
    else:
        hist_status_filter = st.multiselect(
            "Filtrar por estado",
            options=sorted(alerts_df["Estado"].unique()),
            default=sorted(alerts_df["Estado"].unique()),
            key="hist_status_filter",
        )
        hist_df = alerts_df[alerts_df["Estado"].isin(hist_status_filter)] if hist_status_filter else alerts_df
        st.dataframe(hist_df.drop(columns=["id"]), use_container_width=True, hide_index=True)

        logs_df = pd.DataFrame(
            [
                {
                    "Alert ID": log.alert_id,
                    "Canal": log.channel,
                    "Estado": log.status,
                    "Intentado": log.attempted_at,
                    "Error": log.error_message,
                }
                for log in notification_logs
            ]
        )
        if not logs_df.empty:
            st.subheader("Log de notificaciones")
            st.dataframe(logs_df, use_container_width=True, hide_index=True)

# ---- Tab: Trade intents ----
with tab_intents:
    if intents_df.empty:
        st.info("Todavía no hay trade intents.")
    else:
        intent_status_filter = st.multiselect(
            "Filtrar estados",
            options=sorted(intents_df["Estado"].unique()),
            default=sorted(intents_df["Estado"].unique()),
            key="intents_status_filter",
        )
        filtered_intents = intents_df[intents_df["Estado"].isin(intent_status_filter)]
        st.dataframe(filtered_intents.drop(columns=["id"]), use_container_width=True, hide_index=True)

        if not filtered_intents.empty:
            st.divider()
            selected_intent_id = st.selectbox(
                "Seleccionar trade intent",
                options=filtered_intents["id"].tolist(),
                format_func=lambda iid: f"#{iid} — {next((i.symbol for i in intents if i.id == iid), '')}",
            )
            selected_intent = next(intent for intent in intents if intent.id == selected_intent_id)

            intent_detail_col, intent_action_col = st.columns([2, 1])
            with intent_action_col:
                next_status = st.selectbox(
                    "Cambiar estado",
                    ["new", "reviewed", "approved", "rejected", "expired", "executed_manually"],
                    index=[
                        "new", "reviewed", "approved", "rejected", "expired", "executed_manually",
                    ].index(selected_intent.status),
                    key="intent_status_select",
                )
                if st.button("Actualizar estado", use_container_width=True, key="intent_status_btn"):
                    with session_scope() as session:
                        TradeIntentsRepository(session).update_status(selected_intent_id, next_status)
                    st.success("Estado del trade intent actualizado.")
                    st.rerun()
            with intent_detail_col:
                rationale = selected_intent.rationale_json or {}
                key_fields = ["final_score", "technical_score", "risk_score", "portfolio_fit_score", "recommendation", "suggested_weight_add"]
                inline_items = {k: rationale[k] for k in key_fields if k in rationale}
                if inline_items:
                    kv_pairs = " · ".join(f"**{k}**: {v}" for k, v in inline_items.items())
                    st.markdown(kv_pairs)
                with st.expander("Rationale completo", expanded=False):
                    st.json(rationale)

# ---- Tab: Planes de compra ----
with tab_plans:
    st.subheader("Puntos de compra parcial")
    st.caption(
        "Los niveles se crean desde Asset Detail. Active y triggered se vigilan en cada "
        "escaneo diario; triggered se rearma automáticamente al alejarse."
    )
    if not planned_levels:
        st.info("Todavía no hay niveles de compra planificados.")
    else:
        plan_rows = [
            {
                "ID": level.id,
                "Símbolo": level.asset.symbol,
                "Estado": level.status,
                "Precio objetivo": level.target_price,
                "Divisa": level.price_currency or level.asset.quote_currency or "N/A",
                "Último precio observado": level.last_observed_price,
                "Distancia %": (
                    round(
                        PlannedEntryService.distance_pct(
                            level.last_observed_price, level.target_price
                        ),
                        2,
                    )
                    if level.last_observed_price is not None
                    else None
                ),
                "% sugerido": level.suggested_weight_pct,
                "Capital sugerido": level.suggested_capital,
                "Tolerancia %": level.tolerance_pct,
                "Expira": level.expires_at,
                "Último aviso": level.last_alerted_at,
                "Notas": level.notes,
            }
            for level in planned_levels
        ]
        plan_df = pd.DataFrame(plan_rows)
        filter_col1, filter_col2 = st.columns(2)
        plan_symbols = sorted(plan_df["Símbolo"].unique().tolist())
        plan_statuses = sorted(plan_df["Estado"].unique().tolist())
        symbol_filter = filter_col1.multiselect(
            "Símbolo", plan_symbols, default=plan_symbols, key="plan_symbol_filter"
        )
        status_filter = filter_col2.multiselect(
            "Estado", plan_statuses, default=plan_statuses, key="plan_status_filter"
        )
        filtered_plans = plan_df[
            plan_df["Símbolo"].isin(symbol_filter) & plan_df["Estado"].isin(status_filter)
        ]
        st.dataframe(filtered_plans.drop(columns=["ID"]), use_container_width=True, hide_index=True)
        if not filtered_plans.empty:
            edit_col1, edit_col2, edit_col3 = st.columns([2, 2, 1])
            plan_id = edit_col1.selectbox(
                "Seleccionar plan",
                filtered_plans["ID"].tolist(),
                format_func=lambda value: f"#{value}",
                key="alert_plan_id",
            )
            plan_status = edit_col2.selectbox(
                "Cambiar estado",
                ["active", "paused", "executed_manually", "expired"],
                key="alert_plan_status",
            )
            if edit_col3.button("Aplicar", key="alert_plan_apply", use_container_width=True):
                with session_scope() as session:
                    PlannedEntryService(session).set_status(plan_id, plan_status)
                st.rerun()

# ---- Tab: Configuración ----
with tab_config:
    st.subheader("Configuración de notificaciones")
    cfg_col1, cfg_col2, cfg_col3 = st.columns(3)
    cfg_col1.metric("Telegram", "Activado" if settings.telegram_enabled else "Desactivado")
    cfg_col2.metric(
        "Credenciales Telegram",
        "Configuradas" if (settings.telegram_bot_token and settings.telegram_chat_id) else "Faltan",
    )
    cfg_col3.metric("Modo demo", "Sí" if settings.demo_mode else "No")

    with st.expander("Canales de notificación (detalle)", expanded=False):
        st.json(notifications_cfg.get("channels", {}))
