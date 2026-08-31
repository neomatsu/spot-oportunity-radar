from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots
from sqlalchemy import select

from data.database import AssetORM, init_db, session_scope  # noqa: E402
from data.providers.base_provider import ProviderError  # noqa: E402
from data.providers.binance_futures_provider import (  # noqa: E402
    BinanceUsdMFuturesProvider,
)
from services.ema_pivot_strategy_service import (  # noqa: E402
    EmaPivotStrategyConfig,
    EmaPivotStrategyResult,
    EmaPivotStrategyService,
    StrategySetup,
)
from services.intraday_market_data_service import IntradayMarketDataService  # noqa: E402

PERIOD_OPTIONS = {"7 dias": 7, "14 dias": 14, "30 dias": 30}
STATUS_LABELS = {
    "PENDING": "Pendiente",
    "INVALIDATED": "Invalidada antes del fill",
    "EXPIRED": "Expirada",
    "OPEN": "Posicion abierta",
    "STOP": "Stop loss",
    "TP1_BE": "TP1 + runner en breakeven",
    "TP1_RUNNER_OPEN": "TP1 + runner abierto",
    "RUNNER_EXIT": "TP1 + salida runner",
}


def _setup_key(setup: StrategySetup) -> str:
    return setup.signal_time.strftime("%Y%m%dT%H%M%S")


def _build_chart(
    result: EmaPivotStrategyResult,
    setups: list[StrategySetup],
    *,
    visible_days: int,
) -> go.Figure:
    bars = result.bars.copy()
    cutoff = bars["open_time"].max() - pd.Timedelta(days=visible_days)
    visible = bars[bars["open_time"] >= cutoff]
    figure = go.Figure()
    figure.add_trace(
        go.Candlestick(
            x=visible["open_time"],
            open=visible["open"],
            high=visible["high"],
            low=visible["low"],
            close=visible["close"],
            name="BTCUSDT Perpetual",
            increasing_line_color="#0f9f8f",
            decreasing_line_color="#e64b4b",
        )
    )
    figure.add_trace(
        go.Scatter(
            x=visible["open_time"],
            y=visible["ema"],
            name="EMA12",
            line={"color": "#f4b942", "width": 1.5},
        )
    )

    visible_pivots = [pivot for pivot in result.pivots if pivot.confirmed_time >= cutoff]
    for kind, color, symbol in (
        ("HIGH", "#dc2626", "triangle-down"),
        ("LOW", "#eab308", "triangle-up"),
    ):
        selected = [pivot for pivot in visible_pivots if pivot.kind == kind]
        figure.add_trace(
            go.Scatter(
                x=[pivot.pivot_time for pivot in selected],
                y=[pivot.price for pivot in selected],
                mode="markers",
                name=f"Pivotes {kind.lower()}",
                marker={"color": color, "symbol": symbol, "size": 8},
                customdata=[[pivot.confirmed_time] for pivot in selected],
                hovertemplate=(
                    "Pivote %{y:,.2f}<br>Confirmado %{customdata[0]}<extra></extra>"
                ),
            )
        )

    for kind, color, label in (
        ("HIGH", "#dc2626", "Divergencia bajista RSI"),
        ("LOW", "#16a34a", "Divergencia alcista RSI"),
    ):
        divergent = [
            pivot
            for pivot in visible_pivots
            if pivot.kind == kind
            and pivot.divergence
            and pivot.divergence_from_time is not None
            and pivot.divergence_from_price is not None
        ]
        line_x: list[object | None] = []
        line_y: list[float | None] = []
        for pivot in divergent:
            line_x.extend([pivot.divergence_from_time, pivot.pivot_time, None])
            line_y.extend([pivot.divergence_from_price, pivot.price, None])
        figure.add_trace(
            go.Scatter(
                x=line_x,
                y=line_y,
                mode="lines",
                name=label,
                line={"color": color, "width": 2, "dash": "dot"},
                hoverinfo="skip",
            )
        )
        figure.add_trace(
            go.Scatter(
                x=[pivot.pivot_time for pivot in divergent],
                y=[pivot.price for pivot in divergent],
                mode="markers",
                name=f"Confirmacion {label.lower()}",
                marker={
                    "color": color,
                    "symbol": "diamond",
                    "size": 12,
                    "line": {"color": "white", "width": 1},
                },
                customdata=[
                    [pivot.divergence_from_rsi, pivot.rsi] for pivot in divergent
                ],
                hovertemplate=(
                    "%{fullData.name}<br>Precio %{y:,.2f}<br>RSI anterior "
                    "%{customdata[0]:.1f} → RSI actual %{customdata[1]:.1f}"
                    "<extra></extra>"
                ),
            )
        )

    for direction, color, symbol in (
        ("LONG", "#16a34a", "triangle-up"),
        ("SHORT", "#dc2626", "triangle-down"),
    ):
        selected = [setup for setup in setups if setup.direction == direction]
        figure.add_trace(
            go.Scatter(
                x=[setup.signal_time for setup in selected],
                y=[setup.entry for setup in selected],
                mode="markers",
                name=f"Setup {direction}",
                marker={
                    "color": color,
                    "symbol": symbol,
                    "size": [8 + setup.confirmation_score * 2 for setup in selected],
                    "line": {"color": "white", "width": 1},
                },
                customdata=[
                    [setup.confirmation_score, STATUS_LABELS.get(setup.status, setup.status)]
                    for setup in selected
                ],
                hovertemplate=(
                    "%{fullData.name}<br>Entrada limit %{y:,.2f}<br>Confirmaciones "
                    "%{customdata[0]}/5<br>%{customdata[1]}<extra></extra>"
                ),
            )
        )

    figure.update_layout(
        height=680,
        margin={"l": 10, "r": 10, "t": 35, "b": 10},
        legend={"orientation": "h", "y": 1.03},
        xaxis_rangeslider_visible=False,
        hovermode="x unified",
        yaxis_title="Precio (USDT)",
        template="plotly_white",
    )
    return figure


