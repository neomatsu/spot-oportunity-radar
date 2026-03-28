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

st.title("Settings")

settings = get_settings()
providers = get_provider_settings()
real_mode_available = bool(providers.alphavantage_api_key or providers.fmp_api_key)

effective_mode = (
    "hybrid"
    if settings.demo_mode and real_mode_available
    else ("demo" if settings.demo_mode else "real_only")
)

st.subheader("App")
st.json(
    {
        "env": settings.env,
        "db_url": settings.db_url,
        "config_dir": settings.config_dir,
        "log_level": settings.log_level,
        "demo_mode": settings.demo_mode,
        "effective_mode": effective_mode,
    }
)

st.subheader("Providers")
st.json(
    {
        "alphavantage_configured": bool(providers.alphavantage_api_key),
        "fmp_configured": bool(providers.fmp_api_key),
        "binance_public_api": True,
        "stock_etf_real_mode_available": real_mode_available,
    }
)

with session_scope() as session:
    watchlist_rows = WatchlistService(session).get_watchlist_rows()

mode_summary: dict[str, int] = {}
freshness_summary: dict[str, int] = {}
for row in watchlist_rows:
    mode_summary[row["data_mode"]] = mode_summary.get(row["data_mode"], 0) + 1
    freshness_summary[row["freshness_status"]] = freshness_summary.get(
        row["freshness_status"], 0
    ) + 1

st.subheader("Data status")
st.json(
    {
        "assets_tracked": len(watchlist_rows),
        "data_mode_counts": mode_summary,
        "freshness_counts": freshness_summary,
    }
)

if settings.demo_mode:
    st.info(
        "Modo demo activado. Si un proveedor real no esta disponible o falla, "
        "la app puede usar series sinteticas reproducibles para seguir siendo usable."
    )

st.subheader("Scoring config")
st.json(load_yaml_config("scoring.yaml"))

st.subheader("Risk rules")
st.json(load_yaml_config("risk_rules.yaml"))

st.subheader("Portfolio rules")
st.json(load_yaml_config("portfolio_rules.yaml"))

st.subheader("Data sources rules")
st.json(load_yaml_config("data_sources.yaml"))
