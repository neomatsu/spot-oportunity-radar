from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import plotly.express as px
import streamlit as st

from app.components.dashboard import (  # noqa: E402
    dashboard_styles,
    detector_card_html,
    detector_sparkline,
    portfolio_metrics_html,
)
from data.database import session_scope  # noqa: E402
from services.dashboard_service import DashboardService, DashboardSnapshot  # noqa: E402

RECOMMENDATION_LABELS = {
    "BUY_CANDIDATE": "Comprar",
    "WATCH": "Vigilar",
}

SEVERITY_LABELS = {
    "critical": "Crítica",
    "high": "Alta",
    "warning": "Aviso",
    "info": "Info",
}


@st.cache_data(ttl=300, show_spinner=False)
def _load_dashboard() -> DashboardSnapshot:
    with session_scope() as session:
        return DashboardService(session).build()


def _money(value: float) -> str:
    return f"{value:,.2f} €"


def _score_style(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    score = float(value)
    if score >= 70:
        return "color:#15803d;font-weight:700"
    if score >= 45:
        return "color:#d97706;font-weight:700"
    return "color:#dc2626;font-weight:700"


def _job_status(snapshot: DashboardSnapshot) -> tuple[str, str]:
    if snapshot.last_job is None:
        return "Sin ejecuciones registradas", "#94a3b8"
    run = snapshot.last_job
    timestamp = run.finished_at.strftime("%d/%m/%Y %H:%M") if run.finished_at else "en curso"
    if run.status == "success" and run.errors == 0:
        return f"Último job {timestamp} · correcto", "#16a34a"
    if run.status == "running":
        return f"Job iniciado {timestamp}", "#d97706"
    return f"Último job {timestamp} · {run.status}", "#dc2626"


st.set_page_config(page_title="Dashboard · Spot Opportunity Radar", layout="wide")
st.html(dashboard_styles())

with st.spinner("Leyendo el estado persistido del radar..."):
    snapshot = _load_dashboard()

title_col, status_col = st.columns([2, 1])
with title_col:
    st.markdown('<div class="dashboard-kicker">Decision cockpit</div>', unsafe_allow_html=True)
    st.title("Dashboard")
    st.markdown(
        '<div class="dashboard-subtitle">Contexto de mercado, cartera y próximas decisiones.</div>',
        unsafe_allow_html=True,
    )
with status_col:
    status_text, status_color = _job_status(snapshot)
    st.markdown(
        f'<div class="status-line"><span class="status-dot" '
        f'style="background:{status_color}"></span>{status_text}</div>',
        unsafe_allow_html=True,
    )
    freshness = (
        "Todos los activos con datos reales y frescos"
        if snapshot.stale_assets == 0
        else f"{snapshot.stale_assets} activos requieren revisar datos"
    )
    st.caption(freshness)

st.subheader("Pulso de mercado")
market_columns = st.columns(2, gap="large")
detectors = (
    (snapshot.bitcoin, "pages/10_bitcoin_opportunity.py", "Abrir Bitcoin Opportunity"),
    (snapshot.sp500, "pages/11_sp500_opportunity.py", "Abrir S&P 500 Opportunity"),
)
for column, (detector, page, link_label) in zip(market_columns, detectors, strict=True):
    with column:
        with st.container(border=True):
            st.html(detector_card_html(detector))
            st.plotly_chart(
                detector_sparkline(detector),
                width="stretch",
                config={"displayModeBar": False},
            )
            st.page_link(page, label=link_label, use_container_width=True)

st.subheader("Tu cartera")
portfolio = snapshot.portfolio
st.html(
    portfolio_metrics_html(
        [
            ("Capital total", _money(portfolio.total_capital), None),
            ("Valor actual", _money(portfolio.market_value), None),
            (
                "P&L",
                _money(portfolio.pnl),
                f"{portfolio.pnl_pct:+.2f}%" if portfolio.pnl_pct is not None else None,
            ),
            ("Cash estimado", _money(portfolio.estimated_cash), None),
            ("Exposición", f"{portfolio.exposure_pct:.1f}%", None),
            ("Posiciones", str(portfolio.positions_count), None),
        ]
    )
)

content_columns = st.columns([1.65, 1], gap="large")
with content_columns[0]:
    st.subheader("Oportunidades prioritarias")
    if not snapshot.opportunities:
        st.info("No hay candidatos de compra o vigilancia con score disponible.")
    else:
        opportunities = pd.DataFrame(snapshot.opportunities)
        opportunities["recommendation"] = opportunities["recommendation"].map(
            RECOMMENDATION_LABELS
        )
        display = opportunities[
            [
                "symbol",
                "name",
                "final_opportunity_score",
                "risk_score",
                "recommendation",
                "suggested_weight_add",
            ]
        ].rename(
            columns={
                "symbol": "Símbolo",
                "name": "Nombre",
                "final_opportunity_score": "Score",
                "risk_score": "Riesgo",
                "recommendation": "Decisión",
                "suggested_weight_add": "Peso sugerido",
            }
        )
        styled = display.style.map(_score_style, subset=["Score"])
        st.dataframe(
            styled,
            hide_index=True,
            width="stretch",
            height=390,
            column_config={
                "Score": st.column_config.NumberColumn(format="%.1f"),
                "Riesgo": st.column_config.NumberColumn(format="%.1f"),
                "Peso sugerido": st.column_config.NumberColumn(format="%.1f%%"),
            },
        )
    st.page_link(
        "pages/02_watchlist.py",
        label=(
            f"Ver watchlist completa · {snapshot.ready_assets}/"
            f"{snapshot.watched_assets} con señal"
        ),
        use_container_width=True,
    )

with content_columns[1]:
    st.subheader("Composición actual")
    allocation = list(portfolio.allocation)
    cash_weight = max(0.0, 1.0 - portfolio.exposure_pct / 100)
    if cash_weight > 0:
        allocation.append({"symbol": "Cash", "weight": cash_weight})
    if not allocation:
        st.info("No hay posiciones registradas.")
    else:
        allocation_frame = pd.DataFrame(allocation)
        figure = px.pie(
            allocation_frame,
            names="symbol",
            values="weight",
            hole=0.68,
            color_discrete_sequence=[
                "#2563eb",
                "#0f766e",
                "#d97706",
                "#be123c",
                "#64748b",
                "#8b5cf6",
            ],
        )
        figure.update_traces(textposition="inside", textinfo="label+percent")
        figure.update_layout(
            height=350,
            margin={"l": 5, "r": 5, "t": 5, "b": 5},
            showlegend=False,
        )
        st.plotly_chart(figure, width="stretch", config={"displayModeBar": False})
    st.caption(
        f"Mayor posición {portfolio.largest_position_pct:.1f}% · "
        f"Coste invertido {_money(portfolio.invested_cost)}"
    )
    st.page_link("pages/04_portfolio.py", label="Abrir portfolio", use_container_width=True)

st.subheader("Posiciones y niveles que requieren atención")
if not snapshot.attention_items:
    st.info("No hay alertas relevantes nuevas durante los últimos 7 días.")
else:
    attention = pd.DataFrame(snapshot.attention_items)
    attention["severity"] = attention["severity"].map(SEVERITY_LABELS).fillna(
        attention["severity"]
    )
    attention["created_at"] = pd.to_datetime(attention["created_at"]).dt.strftime(
        "%d/%m/%Y %H:%M"
    )
    attention = attention.rename(
        columns={
            "symbol": "Símbolo",
            "alert_type": "Tipo",
            "severity": "Severidad",
            "title": "Alerta",
            "action": "Acción sugerida",
            "created_at": "Fecha",
        }
    )
    st.dataframe(attention, hide_index=True, width="stretch")
st.page_link("pages/05_alerts.py", label="Abrir centro de alertas", use_container_width=True)

with st.expander("Estado operativo", expanded=False):
    status_columns = st.columns(4)
    status_columns[0].metric("Activos vigilados", snapshot.watched_assets)
    status_columns[1].metric("Con señal", snapshot.ready_assets)
    status_columns[2].metric("BUY_CANDIDATE", snapshot.buy_candidates)
    status_columns[3].metric("Datos a revisar", snapshot.stale_assets)
    st.caption(
        "El Dashboard es de solo lectura y utiliza SQLite. Las descargas y recálculos "
        "se realizan desde el job diario o desde las páginas especializadas."
    )
