from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import streamlit as st

from data.database import session_scope  # noqa: E402
from services.watchlist_service import WatchlistService  # noqa: E402

_COL = {
    "symbol": "Símbolo",
    "name": "Nombre",
    "asset_type": "Tipo",
    "sector": "Sector",
    "data_mode": "Modo",
    "freshness_status": "Frescura",
    "last_available_bar_date": "Última barra",
    "last_price": "Precio",
    "rsi14": "RSI 14",
    "distance_to_support_pct": "Dist. soporte %",
    "technical_score": "T. Score",
    "risk_score": "R. Score",
    "risk_level": "Riesgo",
    "portfolio_fit_score": "PF Score",
    "final_opportunity_score": "Score final",
    "recommendation": "Recomendación",
    "suggested_buy_low": "Compra mín.",
    "suggested_buy_high": "Compra máx.",
    "suggested_weight_add": "Peso sugerido",
}

_RECOMMENDATION_LABELS = {
    "BUY_CANDIDATE": "Comprar",
    "WATCH": "Vigilar",
    "AVOID": "Evitar",
}

st.title("Watchlist")

filter_col1, filter_col2, filter_col3 = st.columns(3)
asset_type_filter = filter_col1.selectbox("Tipo de activo", ["Todos", "stock", "etf", "crypto"])
risk_filter = filter_col2.selectbox("Nivel de riesgo", ["Todos", "low", "medium", "high"])
recommendation_filter = filter_col3.multiselect(
    "Recomendación",
    ["BUY_CANDIDATE", "WATCH", "AVOID"],
    default=[],
    format_func=lambda r: _RECOMMENDATION_LABELS.get(r, r),
    placeholder="Todas",
)

with session_scope() as session:
    frame = pd.DataFrame(WatchlistService(session).get_watchlist_rows())

if asset_type_filter != "Todos":
    frame = frame[frame["asset_type"] == asset_type_filter]
if risk_filter != "Todos":
    frame = frame[frame["risk_level"] == risk_filter]
if recommendation_filter:
    frame = frame[frame["recommendation"].isin(recommendation_filter)]

if frame.empty:
    st.info("No hay datos todavía para la watchlist. Actualiza precios y genera señales.")
else:
    display_frame = frame[[
        "symbol", "name", "asset_type", "sector",
        "freshness_status", "last_available_bar_date",
        "last_price", "rsi14", "distance_to_support_pct",
        "technical_score", "risk_score", "risk_level",
        "portfolio_fit_score", "final_opportunity_score",
        "recommendation", "suggested_buy_low", "suggested_buy_high", "suggested_weight_add",
    ]].sort_values(
        ["final_opportunity_score", "technical_score"],
        ascending=False,
        na_position="last",
    ).copy()

    # Etiquetas legibles para recomendación
    display_frame["recommendation"] = (
        display_frame["recommendation"].map(_RECOMMENDATION_LABELS).fillna(display_frame["recommendation"])
    )

    def _score_color(val: float) -> str:
        if val >= 70:
            return "color: #16a34a; font-weight: 600"
        if val >= 45:
            return "color: #d97706; font-weight: 600"
        return "color: #dc2626; font-weight: 600"

    def _style_table(row: pd.Series) -> list[str]:
        rec = row["Recomendación"]
        if rec == "Comprar":
            bg = "background-color: rgba(22, 163, 74, 0.10)"
        elif rec == "Vigilar":
            bg = "background-color: rgba(217, 119, 6, 0.10)"
        elif rec == "Evitar":
            bg = "background-color: rgba(220, 38, 38, 0.10)"
        else:
            bg = ""
        return [bg] * len(row)

    renamed = display_frame.rename(columns=_COL)
    score_cols = ["T. Score", "Score final", "R. Score", "PF Score"]
    styler = renamed.style.apply(_style_table, axis=1)
    for col in score_cols:
        if col in renamed.columns:
            styler = styler.map(
                lambda v: _score_color(float(v)) if v is not None and str(v) not in ("", "nan") else "",
                subset=[col],
            )
    st.dataframe(
        styler,
        use_container_width=True,
        hide_index=True,
        column_config={
            "T. Score": st.column_config.NumberColumn("T. Score", format="%.1f"),
            "Score final": st.column_config.NumberColumn("Score final", format="%.1f"),
            "R. Score": st.column_config.NumberColumn("R. Score", format="%.1f"),
            "PF Score": st.column_config.NumberColumn("PF Score", format="%.1f"),
            "RSI 14": st.column_config.NumberColumn("RSI 14", format="%.1f"),
            "Dist. soporte %": st.column_config.NumberColumn("Dist. soporte %", format="%.1f%%"),
            "Peso sugerido": st.column_config.NumberColumn("Peso sugerido", format="%.1%"),
        },
    )
    st.caption(f"{len(display_frame)} activos mostrados")
