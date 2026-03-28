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

col1, col2, col3, col4 = st.columns(4)
col1.metric("Activos vigilados", len(signals_df))
col2.metric("Senales disponibles", len(available_signals))
col3.metric("BUY_CANDIDATE", len(buy_df))
col4.metric(
    "Peso invertido",
    f"{positions_df['current_weight'].sum() * 100:.1f}%" if not positions_df.empty else "0.0%",
)

if available_signals.empty:
    st.warning(
        "Todavia no hay senales. Ejecuta la actualizacion desde la portada o usa "
        "`python -m jobs.refresh_prices` y `python -m jobs.generate_signals`."
    )
else:
    top_df = pd.DataFrame(
        WatchlistService.top_opportunities(available_signals.to_dict(orient="records"), limit=10)
    )
    ranking_col, risk_col = st.columns([1.5, 1])

    with ranking_col:
        st.subheader("Top opportunities")
        ranking_table = top_df[
            [
                "symbol",
                "asset_type",
                "data_mode",
                "freshness_status",
                "technical_score",
                "risk_score",
                "portfolio_fit_score",
                "final_opportunity_score",
                "recommendation",
                "suggested_weight_add",
            ]
        ]
        st.dataframe(ranking_table, use_container_width=True, hide_index=True)
        st.plotly_chart(
            px.bar(
                ranking_table,
                x="symbol",
                y="final_opportunity_score",
                color="recommendation",
                title="Ranking por final opportunity score",
            ),
            use_container_width=True,
        )

    with risk_col:
        st.subheader("Risk heatmap")
        risk_heatmap = top_df[
            ["symbol", "risk_score", "technical_score", "portfolio_fit_score"]
        ].set_index("symbol")
        st.plotly_chart(
            px.imshow(
                risk_heatmap.T,
                aspect="auto",
                color_continuous_scale="RdYlGn_r",
                title="Calor de riesgo y score",
            ),
            use_container_width=True,
        )

    split_col1, split_col2 = st.columns(2)
    with split_col1:
        st.subheader("BUY_CANDIDATE")
        if buy_df.empty:
            st.info("No hay BUY_CANDIDATE ahora mismo.")
        else:
            st.dataframe(
                buy_df[
                    [
                        "symbol",
                        "last_price",
                        "risk_level",
                        "final_opportunity_score",
                        "suggested_buy_low",
                        "suggested_buy_high",
                        "suggested_weight_add",
                    ]
                ].sort_values("final_opportunity_score", ascending=False),
                use_container_width=True,
                hide_index=True,
            )
    with split_col2:
        st.subheader("WATCH")
        if watch_df.empty:
            st.info("No hay WATCH ahora mismo.")
        else:
            st.dataframe(
                watch_df[
                    [
                        "symbol",
                        "last_price",
                        "risk_level",
                        "final_opportunity_score",
                        "suggested_buy_low",
                        "suggested_buy_high",
                    ]
                ].sort_values("final_opportunity_score", ascending=False),
                use_container_width=True,
                hide_index=True,
            )

st.subheader("Portfolio overview")
if positions_df.empty:
    st.info("No hay posiciones registradas todavia.")
else:
    exposure_by_sector = positions_df.groupby("sector", as_index=False)["current_weight"].sum()
    st.plotly_chart(
        px.pie(
            exposure_by_sector,
            names="sector",
            values="current_weight",
            title="Exposicion por sector",
        ),
        use_container_width=True,
    )

st.subheader("Recent signals")
if available_signals.empty:
    st.info("Todavia no hay senales recientes.")
else:
    recent_df = available_signals[
        [
            "symbol",
            "asset_type",
            "data_mode",
            "freshness_status",
            "last_refresh_source",
            "risk_level",
            "technical_score",
            "risk_score",
            "portfolio_fit_score",
            "final_opportunity_score",
            "recommendation",
            "suggested_weight_add",
        ]
    ]
    st.dataframe(
        recent_df.sort_values("final_opportunity_score", ascending=False, na_position="last"),
        use_container_width=True,
        hide_index=True,
    )
