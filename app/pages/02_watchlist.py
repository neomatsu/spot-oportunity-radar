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

st.title("Watchlist")

filter_col1, filter_col2, filter_col3 = st.columns(3)
asset_type_filter = filter_col1.selectbox("Asset type", ["all", "stock", "etf", "crypto"])
risk_filter = filter_col2.selectbox("Risk level", ["all", "low", "medium", "high"])
recommendation_filter = filter_col3.selectbox(
    "Recommendation", ["all", "BUY_CANDIDATE", "WATCH", "AVOID"]
)

with session_scope() as session:
    frame = pd.DataFrame(WatchlistService(session).get_watchlist_rows())

if asset_type_filter != "all":
    frame = frame[frame["asset_type"] == asset_type_filter]
if risk_filter != "all":
    frame = frame[frame["risk_level"] == risk_filter]
if recommendation_filter != "all":
    frame = frame[frame["recommendation"] == recommendation_filter]

if frame.empty:
    st.info("No hay datos todavia para la watchlist. Actualiza precios y genera senales.")
else:
    display_frame = frame[
        [
            "symbol",
            "asset_type",
            "sector",
            "data_mode",
            "freshness_status",
            "last_refresh_source",
            "last_available_bar_date",
            "last_price",
            "rsi14",
            "distance_to_support_pct",
            "technical_score",
            "risk_score",
            "risk_level",
            "portfolio_fit_score",
            "final_opportunity_score",
            "recommendation",
            "suggested_buy_low",
            "suggested_buy_high",
            "suggested_weight_add",
        ]
    ].sort_values(
        ["final_opportunity_score", "technical_score"],
        ascending=False,
        na_position="last",
    )

    def style_recommendation(row: pd.Series) -> list[str]:
        color = ""
        if row["recommendation"] == "BUY_CANDIDATE":
            color = "background-color: rgba(39, 174, 96, 0.18);"
        elif row["recommendation"] == "WATCH":
            color = "background-color: rgba(241, 196, 15, 0.18);"
        elif row["recommendation"] == "AVOID":
            color = "background-color: rgba(231, 76, 60, 0.18);"
        return [color] * len(row)

    st.dataframe(
        display_frame.style.apply(style_recommendation, axis=1),
        use_container_width=True,
        hide_index=True,
    )
