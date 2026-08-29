from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import streamlit as st
from sqlalchemy import select

from app.components.estimated_volume_profile import (  # noqa: E402
    build_estimated_volume_profile_figure,
)
from data.database import AssetDataStatusORM, session_scope  # noqa: E402
from data.repositories.assets_repo import AssetsRepository  # noqa: E402
from data.repositories.prices_repo import PricesRepository  # noqa: E402
from services.estimated_volume_profile_service import (  # noqa: E402
    EstimatedVolumeProfileService,
)

PERIOD_OPTIONS = {
    "6M": pd.DateOffset(months=6),
    "1Y": pd.DateOffset(years=1),
    "2Y": pd.DateOffset(years=2),
    "3Y": pd.DateOffset(years=3),
    "5Y": pd.DateOffset(years=5),
    "MAX": None,
}


def _filter_period(frame: pd.DataFrame, period: str) -> tuple[pd.DataFrame, pd.Timestamp | None]:
    data = frame.copy()
    data["date"] = pd.to_datetime(data["date"], errors="coerce")
    data = data.dropna(subset=["date"]).sort_values("date")
    offset = PERIOD_OPTIONS[period]
    if data.empty or offset is None:
        return data, None
    requested_start = data["date"].max() - offset
    return data[data["date"] >= requested_start].copy(), requested_start


def _format_price(value: float) -> str:
    return f"{value:,.2f}"


def _metric_label(label: str, currency: str | None = None) -> str:
    return f"{label} ({currency})" if currency else label


st.set_page_config(page_title="Volume Profile Lab", layout="wide")
st.markdown(
    """
    <style>
    /* Keep the six summary metrics readable without Streamlit's ellipsis. */
    div[data-testid="stMetricValue"] > div {
        font-size: clamp(1.55rem, 2.15vw, 2.35rem);
        line-height: 1.15;
        overflow: visible;
        text-overflow: clip;
        white-space: nowrap;
    }
    </style>
    """,
    unsafe_allow_html=True,
)
st.title("Volume Profile Lab")
st.caption(
    "Laboratorio visual aislado. El perfil reparte uniformemente el volumen diario entre "
    "los niveles atravesados por cada vela OHLCV almacenada en SQLite."
)
st.info(
    "Estimated Volume Profile: no representa volumen real negociado por precio intradia. "
    "No modifica scoring, soportes, recomendaciones, alertas ni backtesting."
)

with session_scope() as session:
    assets = AssetsRepository(session).list_enabled()
    asset_options = {f"{asset.symbol} — {asset.name}": asset.id for asset in assets}
    selected_label = st.selectbox("Activo", list(asset_options)) if asset_options else None
    if selected_label:
        asset_id = asset_options[selected_label]
        selected_asset = next(asset for asset in assets if asset.id == asset_id)
        price_frame = PricesRepository(session).get_asset_prices(asset_id)
        data_status = session.scalar(
            select(AssetDataStatusORM).where(AssetDataStatusORM.asset_id == asset_id).limit(1)
        )
        asset_context = {
            "symbol": selected_asset.symbol,
            "name": selected_asset.name,
            "currency": selected_asset.quote_currency,
            "data_mode": data_status.data_mode if data_status else "unknown",
            "source": data_status.last_refresh_source if data_status else None,
        }
    else:
        price_frame = pd.DataFrame()
        asset_context = {}

if not selected_label:
    st.warning("No hay activos habilitados en el inventario.")
    st.stop()

control_col1, control_col2, control_col3 = st.columns([1.2, 1, 1])
period = control_col1.selectbox("Periodo historico", list(PERIOD_OPTIONS), index=2)
bin_count = control_col2.select_slider(
    "Numero de bins",
    options=[60, 80, 100, 120, 150, 200],
    value=120,
)
hvn_count = control_col3.slider("Numero maximo de HVN", 3, 10, 6)

