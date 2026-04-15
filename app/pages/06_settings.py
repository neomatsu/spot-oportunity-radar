from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import streamlit as st

from core.config import get_provider_settings, get_settings, load_yaml_config  # noqa: E402
from data.database import session_scope  # noqa: E402
from services.watchlist_service import WatchlistService  # noqa: E402

st.title("Configuración")

settings = get_settings()
providers = get_provider_settings()
real_mode_available = bool(providers.alphavantage_api_key or providers.fmp_api_key)

effective_mode = (
    "hybrid"
    if settings.demo_mode and real_mode_available
    else ("demo" if settings.demo_mode else "real_only")
)

# --- App ---
st.subheader("Aplicación")
app_col1, app_col2, app_col3, app_col4 = st.columns(4)
app_col1.metric("Entorno", settings.env)
app_col2.metric("Modo efectivo", effective_mode)
app_col3.metric("Demo mode", "Sí" if settings.demo_mode else "No")
app_col4.metric("Log level", settings.log_level)
with st.expander("Rutas del sistema", expanded=False):
    st.markdown(f"- **Base de datos:** `{settings.db_url}`")
    st.markdown(f"- **Config dir:** `{settings.config_dir}`")

if settings.demo_mode:
    st.info(
        "Modo demo activado. Si un proveedor real no está disponible o falla, "
        "la app puede usar series sintéticas reproducibles para seguir siendo usable."
    )

# --- Providers ---
st.subheader("Proveedores de datos")
prov_col1, prov_col2, prov_col3, prov_col4 = st.columns(4)
prov_col1.metric("Alpha Vantage", "Configurado" if providers.alphavantage_api_key else "No configurado")
prov_col2.metric("FMP", "Configurado" if providers.fmp_api_key else "No configurado")
prov_col3.metric("Binance (público)", "Activo")
prov_col4.metric("Modo real disponible", "Sí" if real_mode_available else "No")

# --- Data status ---
st.subheader("Estado de datos")
with session_scope() as session:
    watchlist_rows = WatchlistService(session).get_watchlist_rows()

mode_summary: dict[str, int] = {}
freshness_summary: dict[str, int] = {}
for row in watchlist_rows:
    mode_summary[row["data_mode"]] = mode_summary.get(row["data_mode"], 0) + 1
    freshness_summary[row["freshness_status"]] = freshness_summary.get(
        row["freshness_status"], 0
    ) + 1

data_col1, data_col2 = st.columns(2)
with data_col1:
    st.metric("Activos tracked", len(watchlist_rows))
    for mode, count in mode_summary.items():
        st.metric(f"Modo: {mode}", count)
with data_col2:
    for status, count in freshness_summary.items():
        st.metric(f"Frescura: {status}", count)

# --- Scoring config ---
with st.expander("Scoring config", expanded=False):
    scoring_cfg = load_yaml_config("scoring.yaml")
    for section, values in scoring_cfg.items():
        st.markdown(f"**{section}**")
        if isinstance(values, dict):
            pairs = " · ".join(f"`{k}`: {v}" for k, v in values.items())
            st.markdown(pairs)
        else:
            st.markdown(f"`{values}`")

# --- Risk rules ---
with st.expander("Risk rules", expanded=False):
    st.json(load_yaml_config("risk_rules.yaml"))

# --- Portfolio rules ---
with st.expander("Portfolio rules", expanded=False):
    st.json(load_yaml_config("portfolio_rules.yaml"))

# --- Data sources ---
with st.expander("Data sources", expanded=False):
    st.json(load_yaml_config("data_sources.yaml"))
