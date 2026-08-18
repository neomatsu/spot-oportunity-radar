from __future__ import annotations

# ruff: noqa: E402
import html
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from backtesting.bitcoin_opportunity import (
    BitcoinOpportunityBacktestConfig,
    BitcoinOpportunityBacktester,
    BitcoinOpportunityBacktestResult,
)
from core.config import load_yaml_config
from data.database import init_db, session_scope
from services.bitcoin_opportunity_service import (
    BitcoinOpportunityReport,
    BitcoinOpportunityService,
)

COLORS = {
    "OPORTUNIDAD_EXCEPCIONAL": "#16a34a",
    "BUENA_OPORTUNIDAD": "#22c55e",
    "NEUTRAL": "#eab308",
    "PRECAUCION": "#f97316",
    "SOBRECALENTADO": "#dc2626",
    "DATOS_INSUFICIENTES": "#64748b",
}

LABELS = {
    "OPORTUNIDAD_EXCEPCIONAL": "Oportunidad excepcional",
    "BUENA_OPORTUNIDAD": "Buena oportunidad",
    "NEUTRAL": "Neutral",
    "PRECAUCION": "Precaucion",
    "SOBRECALENTADO": "Sobrecalentado",
    "DATOS_INSUFICIENTES": "Datos insuficientes",
}


@st.cache_data(ttl=3600, show_spinner=False)
def _load_report() -> BitcoinOpportunityReport:
    init_db()
    with session_scope() as session:
        return BitcoinOpportunityService(session).compute()


@st.cache_data(ttl=300, show_spinner=False)
def _load_history(start_date: date, end_date: date) -> pd.DataFrame:
    init_db()
    with session_scope() as session:
        return BitcoinOpportunityService(session).cached_history(start_date, end_date)


def _update_history(start_date: date, end_date: date) -> pd.DataFrame:
    init_db()
    with session_scope() as session:
        frame = BitcoinOpportunityService(session).update_history(start_date, end_date)
    _load_history.clear()
    return frame


def _score_color(score: float | None) -> str:
    if score is None:
        return "#64748b"
    if score >= 7:
        return "#22c55e"
    if score >= 4.5:
        return "#eab308"
    return "#f97316"


def _gauge(report: BitcoinOpportunityReport) -> go.Figure:
    value = report.score or 0
    color = COLORS[report.classification]
    figure = go.Figure(
        go.Indicator(
            mode="gauge",
            value=value,
            gauge={
                "axis": {"range": [0, 100]},
                "bar": {"color": color, "thickness": 0.28},
                "steps": [
                    {"range": [0, 30], "color": "#fee2e2"},
                    {"range": [30, 45], "color": "#ffedd5"},
                    {"range": [45, 65], "color": "#fef9c3"},
                    {"range": [65, 80], "color": "#dcfce7"},
                    {"range": [80, 100], "color": "#bbf7d0"},
                ],
            },
        )
    )
    figure.update_layout(height=205, margin=dict(t=0, b=0, l=35, r=35))
    return figure