def _setup_rows(setups: tuple[StrategySetup, ...]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "Detalle": f"?trade={_setup_key(setup)}#detalle-trade",
                "Senal UTC": setup.signal_time.strftime("%Y-%m-%d %H:%M"),
                "Direccion": setup.direction,
                "Entrada limit": setup.entry,
                "Pivote": setup.pivot.price,
                "Stop": setup.stop,
                "TP1": setup.tp1,
                "Distancia ATR": setup.pivot_distance_atr,
                "Tendencia 1h": setup.trend,
                "Divergencia": "Si" if setup.divergence else "No",
                "Volumen": setup.volume_reference or "-",
                "Score /5": setup.confirmation_score,
                "Estado": STATUS_LABELS.get(setup.status, setup.status),
                "Resultado R": setup.result_r,
            }
            for setup in reversed(setups)
        ]
    )


def _setup_exit_price(setup: StrategySetup) -> float | None:
    if setup.status == "STOP":
        return setup.stop
    if setup.status == "TP1_BE":
        return setup.entry
    if setup.status != "RUNNER_EXIT" or setup.result_r is None:
        return None
    runner_fraction = 1.0 - 0.75
    risk = abs(setup.entry - setup.stop)
    tp1_r = abs(setup.tp1 - setup.entry) / risk
    base_result = 0.75 * tp1_r
    runner_r = (setup.result_r - base_result) / runner_fraction
    return (
        setup.entry + runner_r * risk
        if setup.direction == "LONG"
        else setup.entry - runner_r * risk
    )


def _first_tp1_touch(
    bars: pd.DataFrame, setup: StrategySetup
) -> pd.Timestamp | None:
    if setup.fill_time is None:
        return None
    end_time = setup.exit_time or bars["open_time"].max()
    trade_bars = bars[
        (bars["open_time"] >= setup.fill_time) & (bars["open_time"] <= end_time)
    ]
    touched = (
        trade_bars[trade_bars["high"] >= setup.tp1]
        if setup.direction == "LONG"
        else trade_bars[trade_bars["low"] <= setup.tp1]
    )
    return pd.Timestamp(touched.iloc[0]["open_time"]) if not touched.empty else None


