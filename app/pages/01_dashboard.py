from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import plotly.express as px
import streamlit as st

from data.database import session_scope  # noqa: E402
from services.watchlist_service import WatchlistService  # noqa: E402

_RECOMMENDATION_LABELS = {
    "BUY_CANDIDATE": "Comprar",
    "WATCH": "Vigilar",
    "AVOID": "Evitar",
}

_COL = {
    "symbol": "Símbolo",
    "name": "Nombre",
    "asset_type": "Tipo",
    "data_mode": "Modo",
    "freshness_status": "Frescura",
    "risk_level": "Riesgo",
    "technical_score": "T. Score",
    "risk_score": "R. Score",
    "portfolio_fit_score": "PF Score",
    "final_opportunity_score": "Score final",
    "recommendation": "Recomendación",
    "suggested_weight_add": "Peso sugerido",
    "last_price": "Precio",
    "suggested_buy_low": "Compra mín.",
    "suggested_buy_high": "Compra máx.",
}

# Colores semánticos para scores (verde ≥70, naranja 45-70, rojo <45)
def _score_color(val: float) -> str:
    if val >= 70:
        return "color: #16a34a; font-weight: 600"
    if val >= 45:
        return "color: #d97706; font-weight: 600"
    return "color: #dc2626; font-weight: 600"


def _style_scores(df: pd.DataFrame, score_cols: list[str]) -> pd.io.formats.style.Styler:
    """Aplica color semántico a columnas de score y colorea filas por recomendación."""
    def row_bg(row: pd.Series) -> list[str]:
        rec = row.get("Recomendación", "")
        if rec == "Comprar":
            bg = "background-color: rgba(22, 163, 74, 0.10)"
        elif rec == "Vigilar":
            bg = "background-color: rgba(217, 119, 6, 0.10)"
        elif rec == "Evitar":
            bg = "background-color: rgba(220, 38, 38, 0.10)"
        else:
            bg = ""
        return [bg] * len(row)

    styler = df.style.apply(row_bg, axis=1)
    for col in score_cols:
        if col in df.columns:
            styler = styler.map(
                lambda v: _score_color(float(v)) if v is not None and str(v) not in ("", "nan") else "",
                subset=[col],
            )
    return styler


st.title("Dashboard")

with session_scope() as session:
    watchlist_service = WatchlistService(session)
    signals_df = pd.DataFrame(watchlist_service.get_watchlist_rows())
    positions_df = pd.DataFrame(watchlist_service.get_positions_rows())

available_signals = (
    signals_df.dropna(subset=["final_opportunity_score"]).copy()
    if not signals_df.empty
    else pd.DataFrame()
)
buy_df = (
    available_signals[available_signals["recommendation"] == "BUY_CANDIDATE"]
    if not available_signals.empty
    else pd.DataFrame()
)
watch_df = (
    available_signals[available_signals["recommendation"] == "WATCH"]
    if not available_signals.empty
    else pd.DataFrame()
)

# --- Métricas de resumen ---
col1, col2, col3, col4 = st.columns(4)
col1.metric("Activos vigilados", len(signals_df))
col2.metric("Con señal", len(available_signals))
col3.metric("Candidatos compra", len(buy_df))
col4.metric(
    "Peso invertido",
    f"{positions_df['current_weight'].sum() * 100:.1f}%" if not positions_df.empty else "0.0%",
)

if available_signals.empty:
    st.warning(
        "Todavía no hay señales. Ejecuta la actualización desde la portada o usa "
        "`python -m jobs.refresh_prices` y `python -m jobs.generate_signals`."
    )