def _component_cards_html(report: BitcoinOpportunityReport) -> str:
    cards: list[str] = []
    for component in report.components:
        score = component.score
        score_text = "N/A" if score is None else f"{score:.1f}"
        score_suffix = "" if score is None else "<span>/10</span>"
        progress = 0 if score is None else max(0, min(100, score * 10))
        color = _score_color(score)
        error_html = (
            f'<div class="btc-card-error">{html.escape(component.error)}</div>'
            if component.error
            else ""
        )
        cards.append(
            f"""
            <article class="btc-component-card">
              <div class="btc-card-label">{html.escape(component.label)}</div>
              <div class="btc-card-score" style="color:{color}">
                {score_text}{score_suffix}
              </div>
              <div class="btc-progress-track" aria-label="Score {score_text}">
                <div class="btc-progress-fill"
                     style="width:{progress:.0f}%;background:{color}"></div>
              </div>
              <div class="btc-card-value">{html.escape(component.value_label)}</div>
              <div class="btc-card-detail">{html.escape(component.detail)}</div>
              {error_html}
            </article>
            """
        )

    return f"""
    <style>
      .btc-component-grid {{
        display: grid;
        grid-template-columns: repeat(3, minmax(0, 1fr));
        gap: 16px;
        width: 100%;
      }}
      .btc-component-card {{
        box-sizing: border-box;
        height: 258px;
        padding: 20px;
        border: 1px solid #d8dee8;
        border-radius: 12px;
        background: linear-gradient(155deg, #ffffff 0%, #f8fafc 100%);
        display: flex;
        flex-direction: column;
        overflow: auto;
      }}
      .btc-card-label {{
        color: #334155;
        font-size: 0.92rem;
        font-weight: 700;
        letter-spacing: 0.01em;
      }}
      .btc-card-score {{
        margin-top: 8px;
        font-size: 2.25rem;
        font-weight: 750;
        line-height: 1.1;
      }}
      .btc-card-score span {{
        margin-left: 2px;
        color: #64748b;
        font-size: 1.1rem;
        font-weight: 500;
      }}
      .btc-progress-track {{
        height: 8px;
        margin: 24px 0 18px;
        border-radius: 999px;
        background: #e8edf4;
        overflow: hidden;
        flex: 0 0 auto;
      }}
      .btc-progress-fill {{
        height: 100%;
        border-radius: inherit;
      }}
      .btc-card-value {{
        color: #475569;
        font-size: 0.86rem;
        font-weight: 600;
      }}
      .btc-card-detail {{
        margin-top: 12px;
        color: #64748b;
        font-size: 0.82rem;
        line-height: 1.45;
      }}
      .btc-card-error {{
        margin-top: auto;
        padding-top: 8px;
        color: #b91c1c;
        font-size: 0.75rem;
      }}
      .btc-global-score {{
        display: flex;
        align-items: baseline;
        justify-content: center;
        margin: 8px 0 -8px;
        font-variant-numeric: tabular-nums;
      }}
      .btc-global-score strong {{
        font-size: clamp(3.2rem, 5vw, 4.7rem);
        font-weight: 760;
        line-height: 1;
      }}
      .btc-global-score span {{
        margin-left: 5px;
        color: #64748b;
        font-size: 1.25rem;
        font-weight: 600;
      }}
      @media (max-width: 1100px) {{
        .btc-component-grid {{ grid-template-columns: repeat(2, minmax(0, 1fr)); }}
      }}
      @media (max-width: 700px) {{
        .btc-component-grid {{ grid-template-columns: 1fr; }}
        .btc-component-card {{ height: auto; min-height: 238px; }}
      }}
    </style>
    <div class="btc-component-grid">{''.join(cards)}</div>
    """


def _component_chart(report: BitcoinOpportunityReport) -> go.Figure:
    available = [component for component in report.components if component.score is not None]
    figure = go.Figure(
        go.Bar(
            x=[component.label for component in available],
            y=[component.score for component in available],
            marker_color=[_score_color(component.score) for component in available],
            text=[f"{component.score:.1f}" for component in available],
            textposition="outside",
        )
    )
    figure.update_layout(
        height=320,
        margin=dict(t=25, b=20),
        yaxis=dict(range=[0, 10.8], title="Score 0-10"),
        xaxis_title=None,
    )
    return figure


def _history_chart(frame: pd.DataFrame) -> go.Figure:
    working = frame.copy()
    working["date"] = pd.to_datetime(working["date"])
    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=working["date"],
            y=working["bitcoin_price"],
            name="Precio BTC",
            yaxis="y2",
            line=dict(color="#94a3b8", width=1.5, dash="dot"),
        )
    )
    figure.add_trace(
        go.Scatter(
            x=working["date"],
            y=working["overall_score"],
            name="Indicador global diario",
            line=dict(color="#2563eb", width=2.2),
            connectgaps=False,
        )
    )
    for value, color, label in (
        (65, "#22c55e", "Buena oportunidad"),
        (80, "#15803d", "Excepcional"),
    ):
        figure.add_hline(
            y=value,
            line_dash="dash",
            line_color=color,
            opacity=0.65,
            annotation_text=label,
            annotation_position="top left",
        )
    figure.update_layout(
        height=480,
        margin=dict(t=30, b=20),
        hovermode="x unified",
        legend=dict(orientation="h", y=1.08),
        yaxis=dict(title="Indicador (0-100)", range=[0, 100]),
        yaxis2=dict(title="Precio BTC", overlaying="y", side="right", showgrid=False),
        xaxis_title=None,
    )
    return figure


def _backtest_chart(result: BitcoinOpportunityBacktestResult) -> go.Figure:
    frame = result.equity_curve
    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=frame["date"],
            y=frame["equity"],
            name="Equity",
            line=dict(color="#2563eb", width=2.2),
        )
    )
    figure.add_trace(
        go.Scatter(
            x=frame["date"],
            y=frame["cash"],
            name="Cash",
            line=dict(color="#16a34a", width=1.5, dash="dot"),
        )
    )
    figure.update_layout(
        height=390,
        margin=dict(t=25, b=20),
        hovermode="x unified",
        yaxis_title="Capital",
        xaxis_title=None,
        legend=dict(orientation="h", y=1.08),
    )
    return figure


