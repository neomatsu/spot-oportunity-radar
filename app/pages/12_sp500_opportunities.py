from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.sp500_scoring_service import SP500ScoringService  # noqa: E402
from services.sp500_universe_ranking_service import (  # noqa: E402
    SP500UniverseRankingService,
)


@st.cache_data(ttl=60, show_spinner=False)
def _load_snapshot() -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    result = SP500UniverseRankingService().load()
    return result.ranking, result.errors, result.metadata


@st.cache_data(ttl=300, show_spinner=False)
def _load_price_history(yahoo_symbol: str) -> pd.DataFrame:
    return SP500ScoringService().cached_price_history(yahoo_symbol)


def _score_color(value: Any) -> str:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return ""
    if score >= 70:
        return "background-color: #d9f4df; color: #14532d"
    if score >= 60:
        return "background-color: #eef7d3; color: #365314"
    if score < 40:
        return "background-color: #fde2e2; color: #7f1d1d"
    return ""


def _risk_color(value: Any) -> str:
    try:
        risk = float(value)
    except (TypeError, ValueError):
        return ""
    if risk >= 70:
        return "background-color: #fde2e2; color: #7f1d1d"
    if risk <= 35:
        return "background-color: #d9f4df; color: #14532d"
    return ""


def _price_chart(frame: pd.DataFrame, symbol: str) -> go.Figure:
    data = frame.copy()
    data["date"] = pd.to_datetime(data["date"])
    data = data.sort_values("date").tail(260)
    data["sma50"] = data["close"].rolling(50).mean()
    data["sma200"] = data["close"].rolling(200).mean()
    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=data["date"],
            y=data["close"],
            name=symbol,
            line={"color": "#17324d", "width": 2},
        )
    )
    figure.add_trace(
        go.Scatter(
            x=data["date"],
            y=data["sma50"],
            name="SMA50",
            line={"color": "#e76f51", "width": 1.5},
        )
    )
    figure.add_trace(
        go.Scatter(
            x=data["date"],
            y=data["sma200"],
            name="SMA200",
            line={"color": "#2a9d8f", "width": 1.5},
        )
    )
    figure.update_layout(
        height=390,
        margin={"l": 10, "r": 10, "t": 20, "b": 10},
        hovermode="x unified",
        legend={"orientation": "h", "y": 1.08},
        xaxis_title=None,
        yaxis_title="Precio",
    )
    return figure


st.set_page_config(page_title="S&P 500 Opportunities", layout="wide")
st.title("S&P 500 Opportunities")
st.caption(
    "Ranking consultivo del universo S&P 500 con el scoring actual. No anade activos "
    "al seguimiento y no modifica recommendations operativas ni alertas."
)

action_col, note_col = st.columns([1.3, 3.7], vertical_alignment="center")
refresh_clicked = action_col.button(
    "Actualizar ranking",
    type="primary",
    use_container_width=True,
)
note_col.caption(
    "Actualiza precios incrementalmente y recalcula el ranking desde la cache local."
)

if refresh_clicked:
    with st.spinner("Actualizando precios y puntuando el universo S&P 500..."):
        try:
            refreshed_result = SP500UniverseRankingService().refresh()
            _load_snapshot.clear()
            _load_price_history.clear()
            st.success(
                f"Ranking actualizado: {len(refreshed_result.ranking)} empresas; "
                f"{len(refreshed_result.errors)} incidencias."
            )
        except Exception as exc:
            st.error(f"No se pudo actualizar el ranking: {exc}")

ranking, errors, metadata = _load_snapshot()
if ranking.empty:
    st.info("Todavia no hay un ranking cacheado. Pulsa 'Actualizar ranking' para generarlo.")
    st.stop()

for column in [
    "final_score",
    "technical_score",
    "risk_score",
    "rsi14",
    "distance_to_support_pct",
    "last_price",
]:
    if column in ranking:
        ranking[column] = pd.to_numeric(ranking[column], errors="coerce")

finished_at = metadata.get("finished_at_utc", "N/A")
summary_cols = st.columns(5)
summary_cols[0].metric("Empresas puntuadas", len(ranking))
summary_cols[1].metric(
    "BUY_CANDIDATE", int((ranking["recommendation"] == "BUY_CANDIDATE").sum())
)
summary_cols[2].metric("Score mediano", f"{ranking['final_score'].median():.1f}")
summary_cols[3].metric("Mejor score", f"{ranking['final_score'].max():.1f}")
summary_cols[4].metric("Incidencias", len(errors))
st.caption(f"Ultimo calculo: {finished_at} · Contexto de cartera neutral")