def _build_trade_detail_chart(
    result: EmaPivotStrategyResult, setup: StrategySetup
) -> go.Figure:
    bars = result.bars
    reference_start = setup.pivot.pivot_time
    if setup.pivot.divergence_from_time is not None:
        reference_start = min(reference_start, setup.pivot.divergence_from_time)
    start_time = reference_start - pd.Timedelta(hours=1)
    natural_end = setup.exit_time or min(
        pd.Timestamp(bars["open_time"].max()),
        setup.signal_time + pd.Timedelta(hours=12),
    )
    end_time = natural_end + pd.Timedelta(hours=1)
    visible = bars[
        (bars["open_time"] >= start_time) & (bars["open_time"] <= end_time)
    ]
    if visible.empty:
        visible = bars.iloc[max(0, setup.signal_index - 60) : setup.signal_index + 120]
        end_time = pd.Timestamp(visible["open_time"].max())

    figure = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        row_heights=[0.72, 0.28],
        vertical_spacing=0.04,
    )
    figure.add_trace(
        go.Candlestick(
            x=visible["open_time"],
            open=visible["open"],
            high=visible["high"],
            low=visible["low"],
            close=visible["close"],
            name="BTCUSDT Perpetual",
            increasing_line_color="#0f9f8f",
            decreasing_line_color="#e64b4b",
        ),
        row=1,
        col=1,
    )
    figure.add_trace(
        go.Scatter(
            x=visible["open_time"],
            y=visible["ema"],
            name="EMA12",
            line={"color": "#f4b942", "width": 2},
        ),
        row=1,
        col=1,
    )
    figure.add_trace(
        go.Scatter(
            x=visible["open_time"],
            y=visible["rsi"],
            name="RSI14",
            line={"color": "#475569", "width": 1.5},
        ),
        row=2,
        col=1,
    )

    pivot_color = "#eab308" if setup.direction == "LONG" else "#dc2626"
    figure.add_trace(
        go.Scatter(
            x=[setup.pivot.pivot_time, setup.pivot.confirmed_time],
            y=[setup.pivot.price, setup.pivot.price],
            mode="lines+markers",
            name="Pivote y confirmacion",
            line={"color": pivot_color, "width": 2, "dash": "dot"},
            marker={"size": [11, 7], "symbol": ["diamond", "circle"]},
        ),
        row=1,
        col=1,
    )

    for name, price, color, dash in (
        ("Entrada limit", setup.entry, "#2563eb", "dash"),
        ("Stop", setup.stop, "#dc2626", "solid"),
        ("TP1", setup.tp1, "#16a34a", "solid"),
    ):
        figure.add_trace(
            go.Scatter(
                x=[setup.signal_time, end_time],
                y=[price, price],
                mode="lines",
                name=name,
                line={"color": color, "width": 1.6, "dash": dash},
                hovertemplate=f"{name} {price:,.2f}<extra></extra>",
            ),
            row=1,
            col=1,
        )

    figure.add_trace(
        go.Scatter(
            x=[setup.signal_time],
            y=[setup.entry],
            mode="markers",
            name="Senal EMA",
            marker={"color": "#2563eb", "symbol": "star", "size": 14},
        ),
        row=1,
        col=1,
    )
    if setup.fill_time is not None:
        figure.add_trace(
            go.Scatter(
                x=[setup.fill_time],
                y=[setup.entry],
                mode="markers",
                name="Fill limit",
                marker={"color": "#0891b2", "symbol": "circle", "size": 12},
            ),
            row=1,
            col=1,
        )
    tp1_time = (
        _first_tp1_touch(bars, setup)
        if setup.status in {"TP1_BE", "TP1_RUNNER_OPEN", "RUNNER_EXIT"}
        else None
    )
    if tp1_time is not None:
        figure.add_trace(
            go.Scatter(
                x=[tp1_time],
                y=[setup.tp1],
                mode="markers",
                name="TP1 alcanzado",
                marker={"color": "#16a34a", "symbol": "diamond", "size": 12},
            ),
            row=1,
            col=1,
        )
    exit_price = _setup_exit_price(setup)
    if setup.exit_time is not None and exit_price is not None:
        figure.add_trace(
            go.Scatter(
                x=[setup.exit_time],
                y=[exit_price],
                mode="markers",
                name="Salida",
                marker={"color": "#111827", "symbol": "x", "size": 13},
            ),
            row=1,
            col=1,
        )

    pivot = setup.pivot
    if (
        pivot.divergence
        and pivot.divergence_from_time is not None
        and pivot.divergence_from_price is not None
        and pivot.divergence_from_rsi is not None
        and pivot.rsi is not None
    ):
        divergence_color = "#dc2626" if pivot.kind == "HIGH" else "#16a34a"
        figure.add_trace(
            go.Scatter(
                x=[pivot.divergence_from_time, pivot.pivot_time],
                y=[pivot.divergence_from_price, pivot.price],
                mode="lines+markers",
                name="Divergencia en precio",
                line={"color": divergence_color, "width": 3},
            ),
            row=1,
            col=1,
        )
        figure.add_trace(
            go.Scatter(
                x=[pivot.divergence_from_time, pivot.pivot_time],
                y=[pivot.divergence_from_rsi, pivot.rsi],
                mode="lines+markers",
                name="Divergencia en RSI",
                line={"color": divergence_color, "width": 3},
            ),
            row=2,
            col=1,
        )

    for level in (30, 70):
        figure.add_hline(
            y=level,
            row=2,
            col=1,
            line={"color": "#94a3b8", "width": 1, "dash": "dash"},
        )
    figure.update_layout(
        height=820,
        margin={"l": 10, "r": 10, "t": 35, "b": 10},
        legend={"orientation": "h", "y": 1.02},
        xaxis_rangeslider_visible=False,
        hovermode="x unified",
        template="plotly_white",
    )
    figure.update_yaxes(title_text="Precio (USDT)", row=1, col=1)
    figure.update_yaxes(title_text="RSI14", range=[0, 100], row=2, col=1)
    return figure