def _backtest_trade_chart(result: BitcoinOpportunityBacktestResult) -> go.Figure:
    frame = result.equity_curve.copy()
    figure = go.Figure()
    figure.add_trace(
        go.Scatter(
            x=frame["date"],
            y=frame["bitcoin_price"],
            name="Precio BTC",
            mode="lines",
            line=dict(color="#64748b", width=1.8),
            hovertemplate="%{x|%d/%m/%Y}<br>BTC: %{y:,.0f}<extra></extra>",
        )
    )
    if result.events.empty:
        return figure

    events = result.events.copy()
    events["execution_date"] = pd.to_datetime(events["execution_date"])
    buy_colors = {70.0: "#4ade80", 75.0: "#16a34a", 80.0: "#047857"}
    sell_colors = {25.0: "#f59e0b", 20.0: "#ef4444", 15.0: "#991b1b"}

    for (action, thresholds_text), group in events.groupby(
        ["action", "trigger_thresholds"], sort=False
    ):
        thresholds = tuple(
            float(value.strip()) for value in str(thresholds_text).split(",")
        )
        thresholds_label = "/".join(f"{value:g}" for value in thresholds)
        is_buy = action == "BUY"
        extreme_threshold = max(thresholds) if is_buy else min(thresholds)
        color = (
            buy_colors.get(extreme_threshold, "#16a34a")
            if is_buy
            else sell_colors.get(extreme_threshold, "#dc2626")
        )
        customdata = group[
            ["signal_date", "signal_score", "applied_pct", "gross_value", "fees"]
        ].to_numpy()
        figure.add_trace(
            go.Scatter(
                x=group["execution_date"],
                y=group["price"],
                name=f"{'Compra' if is_buy else 'Venta'} · {thresholds_label}",
                mode="markers",
                marker=dict(
                    symbol="triangle-up" if is_buy else "triangle-down",
                    size=14,
                    color=color,
                    line=dict(color="white", width=1.2),
                ),
                customdata=customdata,
                hovertemplate=(
                    f"<b>{'COMPRA' if is_buy else 'VENTA'} · {thresholds_label}</b>"
                    "<br>Señal: %{customdata[0]}"
                    "<br>Ejecución: %{x|%d/%m/%Y}"
                    "<br>Score: %{customdata[1]:.2f}"
                    "<br>Porcentaje: %{customdata[2]:.1f}%"
                    "<br>Precio: %{y:,.2f}"
                    "<br>Importe: %{customdata[3]:,.2f}"
                    "<br>Costes: %{customdata[4]:,.2f}<extra></extra>"
                ),
            )
        )

    figure.update_layout(
        height=500,
        margin=dict(t=25, b=20),
        hovermode="closest",
        yaxis_title="Precio BTC",
        xaxis_title=None,
        legend=dict(orientation="h", y=1.12),
    )
    return figure


st.set_page_config(page_title="Bitcoin Opportunity", layout="wide")
st.title("Bitcoin Opportunity Detector")
st.caption(
    "Indicador contrarian independiente basado en seis componentes. "
    "No modifica el scoring general, recomendaciones ni alertas."
)

if st.button("Actualizar indicadores", type="primary"):
    _load_report.clear()

with st.spinner("Calculando oportunidad Bitcoin..."):
    report = _load_report()

left, right = st.columns([1, 2])
with left:
    score_display = "N/A" if report.score is None else f"{report.score:.1f}"
    score_color = COLORS[report.classification]
    st.html(
        f"<div class='btc-global-score'><strong style='color:{score_color}'>"
        f"{score_display}</strong><span>/100</span></div>"
    )
    st.plotly_chart(_gauge(report), width="stretch")
    color = COLORS[report.classification]
    st.markdown(
        f"<div style='text-align:center;color:{color};font-size:1.3rem;font-weight:700'>"
        f"{LABELS[report.classification]}</div>",
        unsafe_allow_html=True,
    )
    st.caption(
        f"{report.available_components}/6 componentes disponibles | "
        f"Actualizado {report.updated_at.astimezone().strftime('%d/%m/%Y %H:%M')}"
    )
    if not report.actionable:
        st.warning("No hay suficientes fuentes disponibles para considerar el score accionable.")

