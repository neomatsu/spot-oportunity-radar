from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st

from core.config import get_provider_settings, get_settings  # noqa: E402
from data.database import init_db, seed_assets, session_scope  # noqa: E402
from services.recommendation_facade import RecommendationFacade  # noqa: E402
from services.watchlist_service import WatchlistService  # noqa: E402

st.set_page_config(page_title="Spot Opportunity Radar", layout="wide")

settings = get_settings()
providers = get_provider_settings()

init_db()
seed_assets()

st.title("Spot Opportunity Radar")
st.caption(
    "Decision support para oportunidades spot con foco en riesgo, "
    "soporte y equilibrio de cartera."
)

# --- Resumen de estado ---
with session_scope() as session:
    watchlist_rows = WatchlistService(session).get_watchlist_rows()

ready_rows = [row for row in watchlist_rows if row["final_opportunity_score"] is not None]
buy_rows = [row for row in ready_rows if row.get("recommendation") == "BUY_CANDIDATE"]

summary_col1, summary_col2, summary_col3, summary_col4 = st.columns(4)
summary_col1.metric("Activos cargados", len(watchlist_rows))
summary_col2.metric("Con señal", len(ready_rows))
summary_col3.metric("Candidatos compra", len(buy_rows))
summary_col4.metric(
    "Mejor score",
    (
        f"{max(row['final_opportunity_score'] for row in ready_rows):.1f}"
        if ready_rows
        else "N/A"
    ),
)

st.divider()

# --- Operaciones ---
st.subheader("Actualizar datos")
op_col1, op_col2 = st.columns([3, 1])
with op_col1:
    force_refresh = st.checkbox(
        "Forzar refresh de precios (ignora caché)",
        value=False,
        help="Por defecto la app reutiliza precios recientes para evitar llamadas innecesarias a la API.",
    )
btn_col1, btn_col2 = st.columns(2)
sync_clicked = btn_col1.button("Actualizar precios y señales", use_container_width=True, type="primary")
reload_assets_clicked = btn_col2.button("Recargar activos desde YAML", use_container_width=True)

if reload_assets_clicked:
    seed_assets()
    st.success("Activos sincronizados desde config/assets.yaml.")

if sync_clicked:
    with st.spinner("Actualizando histórico, calculando indicadores y generando señales..."):
        with session_scope() as session:
            summary = RecommendationFacade(session).refresh_and_generate_all(force=force_refresh)

    st.success(
        f"Proceso completado — {summary.total_assets} activos, "
        f"{summary.refreshed_assets} refrescados, "
        f"{summary.generated_signals} señales generadas."
    )
    if summary.demo_assets:
        st.info("Activos en modo demo: " + ", ".join(summary.demo_assets))
    if summary.provider_unavailable_assets:
        st.warning("Sin proveedor configurado para: " + ", ".join(summary.provider_unavailable_assets))
    if summary.provider_error_assets:
        st.error("Error de proveedor para: " + ", ".join(summary.provider_error_assets))

# --- Info de entorno (colapsado por defecto) ---
with st.expander("Estado del sistema", expanded=False):
    provider_count = (
        int(bool(providers.alphavantage_api_key)) + int(bool(providers.fmp_api_key)) + 1
    )
    env_col1, env_col2, env_col3 = st.columns(3)
    env_col1.metric("Entorno", settings.env)
    env_col2.metric("Base de datos", settings.db_url.replace("sqlite:///", ""))
    env_col3.metric("Proveedores activos", provider_count)