st.subheader("Filtros")
filter_cols = st.columns([1.6, 1.4, 1.4, 1.2])
query = filter_cols[0].text_input("Ticker o empresa", placeholder="MSFT, Nvidia...")
sector_options = sorted(ranking["sector"].dropna().astype(str).unique())
selected_sectors = filter_cols[1].multiselect("Sector", sector_options)
recommendation_options = sorted(
    ranking["recommendation"].dropna().astype(str).unique()
)
selected_recommendations = filter_cols[2].multiselect(
    "Recommendation", recommendation_options, default=recommendation_options
)
regime_options = sorted(ranking["dominant_regime"].dropna().astype(str).unique())
selected_regimes = filter_cols[3].multiselect(
    "Regimen", regime_options, default=regime_options
)

threshold_cols = st.columns(2)
min_score = threshold_cols[0].slider("Score final minimo", 0.0, 100.0, 55.0, 1.0)
max_risk = threshold_cols[1].slider("Risk score maximo", 0.0, 100.0, 70.0, 1.0)

filtered = SP500UniverseRankingService.filter_ranking(
    ranking,
    query=query,
    sectors=selected_sectors,
    recommendations=selected_recommendations,
    regimes=selected_regimes,
    min_final_score=min_score,
    max_risk_score=max_risk,
)
st.subheader(f"Ranking ({len(filtered)} resultados)")

display_columns = [
    "rank",
    "symbol",
    "company",
    "sector",
    "price_date",
    "last_price",
    "final_score",
    "technical_score",
    "risk_score",
    "rsi14",
    "distance_to_support_pct",
    "recommendation",
    "dominant_regime",
]
display = filtered[[column for column in display_columns if column in filtered]].rename(
    columns={
        "rank": "Rank global",
        "symbol": "Ticker",
        "company": "Empresa",
        "sector": "Sector",
        "price_date": "Fecha",
        "last_price": "Precio",
        "final_score": "Score final",
        "technical_score": "Technical",
        "risk_score": "Risk",
        "rsi14": "RSI14",
        "distance_to_support_pct": "Dist. soporte %",
        "recommendation": "Recommendation",
        "dominant_regime": "Regimen",
    }
)
styler = display.style.map(_score_color, subset=["Score final"]).map(
    _risk_color, subset=["Risk"]
)
st.dataframe(
    styler,
    hide_index=True,
    width="stretch",
    height=min(720, 38 + max(1, min(len(display), 17)) * 35),
    column_config={
        "Precio": st.column_config.NumberColumn(format="%.2f"),
        "Score final": st.column_config.NumberColumn(format="%.1f"),
        "Technical": st.column_config.NumberColumn(format="%.1f"),
        "Risk": st.column_config.NumberColumn(format="%.1f"),
        "RSI14": st.column_config.NumberColumn(format="%.1f"),
        "Dist. soporte %": st.column_config.NumberColumn(format="%.1f%%"),
    },
)
st.download_button(
    "Descargar resultados filtrados (CSV)",
    data=filtered.to_csv(index=False).encode("utf-8"),
    file_name="sp500_opportunities_filtered.csv",
    mime="text/csv",
)

st.subheader("Ficha rapida")
if filtered.empty:
    st.info("No hay empresas que cumplan los filtros actuales.")
else:
    labels = {
        f"{row.symbol} · {row.company}": row.symbol
        for row in filtered[["symbol", "company"]].itertuples(index=False)
    }
    selected_label = st.selectbox("Empresa", list(labels))
    selected = filtered.loc[filtered["symbol"] == labels[selected_label]].iloc[0]
    detail_cols = st.columns(6)
    detail_cols[0].metric("Score final", f"{selected['final_score']:.1f}")
    detail_cols[1].metric("Technical", f"{selected['technical_score']:.1f}")
    detail_cols[2].metric("Risk", f"{selected['risk_score']:.1f}")
    detail_cols[3].metric("RSI14", f"{selected['rsi14']:.1f}")
    detail_cols[4].metric("Recommendation", str(selected["recommendation"]))
    detail_cols[5].metric("Regimen", str(selected.get("dominant_regime") or "N/A"))
    st.caption(
        f"{selected['company']} · {selected['sector']} · "
        f"{selected.get('sub_industry', '')} · Datos {selected['price_date']}"
    )
    history = _load_price_history(str(selected["yahoo_symbol"]))
    if history.empty:
        st.warning("No hay historico local para mostrar el grafico.")
    else:
        st.plotly_chart(
            _price_chart(history, str(selected["symbol"])),
            width="stretch",
        )

if not errors.empty:
    with st.expander(f"Incidencias del ultimo calculo ({len(errors)})"):
        st.dataframe(errors, hide_index=True, width="stretch")

st.info(
    "Modulo independiente y consultivo. Usa componentes actuales del S&P 500, por lo "
    "que puede contener survivorship bias y no debe interpretarse como recomendacion financiera."
)
