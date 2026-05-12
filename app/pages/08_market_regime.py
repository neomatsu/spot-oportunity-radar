from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from core.config import load_yaml_config  # noqa: E402
from data.database import init_db, session_scope  # noqa: E402
from data.repositories.assets_repo import AssetsRepository  # noqa: E402
from data.repositories.market_regime_repo import MarketRegimeRepository  # noqa: E402
from data.repositories.prices_repo import PricesRepository  # noqa: E402
from market_regime import MarketRegimeService  # noqa: E402

RANGE_OPTIONS = {
    "Todo": None,
    "5 anos": 365 * 5,
    "2 anos": 365 * 2,
    "1 ano": 365,
    "6 meses": 182,
}

REGIME_COLORS = {
    "BULL": "#16a34a",
    "BEAR": "#dc2626",
    "TRANSITION": "#d97706",
    "PENDING": "#64748b",
}


def _badge(label: str, bubble_probability: float) -> None:
    color = REGIME_COLORS.get(label, "#6b7280")
    bubble_label = "Bubble risk high" if bubble_probability >= 60 else "Bubble risk normal"
    bubble_color = "#d97706" if bubble_probability >= 60 else "#64748b"
    st.markdown(
        f"""
        <div style="display:flex; gap:8px; align-items:center; margin: 4px 0 16px 0;">
          <span style="background:{color}; color:white; padding:4px 10px;
                       border-radius:6px; font-weight:600;">
            {label}
          </span>
          <span style="background:{bubble_color}; color:white; padding:4px 10px;
                       border-radius:6px; font-weight:600;">
            {bubble_label}
          </span>
        </div>
        """,
        unsafe_allow_html=True,
    )


def _probability_chart(row: dict) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(
        go.Bar(
            x=["Bull", "Bear", "Bubble"],
            y=[
                row["bull_probability"],
                row["bear_probability"],
                row["bubble_probability"],
            ],
            marker_color=["#16a34a", "#dc2626", "#d97706"],
        )
    )
    fig.update_layout(height=260, margin=dict(t=20, b=20), yaxis_range=[0, 100])
    return fig


def _history_chart(history: pd.DataFrame, prices: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=history["date"],
            y=history["bull_probability"],
            mode="lines+markers",
            name="Bull",
            line=dict(color="#16a34a", width=2),
            marker=dict(size=6),
        )
    )
    fig.add_trace(
        go.Scatter(
            x=history["date"],
            y=history["bear_probability"],
            mode="lines+markers",
            name="Bear",
            line=dict(color="#dc2626", width=2),
            marker=dict(size=6),
        )
    )
    fig.add_trace(
        go.Scatter(
            x=history["date"],
            y=history["bubble_probability"],
            mode="lines+markers",
            name="Bubble",
            line=dict(color="#d97706", width=2),
            marker=dict(size=6),
        )
    )
    if not prices.empty:
        price_frame = prices.copy()
        price_frame["date"] = pd.to_datetime(price_frame["date"])
        fig.add_trace(
            go.Scatter(
                x=price_frame["date"],
                y=price_frame["close"],
                mode="lines",
                name="Precio",
                yaxis="y2",
                line=dict(color="#64748b", width=1.2, dash="dot"),
                opacity=0.65,
            )
        )
    fig.update_layout(
        height=420,
        margin=dict(t=30, b=20),
        yaxis=dict(title="Probabilidad", range=[0, 100]),
        yaxis2=dict(title="Precio", overlaying="y", side="right", showgrid=False),
        legend=dict(orientation="h", y=1.08),
    )
    return fig


def _build_row(symbol: str, regime_record) -> dict:
    return {
        "symbol": symbol,
        "date": regime_record.date,
        "bull_probability": regime_record.bull_probability,
        "bear_probability": regime_record.bear_probability,
        "bubble_probability": regime_record.bubble_probability,
        "dominant_regime": regime_record.dominant_regime,
    }


def _empty_table_row(asset) -> dict:
    return {
        "symbol": asset.symbol,
        "name": asset.name,
        "asset_type": asset.asset_type,
        "sector": asset.sector,
        "date": None,
        "bull_probability": None,
        "bear_probability": None,
        "bubble_probability": None,
        "dominant_regime": "PENDING",
        "bubble_risk": "N/A",
    }


def _regime_table_row(asset, regime_record) -> dict:
    if regime_record is None:
        return _empty_table_row(asset)
    bubble_risk = (
        "HIGH"
        if regime_record.bubble_probability >= 60
        else "WATCH"
        if regime_record.bubble_probability >= 45
        else "NORMAL"
    )
    return {
        "symbol": asset.symbol,
        "name": asset.name,
        "asset_type": asset.asset_type,
        "sector": asset.sector,
        "date": regime_record.date,
        "bull_probability": regime_record.bull_probability,
        "bear_probability": regime_record.bear_probability,
        "bubble_probability": regime_record.bubble_probability,
        "dominant_regime": regime_record.dominant_regime,
        "bubble_risk": bubble_risk,
    }