if price_frame.empty:
    st.warning("El activo no tiene barras OHLCV disponibles en SQLite.")
    st.stop()

filtered_frame, requested_start = _filter_period(price_frame, period)
available_start = pd.to_datetime(price_frame["date"]).min()
if requested_start is not None and available_start > requested_start:
    st.warning(
        "El historico local no cubre todo el periodo solicitado. "
        f"Se utilizaran solamente las barras disponibles desde {available_start:%Y-%m-%d}."
    )
if asset_context["data_mode"] == "demo":
    st.warning(
        "Este activo contiene datos demo. El perfil se muestra como simulacion visual y no "
        "debe interpretarse como informacion real de mercado."
    )
if len(filtered_frame) < 20:
    st.warning(
        f"Solo hay {len(filtered_frame)} barras en el periodo. Se necesitan al menos 20 "
        "para una lectura visual minimamente estable."
    )
    st.stop()

service = EstimatedVolumeProfileService()
try:
    result = service.calculate(filtered_frame, bins=bin_count, max_hvns=hvn_count)
except ValueError as exc:
    st.warning(f"No se puede calcular el Estimated Volume Profile: {exc}")
    st.stop()

distance_to_poc = result.poc.distance_to_current_price_pct
metric_columns = st.columns(6)
metric_columns[0].metric(
    _metric_label("Precio actual", asset_context["currency"]),
    _format_price(result.current_price),
)
metric_columns[1].metric(
    _metric_label("POC estimado", asset_context["currency"]),
    _format_price(result.poc.center),
)
metric_columns[2].metric("Distancia al POC", f"{distance_to_poc:+.2f}%")
metric_columns[3].metric("HVN detectados", len(result.hvns))
metric_columns[4].metric(
    "Periodo efectivo",
    f"{filtered_frame['date'].min():%m/%y}–{filtered_frame['date'].max():%m/%y}",
)
metric_columns[5].metric("Barras", result.bars_used)

figure = build_estimated_volume_profile_figure(
    filtered_frame,
    result,
    symbol=asset_context["symbol"],
)
st.plotly_chart(figure, width="stretch", config={"displaylogo": False})

st.subheader("POC y High Volume Nodes")
table = pd.DataFrame(service.table_rows(result))
table["Relative Intensity"] = table["Relative Intensity"] * 100.0
st.dataframe(
    table,
    width="stretch",
    hide_index=True,
    column_config={
        "Price Center": st.column_config.NumberColumn(format="%.4f"),
        "Zone Low": st.column_config.NumberColumn(format="%.4f"),
        "Zone High": st.column_config.NumberColumn(format="%.4f"),
        "Estimated Volume": st.column_config.NumberColumn(format="%.0f"),
        "Relative Intensity": st.column_config.ProgressColumn(
            min_value=0.0,
            max_value=100.0,
            format="%.1f%%",
        ),
        "Distance from Current Price %": st.column_config.NumberColumn(format="%+.2f%%"),
    },
)

with st.expander("Metodologia y limitaciones", expanded=False):
    st.markdown(
        f"""
        - **Fuente:** barras diarias OHLCV ya almacenadas en SQLite.
        - **Proveedor de la ultima carga:** `{asset_context['source'] or 'N/A'}`.
        - **Bins:** {bin_count} intervalos uniformes entre el minimo Low y el maximo High.
        - **Asignacion:** el volumen de cada vela se divide por igual entre todos los bins
          atravesados desde Low hasta High.
        - **POC:** bin con mayor volumen estimado acumulado.
        - **HVN:** maximos locales con al menos un 40% de la intensidad del POC, ademas de
          prominencia y separacion minimas; los bins vecinos suficientemente intensos se
          agrupan como una zona. El control de la pantalla limita el maximo, no fuerza una
          cantidad concreta de nodos.
        - El metodo no dispone de volumen intradia por precio, bid/ask ni sesiones separadas.
          Su uso es exclusivamente exploratorio y visual.
        """
    )