def _render_trade_detail(
    result: EmaPivotStrategyResult, setup: StrategySetup
) -> None:
    st.markdown('<div id="detalle-trade"></div>', unsafe_allow_html=True)
    st.subheader(
        f"Detalle {_setup_key(setup)} · {setup.direction} · "
        f"{STATUS_LABELS.get(setup.status, setup.status)}"
    )
    summary = st.columns(6)
    summary[0].metric("Entrada limit", f"{setup.entry:,.2f}")
    summary[1].metric("Stop", f"{setup.stop:,.2f}")
    summary[2].metric("TP1", f"{setup.tp1:,.2f}")
    summary[3].metric("Riesgo", f"{abs(setup.entry - setup.stop):,.2f}")
    summary[4].metric("Confirmaciones", f"{setup.confirmation_score}/5")
    summary[5].metric(
        "Resultado",
        f"{setup.result_r:.2f}R" if setup.result_r is not None else "Abierto/N.A.",
    )
    st.caption(
        f"Pivote {setup.pivot.kind} {setup.pivot.pivot_time:%Y-%m-%d %H:%M} UTC, "
        f"confirmado {setup.pivot.confirmed_time:%H:%M} · "
        f"distancia {setup.pivot_distance_atr:.2f} ATR · tendencia 1h {setup.trend} · "
        f"volumen {setup.volume_reference or 'sin confirmacion'} · "
        f"divergencia RSI {'si' if setup.divergence else 'no'}."
    )
    st.plotly_chart(
        _build_trade_detail_chart(result, setup),
        width="stretch",
        config={"displaylogo": False},
    )
    st.markdown("[Cerrar detalle](?)")