def _regime_row_style(row: pd.Series) -> list[str]:
    regime = row.get("Regimen")
    bubble = row.get("Bubble Risk")
    if regime == "BULL":
        background = "background-color: rgba(22, 163, 74, 0.12)"
    elif regime == "BEAR":
        background = "background-color: rgba(220, 38, 38, 0.12)"
    elif regime == "TRANSITION":
        background = "background-color: rgba(217, 119, 6, 0.12)"
    else:
        background = "background-color: rgba(100, 116, 139, 0.08)"

    styles = [background] * len(row)
    if bubble == "HIGH":
        styles = [
            "background-color: rgba(217, 119, 6, 0.18); font-weight: 600"
        ] * len(row)
    return styles


def _probability_color(value: float | None) -> str:
    if value is None or pd.isna(value):
        return ""
    if value >= 65:
        return "font-weight: 700"
    if value >= 50:
        return "font-weight: 600"
    return ""


init_db()

st.title("Market Regime Detector")
st.caption(
    "Contexto de mercado por activo. Modulo observacional: "
    "no modifica scoring ni recomendaciones."
)

with session_scope() as session:
    assets_repo = AssetsRepository(session)
    prices_repo = PricesRepository(session)
    regime_repo = MarketRegimeRepository(session)
    regime_service = MarketRegimeService()
    config = load_yaml_config("regime_config.yaml")
    benchmark_symbol = str(
        config.get("relative_strength", {}).get("benchmark_symbol", "SPY")
    )

    assets = assets_repo.list_enabled()
    asset_options = {f"{asset.symbol} - {asset.name}": asset for asset in assets}
    benchmark_asset = assets_repo.get_by_symbol(benchmark_symbol)
    benchmark_frame = (
        prices_repo.get_asset_prices(benchmark_asset.id)
        if benchmark_asset is not None
        else pd.DataFrame()
    )

    top_col1, top_col2, top_col3 = st.columns([1.5, 1, 1])
    compute_all = top_col1.button(
        "Actualizar todos los regimenes",
        use_container_width=True,
        type="primary",
    )
    asset_type_filter = top_col2.selectbox(
        "Tipo",
        ["Todos", "stock", "etf", "crypto"],
        key="regime_asset_type_filter",
    )
    regime_filter = top_col3.multiselect(
        "Regimen",
        ["BULL", "BEAR", "TRANSITION", "PENDING"],
        default=[],
        placeholder="Todos",
        key="regime_state_filter",
    )

    if compute_all:
        computed = 0
        skipped = 0
        with st.spinner("Calculando regimen de todos los activos..."):
            for asset in assets:
                price_frame = prices_repo.get_asset_prices(asset.id)
                if price_frame.empty:
                    skipped += 1
                    continue
                regime = regime_service.compute_regime(
                    price_frame,
                    benchmark_frame=benchmark_frame,
                )
                regime_repo.upsert_regime(
                    asset_id=asset.id,
                    symbol=asset.symbol,
                    regime=regime,
                )
                computed += 1
        st.success(f"Regimenes actualizados: {computed}. Sin historico: {skipped}.")

    table_rows = [
        _regime_table_row(asset, regime_repo.latest_for_asset(asset.id))
        for asset in assets
    ]
    latest_frame = pd.DataFrame(table_rows)
    if asset_type_filter != "Todos" and not latest_frame.empty:
        latest_frame = latest_frame[latest_frame["asset_type"] == asset_type_filter]
    if regime_filter and not latest_frame.empty:
        latest_frame = latest_frame[latest_frame["dominant_regime"].isin(regime_filter)]

    if not latest_frame.empty:
        st.subheader("Mapa de regimen por activo")
        latest_frame = latest_frame.sort_values(
            ["dominant_regime", "bubble_probability", "bull_probability"],
            ascending=[True, False, False],
        )
        display_frame = latest_frame.rename(
            columns={
                "symbol": "Simbolo",
                "name": "Nombre",
                "asset_type": "Tipo",
                "sector": "Sector",
                "date": "Fecha",
                "bull_probability": "Bull %",
                "bear_probability": "Bear %",
                "bubble_probability": "Bubble %",
                "dominant_regime": "Regimen",
                "bubble_risk": "Bubble Risk",
            }
        )
        styler = display_frame.style.apply(_regime_row_style, axis=1)
        for column in ["Bull %", "Bear %", "Bubble %"]:
            styler = styler.map(_probability_color, subset=[column])
        st.dataframe(
            styler,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Bull %": st.column_config.NumberColumn("Bull %", format="%.1f"),
                "Bear %": st.column_config.NumberColumn("Bear %", format="%.1f"),
                "Bubble %": st.column_config.NumberColumn("Bubble %", format="%.1f"),
            },
        )
        summary_cols = st.columns(4)
        summary_cols[0].metric("Activos", len(latest_frame))
        summary_cols[1].metric(
            "Bull",
            int((latest_frame["dominant_regime"] == "BULL").sum()),
        )
        summary_cols[2].metric(
            "Bear",
            int((latest_frame["dominant_regime"] == "BEAR").sum()),
        )
        summary_cols[3].metric(
            "Bubble high",
            int((latest_frame["bubble_risk"] == "HIGH").sum()),
        )

    st.subheader("Analisis por activo")
    col1, col2 = st.columns([2, 1])
    selected_label = col1.selectbox("Activo", list(asset_options) if asset_options else [])
    range_label = col2.selectbox("Rango historico", list(RANGE_OPTIONS), index=2)

    if selected_label:
        asset = asset_options[selected_label]
        price_frame = prices_repo.get_asset_prices(asset.id)
        if price_frame.empty:
            st.warning("No hay historico de precios para este activo.")
        else:
            price_frame["date"] = pd.to_datetime(price_frame["date"])
            lookback_days = RANGE_OPTIONS[range_label]
            if lookback_days is not None:
                cutoff = price_frame["date"].max() - pd.Timedelta(days=lookback_days)
                chart_price_frame = price_frame[price_frame["date"] >= cutoff].copy()
                start_date = cutoff.date()
            else:
                chart_price_frame = price_frame.copy()
                start_date = price_frame["date"].min().date()
            end_date = price_frame["date"].max().date()

            action_col1, action_col2 = st.columns(2)
            compute_current = action_col1.button(
                "Calcular regimen actual",
                use_container_width=True,
                type="primary",
            )
            compute_history = action_col2.button(
                "Calcular historico del rango",
                use_container_width=True,
            )

            if compute_current:
                with st.spinner("Calculando regimen actual..."):
                    regime = regime_service.compute_regime(
                        price_frame,
                        benchmark_frame=benchmark_frame,
                    )
                    regime_repo.upsert_regime(
                        asset_id=asset.id,
                        symbol=asset.symbol,
                        regime=regime,
                    )
                    st.success("Regimen actual guardado.")

            if compute_history:
                with st.spinner("Calculando historico de regimen..."):
                    history = regime_service.compute_regime_history(
                        price_frame,
                        benchmark_frame=benchmark_frame,
                        start_date=start_date,
                        end_date=end_date,
                    )
                    inserted = regime_repo.upsert_history(
                        asset_id=asset.id,
                        symbol=asset.symbol,
                        frame=history,
                    )
                    st.success(f"Historico actualizado. Nuevas filas: {inserted}.")

            latest = regime_repo.latest_for_asset(asset.id)
            if latest is None:
                st.info("Todavia no hay regimen persistido para este activo.")
            else:
                row = _build_row(asset.symbol, latest)
                _badge(row["dominant_regime"], row["bubble_probability"])
                metric_cols = st.columns(4)
                metric_cols[0].metric("Fecha", str(row["date"]))
                metric_cols[1].metric("Bull", f'{row["bull_probability"]:.1f}%')
                metric_cols[2].metric("Bear", f'{row["bear_probability"]:.1f}%')
                metric_cols[3].metric("Bubble", f'{row["bubble_probability"]:.1f}%')
                st.plotly_chart(_probability_chart(row), use_container_width=True)

                breakdown = latest.breakdown_json or {}
                if breakdown:
                    with st.expander("Breakdown del regimen", expanded=False):
                        st.json(breakdown)

            history = regime_repo.history_for_asset(
                asset.id,
                start_date=start_date,
                end_date=end_date,
            )
            if not history.empty:
                history["date"] = pd.to_datetime(history["date"])
                st.subheader("Evolucion del regimen")
                if len(history) < 2:
                    st.info(
                        "Solo hay un punto de regimen persistido para este rango. "
                        "Pulsa 'Calcular historico del rango' para ver las curvas completas."
                    )
                st.plotly_chart(
                    _history_chart(history, chart_price_frame),
                    use_container_width=True,
                )

                counts = history["dominant_regime"].value_counts().reset_index()
                counts.columns = ["Regimen", "Dias"]
                st.dataframe(counts, use_container_width=True, hide_index=True)
