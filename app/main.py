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

col1, col2, col3 = st.columns(3)
col1.metric("Entorno", settings.env)
col2.metric("DB", settings.db_url.replace("sqlite:///", ""))
provider_count = int(bool(providers.alphavantage_api_key)) + int(bool(providers.fmp_api_key)) + 1
col3.metric("Proveedores activos", provider_count)

st.info(
    "Usa el menu lateral para navegar por dashboard, watchlist, detalle de activo, "
    "cartera y settings. Si faltan API keys para equities o ETFs, la app seguira "
    "funcionando y lo indicara con mensajes claros."
)

st.subheader("Operaciones")
controls_left, controls_right = st.columns([2, 1])
with controls_left:
    st.write(
        "La Fase 1 ya permite cargar activos desde YAML, persistir precios diarios, "
        "calcular indicadores tecnicos y generar recomendaciones explicables."
    )
with controls_right:
    force_refresh = st.checkbox("Forzar refresh de precios", value=False)

buttons_left, buttons_right = st.columns(2)
sync_clicked = buttons_left.button("Actualizar precios y senales", use_container_width=True)
reload_assets_clicked = buttons_right.button(
    "Recargar activos desde YAML",
    use_container_width=True,
)

if reload_assets_clicked:
    seed_assets()
    st.success("Activos sincronizados desde config/assets.yaml.")

if sync_clicked:
    with st.spinner(
        "Actualizando historico, calculando indicadores y generando recomendaciones..."
    ):
        with session_scope() as session:
            summary = RecommendationFacade(session).refresh_and_generate_all(force=force_refresh)

    st.success(
        f"Proceso completado. Activos: {summary.total_assets}, "
        f"cache reutilizada: {summary.cached_assets}, "
        f"precios refrescados: {summary.refreshed_assets}, "
        f"cache preservada tras fallo: {summary.preserved_assets}, "
        f"senales generadas: {summary.generated_signals}."
    )
    if summary.demo_assets:
        st.info("Activos servidos en modo demo: " + ", ".join(summary.demo_assets))
    if summary.provider_unavailable_assets:
        st.warning(
            "Sin proveedor configurado para: "
            + ", ".join(summary.provider_unavailable_assets)
        )
    if summary.provider_error_assets:
        st.error("Error de proveedor para: " + ", ".join(summary.provider_error_assets))

with session_scope() as session:
    watchlist_rows = WatchlistService(session).get_watchlist_rows()

ready_rows = [row for row in watchlist_rows if row["final_opportunity_score"] is not None]
metrics_col1, metrics_col2, metrics_col3 = st.columns(3)
metrics_col1.metric("Activos cargados", len(watchlist_rows))
metrics_col2.metric("Activos con senal", len(ready_rows))
metrics_col3.metric(
    "Mejor final score",
    (
        f"{max(row['final_opportunity_score'] for row in ready_rows):.1f}"
        if ready_rows
        else "N/A"
    ),
)