st.set_page_config(page_title="EMA + Pivot 5m Lab", layout="wide")
init_db()
st.title("BTCUSDT EMA + Pivot 5m Lab")
st.caption(
    "Prototipo causal sobre Binance USD-M Futures. Detecta setups, simula ordenes limit "
    "y evalua confirmaciones sin conectarse a una cuenta de trading."
)
st.warning(
    "Laboratorio experimental: no crea ordenes reales ni modifica scoring, portfolio, "
    "alertas, velas diarias o backtests existentes. Cada setup se simula de forma "
    "independiente; todavia no es un backtest de cartera."
)

with st.expander("Reglas implementadas", expanded=False):
    st.markdown(
        """
        - El pivote requiere máximos/mínimos inferiores a ambos lados y solo se usa tras
          cerrar las barras de confirmación de la derecha.
        - El cruce de cierre sobre/bajo EMA genera una limit en la EMA para la siguiente vela.
        - Stop en pivote ± buffer ATR; TP1 en 1,7R para el 75%; runner del 25% a breakeven.
        - La orden expira tras 12 velas o al recorrer el 80% del camino a TP1 sin fill.
        - Confirmaciones: tendencia EMA50/EMA200 de 1h, divergencia RSI extrema y cercanía
          al POC/HVN causal de los siete días anteriores.
        - La divergencia compara el extremo RSI más relevante de las últimas 72 velas
          (seis horas), usa un entorno local de hasta ±6 velas y exige una diferencia
          mínima de 1,5 puntos. Basta con que uno de los dos extremos alcance RSI 70/30.
        """
    )

period_col, refresh_col = st.columns([1, 2])
period_label = period_col.selectbox("Historico 5m", list(PERIOD_OPTIONS), index=1)
force_refresh = refresh_col.button("Actualizar Binance Futures", type="primary")

with st.expander("Parametros del prototipo", expanded=False):
    c1, c2, c3, c4 = st.columns(4)
    pivot_left = c1.number_input("Barras pivote izquierda", 2, 20, 5)
    pivot_right = c2.number_input("Barras pivote derecha", 2, 20, 5)
    pivot_lookback = c3.number_input("Vigencia pivote (velas)", 50, 1000, 400, step=50)
    min_distance = c4.number_input("Distancia minima (ATR)", 0.0, 5.0, 0.5, step=0.1)
    c5, c6, c7, c8 = st.columns(4)
    stop_buffer = c5.number_input("Buffer stop (ATR)", 0.0, 2.0, 0.1, step=0.05)
    tp_r = c6.number_input("TP1 (R)", 0.5, 5.0, 1.7, step=0.1)
    expiry = c7.number_input("Expiracion limit (velas)", 1, 100, 12)
    invalidation = c8.number_input(
        "Invalidar camino a TP1 (%)", 10, 100, 80, step=5
    )
    c9, c10, c11 = st.columns(3)
    divergence_lookback = c9.number_input(
        "Ventana divergencia 5m (velas)",
        12,
        96,
        72,
        step=12,
        help="72 velas equivalen a 6 horas; el maximo permitido equivale a 8 horas.",
    )
    divergence_rsi_window = c10.number_input(
        "Entorno local RSI (velas)", 0, 10, 6
    )
    divergence_min_delta = c11.number_input(
        "Diferencia minima RSI", 0.0, 20.0, 1.5, step=0.5
    )

days = PERIOD_OPTIONS[period_label]
end_at = datetime.now(UTC)
start_at = end_at - timedelta(days=days)
with session_scope() as session:
    asset = session.scalar(select(AssetORM).where(AssetORM.symbol == "BTCUSDT").limit(1))
    if asset is None:
        st.error("BTCUSDT no esta configurado en el inventario de activos.")
        st.stop()
    try:
        with st.spinner("Preparando velas 5m de Binance USD-M Futures..."):
            intraday = IntradayMarketDataService(
                session,
                provider=BinanceUsdMFuturesProvider(),
                market="usd_m_futures",
            ).get_crypto_bars(
                asset,
                start_at=start_at,
                end_at=end_at,
                force_refresh=force_refresh,
                interval="5m",
            )
    except (ProviderError, ValueError) as exc:
        st.error(f"No se pudieron cargar las velas Futures: {exc}")
        st.stop()