else:
    top_records = WatchlistService.top_opportunities(
        available_signals.to_dict(orient="records"), limit=10
    )
    top_df = pd.DataFrame(top_records)
    if "recommendation" in top_df.columns:
        top_df["recommendation"] = (
            top_df["recommendation"].map(_RECOMMENDATION_LABELS).fillna(top_df["recommendation"])
        )

    ranking_col, risk_col = st.columns([1.5, 1])

    with ranking_col:
        st.subheader("Top oportunidades")
        ranking_display = (
            top_df[[
                "symbol", "name", "asset_type",
                "technical_score", "risk_score", "portfolio_fit_score",
                "final_opportunity_score", "recommendation", "suggested_weight_add",
            ]].rename(columns=_COL)
        )
        st.dataframe(
            _style_scores(ranking_display, ["T. Score", "Score final", "R. Score", "PF Score"]),
            use_container_width=True,
            hide_index=True,
            column_config={
                "T. Score": st.column_config.NumberColumn("T. Score", format="%.1f"),
                "R. Score": st.column_config.NumberColumn("R. Score", format="%.1f"),
                "PF Score": st.column_config.NumberColumn("PF Score", format="%.1f"),
                "Score final": st.column_config.NumberColumn("Score final", format="%.1f"),
                "Peso sugerido": st.column_config.NumberColumn("Peso sugerido", format="%.1%"),
            },
        )
        st.plotly_chart(
            px.bar(
                top_df,
                x="symbol",
                y="final_opportunity_score",
                color="recommendation",
                color_discrete_map={
                    "Comprar": "#16a34a",
                    "Vigilar": "#d97706",
                    "Evitar": "#dc2626",
                },
                labels={
                    "final_opportunity_score": "Score final",
                    "symbol": "Símbolo",
                    "recommendation": "",
                },
                title="Ranking por score final",
            ),
            use_container_width=True,
        )

    with risk_col:
        st.subheader("Comparativa de scores")
        heatmap_df = top_df[["symbol", "technical_score", "portfolio_fit_score"]].copy()
        heatmap_df["seguridad"] = 100 - top_df["risk_score"]
        heatmap_df = heatmap_df.rename(columns={
            "technical_score": "Técnico",
            "portfolio_fit_score": "Portfolio fit",
            "seguridad": "Seguridad",
        }).set_index("symbol")
        st.plotly_chart(
            px.imshow(
                heatmap_df.T,
                aspect="auto",
                color_continuous_scale="RdYlGn",
                zmin=0,
                zmax=100,
                title="Verde = favorable (Seguridad = 100 − R.Score)",
            ),
            use_container_width=True,
        )

    # --- Comprar / Vigilar ---
    split_col1, split_col2 = st.columns(2)
    with split_col1:
        st.subheader("Comprar ahora")
        if buy_df.empty:
            st.info("No hay candidatos de compra en este momento.")
        else:
            buy_display = (
                buy_df[[
                    "symbol", "name", "last_price", "risk_level",
                    "final_opportunity_score", "suggested_buy_low",
                    "suggested_buy_high", "suggested_weight_add",
                ]]
                .sort_values("final_opportunity_score", ascending=False)
                .rename(columns=_COL)
            )
            st.dataframe(
                _style_scores(buy_display, ["Score final"]),
                use_container_width=True,
                hide_index=True,
            )

    with split_col2:
        st.subheader("En vigilancia")
        if watch_df.empty:
            st.info("No hay activos en vigilancia en este momento.")
        else:
            watch_display = (
                watch_df[[
                    "symbol", "name", "last_price", "risk_level",
                    "final_opportunity_score", "suggested_buy_low", "suggested_buy_high",
                ]]
                .sort_values("final_opportunity_score", ascending=False)
                .rename(columns=_COL)
            )
            st.dataframe(
                _style_scores(watch_display, ["Score final"]),
                use_container_width=True,
                hide_index=True,
            )

# --- Portfolio overview ---
st.subheader("Portfolio overview")
if positions_df.empty:
    st.info("No hay posiciones registradas todavía.")
else:
    exposure_by_sector = positions_df.groupby("sector", as_index=False)["current_weight"].sum()
    st.plotly_chart(
        px.pie(
            exposure_by_sector,
            names="sector",
            values="current_weight",
            title="Exposición por sector",
            color_discrete_sequence=px.colors.qualitative.Set2,
        ),
        use_container_width=True,
    )

# --- Todas las señales ---
st.subheader("Todas las señales")
if available_signals.empty:
    st.info("Todavía no hay señales.")
else:
    if "recommendation" in available_signals.columns:
        available_signals["recommendation"] = (
            available_signals["recommendation"]
            .map(_RECOMMENDATION_LABELS)
            .fillna(available_signals["recommendation"])
        )
    recent_display = (
        available_signals[[
            "symbol", "name", "asset_type", "freshness_status", "risk_level",
            "technical_score", "risk_score", "portfolio_fit_score",
            "final_opportunity_score", "recommendation", "suggested_weight_add",
        ]]
        .sort_values("final_opportunity_score", ascending=False, na_position="last")
        .rename(columns=_COL)
    )
    st.dataframe(
        _style_scores(
            recent_display,
            ["T. Score", "R. Score", "PF Score", "Score final"],
        ),
        use_container_width=True,
        hide_index=True,
        column_config={
            "T. Score": st.column_config.NumberColumn("T. Score", format="%.1f"),
            "R. Score": st.column_config.NumberColumn("R. Score", format="%.1f"),
            "PF Score": st.column_config.NumberColumn("PF Score", format="%.1f"),
            "Score final": st.column_config.NumberColumn("Score final", format="%.1f"),
            "Peso sugerido": st.column_config.NumberColumn("Peso sugerido", format="%.1%"),
        },
    )