with right:
    st.subheader("Componentes")
    st.html(_component_cards_html(report))

st.divider()
st.subheader("Evolución diaria del indicador global vs precio BTC")
st.caption(
    "La línea azul representa el score global reconstruido para cada fecha; "
    "la línea gris discontinua muestra el precio de Bitcoin en el eje derecho."
)
history_controls = st.columns([1, 1, 3])
history_config = load_yaml_config("bitcoin_opportunity.yaml").get("history", {})
with history_controls[0]:
    history_period = st.selectbox(
        "Periodo",
        [1, 2, 3, 5, "since_2018"],
        index=1,
        format_func=lambda value: (
            "Desde 2018" if value == "since_2018" else f"{value} años"
        ),
    )

history_end = report.bitcoin_price_date or date.today()
if history_period == "since_2018":
    history_start = pd.Timestamp(
        history_config.get("indicator_start_date", "2018-01-01")
    ).date()
else:
    history_start = (
        pd.Timestamp(history_end) - pd.DateOffset(years=int(history_period))
    ).date()
with history_controls[1]:
    st.write("")
    refresh_history = st.button("Generar / actualizar histórico", width="stretch")

if refresh_history:
    with st.spinner("Completando histórico y guardándolo en SQLite..."):
        try:
            history_frame = _update_history(history_start, history_end)
            st.success(f"Histórico actualizado: {len(history_frame)} observaciones en caché.")
        except Exception as exc:
            st.error(f"No se pudo actualizar el histórico: {exc}")
            history_frame = _load_history(history_start, history_end)
else:
    history_frame = _load_history(history_start, history_end)

if history_frame.empty:
    st.info(
        "Todavía no hay histórico calculado para este periodo. Usa "
        "'Generar / actualizar histórico'; las siguientes visitas leerán SQLite sin consultar APIs."
    )
else:
    st.plotly_chart(_history_chart(history_frame), width="stretch")
    valid_scores = int(history_frame["overall_score"].notna().sum())
    last_computed = pd.to_datetime(history_frame["computed_at"]).max()
    st.caption(
        f"{valid_scores} días con score | Caché: SQLite | "
        f"Cobertura: {pd.to_datetime(history_frame['date']).min().strftime('%d/%m/%Y')} - "
        f"{pd.to_datetime(history_frame['date']).max().strftime('%d/%m/%Y')} | "
        f"Último cálculo {last_computed.strftime('%d/%m/%Y %H:%M')} | "
        "Cada punto usa únicamente información disponible hasta esa fecha."
    )

