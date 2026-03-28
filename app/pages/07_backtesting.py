from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import plotly.express as px
import streamlit as st

from backtesting.engine import BacktestEngine  # noqa: E402
from data.database import session_scope  # noqa: E402

st.title("Backtesting")
st.caption(
    "Version ligera basada en señales historicas ya persistidas. Sirve para una "
    "primera validacion cuantitativa, no sustituye un backtester completo."
)

recommendation_filter = st.selectbox(
    "Filtrar recomendacion",
    ["all", "BUY_CANDIDATE", "WATCH", "AVOID"],
)

with session_scope() as session:
    engine = BacktestEngine(session)
    result = engine.run(None if recommendation_filter == "all" else recommendation_filter)

metrics = st.columns(5)
metrics[0].metric("Trades evaluados", result.trades)
metrics[1].metric("Hit rate 20d", f"{result.hit_rate:.1f}%")
metrics[2].metric("Retorno medio 5d", f"{result.avg_return_5d:.2f}%")
metrics[3].metric("Retorno medio 10d", f"{result.avg_return_10d:.2f}%")
metrics[4].metric("Retorno medio 20d", f"{result.avg_return_20d:.2f}%")

st.metric("Peor retorno 20d", f"{result.worst_return_20d:.2f}%")

rows_df = pd.DataFrame(result.rows)
if rows_df.empty:
    st.info("Todavia no hay suficientes señales historicas para este filtro.")
else:
    st.dataframe(rows_df, use_container_width=True, hide_index=True)
    st.plotly_chart(
        px.scatter(
            rows_df,
            x="final_score",
            y="return_20d",
            color="recommendation",
            hover_name="symbol",
            title="Final score vs retorno a 20 sesiones",
        ),
        use_container_width=True,
    )