if intraday.refresh_error:
    st.warning(
        "No se completo la actualizacion, por lo que se utiliza la cache disponible: "
        f"{intraday.refresh_error}"
    )
if intraday.frame.empty:
    st.warning("La cache de Futures no contiene velas para el periodo seleccionado.")
    st.stop()

config = EmaPivotStrategyConfig(
    pivot_left_bars=int(pivot_left),
    pivot_right_bars=int(pivot_right),
    pivot_lookback_bars=int(pivot_lookback),
    stop_atr_buffer=float(stop_buffer),
    min_pivot_distance_atr=float(min_distance),
    tp1_r_multiple=float(tp_r),
    pending_expiry_bars=int(expiry),
    pending_invalidation_progress=float(invalidation) / 100,
    divergence_lookback_bars=int(divergence_lookback),
    divergence_rsi_window_bars=int(divergence_rsi_window),
    divergence_min_rsi_delta=float(divergence_min_delta),
)
try:
    with st.spinner("Calculando pivotes, setups y simulaciones causales..."):
        result = EmaPivotStrategyService(config).analyze(intraday.frame)
except ValueError as exc:
    st.error(f"No se pudo calcular el prototipo: {exc}")
    st.stop()

completed = [setup for setup in result.setups if setup.result_r is not None]
winners = [setup for setup in completed if setup.result_r and setup.result_r > 0]
filled = [setup for setup in result.setups if setup.fill_time is not None]
metric_columns = st.columns(6)
metric_columns[0].metric("Velas 5m", f"{len(result.bars):,}")
metric_columns[1].metric("Pivotes confirmados", len(result.pivots))
metric_columns[2].metric("Setups", len(result.setups))
metric_columns[3].metric("Ordenes ejecutadas", len(filled))
metric_columns[4].metric(
    "Win rate cerrado",
    f"{len(winners) / len(completed):.1%}" if completed else "N/A",
)
metric_columns[5].metric(
    "R medio cerrado",
    f"{sum(setup.result_r or 0 for setup in completed) / len(completed):.2f}R"
    if completed
    else "N/A",
)
st.caption(
    f"Mercado `usd_m_futures` · intervalo `{intraday.interval}` · "
    f"{intraday.cached_rows:,} barras en cache · "
    f"{intraday.downloaded_rows:,} descargadas ahora. Horas mostradas en UTC."
)

visible_setups = [
    setup
    for setup in result.setups
    if setup.signal_time >= result.bars["open_time"].max() - pd.Timedelta(days=7)
]
st.plotly_chart(
    _build_chart(result, visible_setups, visible_days=7),
    width="stretch",
    config={"displaylogo": False},
)
st.caption(
    "El grafico muestra los ultimos siete dias para mantenerlo legible. El tamano de cada "
    "marcador de setup aumenta con su score de confirmaciones (0-5)."
)

st.subheader("Registro de setups")
rows = _setup_rows(result.setups)
if rows.empty:
    st.info("No se han detectado setups con los parametros y periodo seleccionados.")
else:
    st.dataframe(
        rows,
        width="stretch",
        hide_index=True,
        column_config={
            "Detalle": st.column_config.LinkColumn(
                "Detalle",
                display_text="Abrir",
                help="Abre el grafico detallado de este setup.",
            ),
            "Entrada limit": st.column_config.NumberColumn(format="%.2f"),
            "Pivote": st.column_config.NumberColumn(format="%.2f"),
            "Stop": st.column_config.NumberColumn(format="%.2f"),
            "TP1": st.column_config.NumberColumn(format="%.2f"),
            "Distancia ATR": st.column_config.NumberColumn(format="%.2f"),
            "Resultado R": st.column_config.NumberColumn(format="%.2f"),
        },
    )

    selected_trade = st.query_params.get("trade")
    if selected_trade:
        selected_setup = next(
            (
                setup
                for setup in result.setups
                if _setup_key(setup) == selected_trade
            ),
            None,
        )
        if selected_setup is None:
            st.warning(
                "El setup solicitado no pertenece al periodo o a los parametros actuales."
            )
        else:
            _render_trade_detail(result, selected_setup)