with st.expander("Backtesting por umbrales del indicador", expanded=False):
    st.caption(
        "Las compras se activan al cruzar el umbral hacia arriba y usan un porcentaje "
        "del capital inicial. Las ventas se activan al cruzarlo hacia abajo y reducen "
        "un porcentaje de la posición. Todas se ejecutan al precio del día siguiente."
    )
    bt_defaults = load_yaml_config("bitcoin_opportunity.yaml").get("backtesting", {})
    if profile_label := bt_defaults.get("default_profile_label"):
        st.info(
            f"Valores por defecto: {profile_label}. Es una referencia histórica, no una "
            "garantía de rendimiento futuro."
        )
    if history_frame.empty:
        cached_start = history_start
        cached_end = history_end
    else:
        cached_dates = pd.to_datetime(history_frame["date"])
        cached_start = cached_dates.min().date()
        cached_end = cached_dates.max().date()
    with st.form("bitcoin_opportunity_backtest_form"):
        date_start_col, date_end_col = st.columns(2)
        bt_start_date = date_start_col.date_input(
            "Inicio del backtest",
            value=cached_start,
            min_value=cached_start,
            max_value=cached_end,
        )
        bt_end_date = date_end_col.date_input(
            "Fin del backtest",
            value=cached_end,
            min_value=cached_start,
            max_value=cached_end,
        )
        capital_col, cost_col, slip_col = st.columns(3)
        bt_initial_capital = capital_col.number_input(
            "Capital inicial",
            min_value=1_000.0,
            value=float(bt_defaults.get("initial_capital", 100_000)),
            step=1_000.0,
        )
        bt_commission = cost_col.number_input(
            "Comisión (bps)",
            min_value=0.0,
            value=float(bt_defaults.get("commission_bps", 8)),
            step=1.0,
        )
        bt_slippage = slip_col.number_input(
            "Slippage (bps)",
            min_value=0.0,
            value=float(bt_defaults.get("slippage_bps", 5)),
            step=1.0,
        )
        sizing_options = ["available_cash", "initial_capital"]
        sizing_default = str(
            bt_defaults.get("buy_sizing_basis", "initial_capital")
        )
        bt_buy_sizing_basis = st.selectbox(
            "Base para calcular el porcentaje de compra",
            options=sizing_options,
            index=(
                sizing_options.index(sizing_default)
                if sizing_default in sizing_options
                else 1
            ),
            format_func=lambda value: (
                "Cash disponible en ese momento"
                if value == "available_cash"
                else "Capital inicial"
            ),
            help=(
                "Con cash disponible, cada porcentaje se aplica al efectivo existente "
                "justo antes de ejecutar la compra D+1."
            ),
        )

        reset_buy_col, reset_sell_col = st.columns(2)
        buy_reset_threshold = reset_buy_col.number_input(
            "Rearmar compras cuando score <",
            min_value=0.0,
            max_value=100.0,
            value=float(bt_defaults.get("buy_reset_threshold", 60)),
        )
        sell_reset_threshold = reset_sell_col.number_input(
            "Rearmar ventas cuando score >",
            min_value=0.0,
            max_value=100.0,
            value=float(bt_defaults.get("sell_reset_threshold", 40)),
        )

        st.markdown("**Compras escalonadas**")
        buy_threshold_defaults = bt_defaults.get("buy_thresholds", [70, 75, 80])
        buy_pct_defaults = bt_defaults.get("buy_capital_pcts", [0.10, 0.15, 0.25])
        buy_thresholds: list[float] = []
        buy_pcts: list[float] = []
        for index, column in enumerate(st.columns(3)):
            with column:
                buy_thresholds.append(
                    st.number_input(
                        f"Umbral compra {index + 1}",
                        min_value=0.0,
                        max_value=100.0,
                        value=float(buy_threshold_defaults[index]),
                        key=f"btc_buy_threshold_{index}",
                    )
                )
                buy_pcts.append(
                    st.number_input(
                        f"% capital compra {index + 1}",
                        min_value=0.0,
                        max_value=100.0,
                        value=float(buy_pct_defaults[index]) * 100,
                        key=f"btc_buy_pct_{index}",
                    )
                )

        st.markdown("**Ventas escalonadas**")
        sell_threshold_defaults = bt_defaults.get("sell_thresholds", [20, 25, 30])
        sell_pct_defaults = bt_defaults.get("sell_position_pcts", [0.50, 0.30, 0.20])
        sell_thresholds: list[float] = []
        sell_pcts: list[float] = []
        for index, column in enumerate(st.columns(3)):
            with column:
                sell_thresholds.append(
                    st.number_input(
                        f"Umbral venta {index + 1}",
                        min_value=0.0,
                        max_value=100.0,
                        value=float(sell_threshold_defaults[index]),
                        key=f"btc_sell_threshold_{index}",
                    )
                )
                sell_pcts.append(
                    st.number_input(
                        f"% posición venta {index + 1}",
                        min_value=0.0,
                        max_value=100.0,
                        value=float(sell_pct_defaults[index]) * 100,
                        key=f"btc_sell_pct_{index}",
                    )
                )

        candidate_defaults = [
            float(value) * 100
            for value in bt_defaults.get(
                "optimization_pct_candidates", [0.10, 0.20, 0.30, 0.40]
            )
        ]
        optimization_candidates = st.multiselect(
            "% candidatos para optimización",
            options=[5.0, 10.0, 15.0, 20.0, 25.0, 30.0, 40.0, 50.0, 75.0, 100.0],
            default=candidate_defaults,
        )
        run_col, optimize_col = st.columns(2)
        run_backtest = run_col.form_submit_button("Lanzar backtest", width="stretch")
        run_optimization = optimize_col.form_submit_button(
            "Optimizar porcentajes", width="stretch"
        )

    if run_backtest or run_optimization:
        try:
            simulation_config = BitcoinOpportunityBacktestConfig(
                initial_capital=bt_initial_capital,
                buy_sizing_basis=bt_buy_sizing_basis,
                buy_thresholds=tuple(buy_thresholds),  # type: ignore[arg-type]
                buy_capital_pcts=tuple(value / 100 for value in buy_pcts),  # type: ignore[arg-type]
                sell_thresholds=tuple(sell_thresholds),  # type: ignore[arg-type]
                sell_position_pcts=tuple(value / 100 for value in sell_pcts),  # type: ignore[arg-type]
                buy_reset_threshold=buy_reset_threshold,
                sell_reset_threshold=sell_reset_threshold,
                commission_bps=bt_commission,
                slippage_bps=bt_slippage,
                minimum_trade_value=float(bt_defaults.get("minimum_trade_value", 50)),
            )
            backtester = BitcoinOpportunityBacktester()
            if history_frame.empty:
                raise ValueError(
                    "Primero genera el histórico del indicador para el periodo elegido."
                )
            if bt_start_date >= bt_end_date:
                raise ValueError("La fecha de inicio debe ser anterior a la fecha de fin.")
            history_dates = pd.to_datetime(history_frame["date"]).dt.date
            backtest_history = history_frame.loc[
                (history_dates >= bt_start_date) & (history_dates <= bt_end_date)
            ].copy()
            if run_backtest:
                st.session_state["btc_opportunity_backtest"] = backtester.run(
                    backtest_history, simulation_config
                )
            if run_optimization:
                if not optimization_candidates:
                    raise ValueError("Selecciona al menos un porcentaje candidato.")
                with st.spinner("Evaluando combinaciones monotónicas..."):
                    st.session_state["btc_opportunity_optimization"] = (
                        backtester.optimize_percentages(
                            backtest_history,
                            simulation_config,
                            [value / 100 for value in optimization_candidates],
                        )
                    )
        except ValueError as exc:
            st.error(str(exc))

    backtest_result = st.session_state.get("btc_opportunity_backtest")
    if isinstance(backtest_result, BitcoinOpportunityBacktestResult):
        metric_columns = st.columns(6)
        metric_columns[0].metric("Capital final", f"{backtest_result.final_equity:,.0f}")
        metric_columns[1].metric("Retorno", f"{backtest_result.total_return_pct:.2f}%")
        metric_columns[2].metric(
            "Buy & hold", f"{backtest_result.benchmark_return_pct:.2f}%"
        )
        metric_columns[3].metric("Max DD", f"{backtest_result.max_drawdown_pct:.2f}%")
        metric_columns[4].metric("Compras", backtest_result.buy_count)
        metric_columns[5].metric("Ventas", backtest_result.sell_count)
        st.plotly_chart(_backtest_chart(backtest_result), width="stretch")
        st.markdown("**Precio de Bitcoin y ejecuciones de la estrategia**")
        st.caption(
            "Los triángulos verdes indican compras y los descendentes indican ventas. "
            "La leyenda identifica el umbral o los umbrales cruzados; pasa el cursor "
            "para consultar score, porcentaje e importe ejecutado."
        )
        st.plotly_chart(_backtest_trade_chart(backtest_result), width="stretch")
        if backtest_result.events.empty:
            st.info("La configuración no generó operaciones en el periodo.")
        else:
            st.dataframe(backtest_result.events, hide_index=True, width="stretch")

    optimization_result = st.session_state.get("btc_opportunity_optimization")
    if isinstance(optimization_result, pd.DataFrame) and not optimization_result.empty:
        st.markdown("**Mejores combinaciones por retorno ajustado por drawdown**")
        st.dataframe(optimization_result.head(20), hide_index=True, width="stretch")

st.divider()
st.subheader("Comparativa actual de componentes")
st.plotly_chart(_component_chart(report), width="stretch")

with st.expander("Metodologia, fuentes y limitaciones"):
    st.markdown(
        """
        - **Fear & Greed:** lectura contrarian del indice de Alternative.me.
        - **RSI diario:** RSI14 calculado con el historico local de BTCUSDT.
        - **EMA200:** descuento o prima del precio frente a su EMA de 200 sesiones.
        - **Liquidez:** proxy de participacion mediante volumen medio 7d frente a 30d.
        - **DXY:** variacion de 20 sesiones; un dolar debilitandose favorece el score.
        - **Interes publico:** percentil de visitas al articulo Bitcoin en Wikimedia; menor
          atencion recibe mayor puntuacion contrarian.

        El indicador no predice suelos ni sustituye gestion de riesgo. Liquidez e interes
        son proxies, no mediciones institucionales directas. Los pesos y umbrales viven en
        `config/bitcoin_opportunity.yaml`.
        """
    )
    source_rows = [
        {
            "Componente": component.label,
            "Fuente": component.source,
            "Fecha": component.as_of,
            "Estado": "OK" if component.available else "No disponible",
        }
        for component in report.components
    ]
    st.dataframe(pd.DataFrame(source_rows), hide_index=True, width="stretch")
