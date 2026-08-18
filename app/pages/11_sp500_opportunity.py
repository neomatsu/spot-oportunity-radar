from __future__ import annotations

import html
import sys
from dataclasses import asdict
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

from analytics.sp500_forward_returns import SP500ForwardReturnsAnalyzer  # noqa: E402
from backtesting.sp500_opportunity import (  # noqa: E402
    SP500OpportunityBacktestConfig,
    SP500OpportunityBacktester,
    SP500OpportunityBacktestResult,
)
from core.config import load_yaml_config  # noqa: E402
from data.database import init_db, session_scope  # noqa: E402
from services.sp500_opportunity_service import (  # noqa: E402
    SP500OpportunityReport,
    SP500OpportunityService,
)

COLORS = {
    "CAPITULACION_EXTREMA": "#047857",
    "OPORTUNIDAD_EXCEPCIONAL": "#059669",
    "BUENA_OPORTUNIDAD": "#16a34a",
    "OPORTUNIDAD": "#65a30d",
    "NEUTRAL": "#d97706",
    "POCO_ATRACTIVO": "#ea580c",
    "MUY_DESFAVORABLE": "#dc2626",
    "DATOS_INSUFICIENTES": "#64748b",
}

LABELS = {
    "CAPITULACION_EXTREMA": "Capitulación extrema",
    "OPORTUNIDAD_EXCEPCIONAL": "Oportunidad excepcional",
    "BUENA_OPORTUNIDAD": "Buena oportunidad",
    "OPORTUNIDAD": "Oportunidad",
    "NEUTRAL": "Neutral",
    "POCO_ATRACTIVO": "Poco atractivo",
    "MUY_DESFAVORABLE": "Muy desfavorable",
    "DATOS_INSUFICIENTES": "Datos insuficientes",
}


@st.cache_data(ttl=3600, show_spinner=False)
def _load_report() -> SP500OpportunityReport:
    init_db()
    with session_scope() as session:
        return SP500OpportunityService(session).compute()


@st.cache_data(ttl=3600, show_spinner=False)
def _load_history(start_date: date, end_date: date) -> pd.DataFrame:
    init_db()
    with session_scope() as session:
        return SP500OpportunityService(session).cached_history(start_date, end_date)


def _update_history(start_date: date, end_date: date, *, force: bool) -> pd.DataFrame:
    init_db()
    with session_scope() as session:
        result = SP500OpportunityService(session).update_history(
            start_date, end_date, force=force
        )
    _load_history.clear()
    _load_report.clear()
    return result


def _component_cards(report: SP500OpportunityReport) -> str:
    cards = []
    for component in report.components:
        score = component.score
        color = _score_color(score)
        raw = "N/A" if component.raw_value is None else f"{component.raw_value:,.2f}"
        percentile = (
            "N/A"
            if component.percentile is None
            else f"{component.percentile * 100:.1f}"
        )
        warning = "" if component.point_in_time_safe else " · no PIT-safe"
        cards.append(
            f"""
            <div class="spx-card">
              <div class="spx-card-title">{html.escape(component.label)}</div>
              <div class="spx-card-score" style="color:{color}">
                {'N/A' if score is None else f'{score:.1f}'}<span>/10</span>
              </div>
              <div class="spx-progress">
                <i style="width:{0 if score is None else score * 10}%;
                  background:{color}"></i>
              </div>
              <div class="spx-card-raw">Valor: {raw} · Percentil: {percentile}</div>
              <div class="spx-card-detail">{html.escape(component.description)}</div>
              <div class="spx-card-source">{html.escape(component.source + warning)}</div>
            </div>
            """
        )
    return f'<div class="spx-card-grid">{"".join(cards)}</div>'


def _score_color(score: float | None) -> str:
    if score is None:
        return "#64748b"
    if score >= 7:
        return "#22c55e"
    if score >= 4.5:
        return "#eab308"
    return "#f97316"


def _gauge(report: SP500OpportunityReport) -> go.Figure:
    value = report.score or 0.0
    color = COLORS.get(report.classification, "#64748b")
    figure = go.Figure(
        go.Indicator(
            mode="gauge",
            value=value,
            gauge={
                "axis": {"range": [0, 100]},
                "bar": {"color": color, "thickness": 0.28},
                "steps": [
                    {"range": [0, 25], "color": "#fee2e2"},
                    {"range": [25, 45], "color": "#ffedd5"},
                    {"range": [45, 60], "color": "#fef9c3"},
                    {"range": [60, 70], "color": "#ecfccb"},
                    {"range": [70, 80], "color": "#dcfce7"},
                    {"range": [80, 100], "color": "#bbf7d0"},
                ],
            },
        )
    )
    figure.update_layout(height=205, margin=dict(t=0, b=0, l=35, r=35))
    return figure


def _history_chart(history: pd.DataFrame, thresholds: list[tuple[float, str]]) -> go.Figure:
    frame = history.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    figure = make_subplots(specs=[[{"secondary_y": True}]])
    figure.add_trace(
        go.Scatter(
            x=frame["date"],
            y=frame["overall_score"],
            name="Opportunity Score",
            line=dict(color="#2563eb", width=2),
        ),
        secondary_y=False,
    )
    figure.add_trace(
        go.Scatter(
            x=frame["date"],
            y=frame["sp500_price"],
            name="S&P 500",
            line=dict(color="#64748b", width=1.3, dash="dot"),
        ),
        secondary_y=True,
    )
    for value, label in thresholds:
        figure.add_hline(
            y=value,
            line_dash="dash",
            line_color="#16a34a",
            annotation_text=label,
            secondary_y=False,
        )
    figure.update_yaxes(title_text="Opportunity Score", range=[0, 100], secondary_y=False)
    figure.update_yaxes(title_text="S&P 500", secondary_y=True)
    figure.update_layout(height=520, hovermode="x unified", margin=dict(t=25, b=20))
    return figure


def _backtest_chart(result: SP500OpportunityBacktestResult) -> go.Figure:
    figure = go.Figure()
    curve = result.equity_curve
    figure.add_trace(go.Scatter(x=curve["date"], y=curve["equity"], name="Equity"))
    figure.add_trace(
        go.Scatter(
            x=curve["date"],
            y=curve["cash"],
            name="Cash",
            line=dict(color="#16a34a", dash="dot"),
        )
    )
    figure.update_layout(height=430, yaxis_title="Capital", hovermode="x unified")
    return figure


def _base_config(defaults: dict) -> SP500OpportunityBacktestConfig:
    return SP500OpportunityBacktestConfig(
        initial_capital=float(defaults.get("initial_capital", 100_000)),
        buy_sizing_basis=str(
            defaults.get("buy_sizing_basis", "initial_capital")
        ),
        buy_thresholds=tuple(float(value) for value in defaults["buy_thresholds"]),
        buy_capital_pcts=tuple(float(value) for value in defaults["buy_capital_pcts"]),
        sell_thresholds=tuple(float(value) for value in defaults["sell_thresholds"]),
        sell_position_pcts=tuple(float(value) for value in defaults["sell_position_pcts"]),
        buy_reset_threshold=float(defaults.get("buy_reset_threshold", 55)),
        sell_reset_threshold=float(defaults.get("sell_reset_threshold", 55)),
        commission_bps=float(defaults.get("commission_bps", 8)),
        slippage_bps=float(defaults.get("slippage_bps", 5)),
        minimum_trade_value=float(defaults.get("minimum_trade_value", 50)),
    )


st.set_page_config(page_title="S&P 500 Opportunity", layout="wide")
st.title("S&P 500 Opportunity Detector")
st.caption(
    "Indicador contrarian independiente para acumulación de medio/largo plazo. "
    "No modifica el scoring general, recomendaciones, alertas ni Bitcoin Opportunity."
)
st.html(
    """
    <style>
    .spx-card-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));
      gap:16px;width:100%}
    .spx-card{box-sizing:border-box;border:1px solid #d8dee8;border-radius:12px;
      padding:20px;min-height:285px;background:linear-gradient(155deg,#fff 0%,#f8fafc 100%);
      display:flex;flex-direction:column;overflow:visible}
    .spx-card-title{font-weight:700;font-size:.92rem;color:#334155;
      letter-spacing:.01em}
    .spx-card-score{font-size:2.25rem;font-weight:750;margin-top:8px;line-height:1.1}
    .spx-card-score span{margin-left:2px;font-size:1.1rem;font-weight:500;
      color:#64748b}
    .spx-progress{height:8px;margin:24px 0 18px;border-radius:999px;
      background:#e8edf4;overflow:hidden;flex:0 0 auto}
    .spx-progress i{display:block;height:100%;border-radius:9px}
    .spx-card-raw{color:#475569;font-size:.86rem;font-weight:600}
    .spx-card-detail{margin-top:12px;color:#64748b;font-size:.82rem;line-height:1.45}
    .spx-card-source{margin-top:auto;padding-top:10px;font-size:.75rem;color:#94a3b8}
    .spx-global-score{display:flex;align-items:baseline;justify-content:center;
      margin:8px 0 -8px;font-variant-numeric:tabular-nums}
    .spx-global-score strong{font-size:clamp(3.2rem,5vw,4.7rem);
      font-weight:760;line-height:1}
    .spx-global-score span{margin-left:5px;color:#64748b;font-size:1.25rem;
      font-weight:600}
    @media(max-width:1500px){.spx-card-grid{grid-template-columns:repeat(2,minmax(0,1fr))}}
    @media(max-width:700px){.spx-card-grid{grid-template-columns:1fr}
      .spx-card{min-height:250px}}
    </style>
    """
)

refresh_col, force_col = st.columns([1.4, 4])
refresh_current = refresh_col.button("Actualizar indicador", type="primary")
force_refresh = force_col.checkbox("Forzar descarga de fuentes", value=False)
if refresh_current:
    config = load_yaml_config("sp500_opportunity.yaml")
    start = pd.Timestamp(config["history"]["default_start_date"]).date()
    with st.spinner("Actualizando histórico y score..."):
        try:
            _update_history(start, date.today(), force=force_refresh)
            st.success("Indicador actualizado.")
        except Exception as exc:
            st.error(f"No se pudo actualizar: {exc}")

with st.spinner("Leyendo indicador desde SQLite..."):
    report = _load_report()

score_col, component_col = st.columns([1, 2])
with score_col:
    color = COLORS.get(report.classification, "#64748b")
    score_display = "N/A" if report.score is None else f"{report.score:.1f}"
    st.html(
        f"<div class='spx-global-score'><strong style='color:{color}'>"
        f"{score_display}</strong><span>/100</span></div>"
    )
    st.plotly_chart(_gauge(report), width="stretch")
    st.markdown(
        f"<div style='text-align:center;color:{color};font-size:1.3rem;font-weight:700'>"
        f"{LABELS.get(report.classification, report.classification)}</div>",
        unsafe_allow_html=True,
    )
    price_label = (
        "N/A" if report.sp500_price is None else f"{report.sp500_price:,.2f}"
    )
    st.markdown(
        f"<div style='text-align:center;color:#475569;margin-top:.45rem'>"
        f"S&P 500: <strong>{price_label}</strong></div>",
        unsafe_allow_html=True,
    )
    st.markdown(
        f"<div style='text-align:center;color:#64748b;font-size:.88rem;margin-top:.45rem'>"
        f"{report.available_components}/6 componentes disponibles | "
        f"Actualizado {report.price_date or 'N/A'}</div>",
        unsafe_allow_html=True,
    )
    if not report.actionable:
        st.warning("No hay suficientes componentes para considerar el score accionable.")
with component_col:
    st.subheader("Componentes")
    st.html(_component_cards(report))

config = load_yaml_config("sp500_opportunity.yaml")
history_cfg = config["history"]
st.divider()
st.subheader("Histórico diario")
history_controls = st.columns([1, 1, 1, 2])
history_start = history_controls[0].date_input(
    "Inicio",
    value=pd.Timestamp(history_cfg["default_start_date"]).date(),
    min_value=pd.Timestamp(history_cfg["maximum_start_date"]).date(),
    max_value=date.today(),
)
history_end = history_controls[1].date_input(
    "Fin", value=date.today(), min_value=history_start, max_value=date.today()
)
generate_history = history_controls[2].button("Reconstruir", width="stretch")
history_controls[3].caption(
    "La lectura habitual usa SQLite. Reconstruir consulta únicamente las fuentes que falten."
)
if generate_history:
    with st.spinner("Reconstruyendo score por sesión..."):
        try:
            history = _update_history(history_start, history_end, force=force_refresh)
            st.success(f"Histórico listo: {len(history):,} sesiones.")
        except Exception as exc:
            st.error(str(exc))
            history = _load_history(history_start, history_end)
else:
    history = _load_history(history_start, history_end)

if history.empty:
    st.info("No hay histórico en SQLite para el período. Pulsa Reconstruir.")
else:
    chart_thresholds = [
        (float(row["score"]), str(row["label"]))
        for row in config.get("visualization", {}).get("thresholds", [])
    ]
    st.plotly_chart(
        _history_chart(history, chart_thresholds),
        width="stretch",
    )
    unsafe_count = history["data_quality_json"].map(
        lambda value: bool((value or {}).get("point_in_time_unsafe_keys"))
    ).sum()
    st.caption(
        f"Cobertura {pd.to_datetime(history['date']).min().date()} - "
        f"{pd.to_datetime(history['date']).max().date()} · "
        f"{history['overall_score'].notna().sum():,} scores válidos · "
        f"{unsafe_count:,} sesiones incluyen componentes no PIT-safe identificados."
    )

with st.expander("Forward Returns Analysis", expanded=False):
    st.caption(
        "Evalúa si scores mayores ordenan mejores retornos futuros. Las observaciones "
        "solapadas no son estadísticamente independientes."
    )
    run_forward = st.button("Calcular retornos futuros", disabled=history.empty)
    if run_forward:
        forward_cfg = config["forward_returns"]
        with st.spinner("Calculando horizontes y MAE..."):
            result = SP500ForwardReturnsAnalyzer().analyze(
                history,
                horizons={
                    key: int(value)
                    for key, value in forward_cfg["horizons_sessions"].items()
                },
                score_bands=[float(value) for value in forward_cfg["score_bands"]],
            )
            st.session_state["sp500_forward_returns"] = result
    forward_result = st.session_state.get("sp500_forward_returns")
    if forward_result is not None:
        st.dataframe(forward_result.summary, hide_index=True, width="stretch")
        st.markdown("**Monotonicidad global**")
        st.dataframe(forward_result.monotonicity, hide_index=True, width="stretch")

with st.expander("Backtesting D+1 y validación temporal", expanded=False):
    defaults = config["backtesting"]
    profile_label = defaults.get("default_profile_label")
    profile_id = defaults.get("default_profile_config_id")
    if profile_label:
        suffix = f" · `{profile_id}`" if profile_id else ""
        st.caption(f"Valores por defecto: {profile_label}{suffix}")
    with st.form("sp500_opportunity_backtest"):
        date_cols = st.columns(3)
        bt_start = date_cols[0].date_input("Inicio backtest", value=history_start)
        bt_end = date_cols[1].date_input("Fin backtest", value=history_end)
        split_date = date_cols[2].date_input(
            "Split train/test",
            value=pd.Timestamp(defaults["validation_split_date"]).date(),
        )
        capital_cols = st.columns(3)
        capital = capital_cols[0].number_input(
            "Capital inicial", min_value=1000.0, value=float(defaults["initial_capital"])
        )
        commission = capital_cols[1].number_input(
            "Comisión (bps)", min_value=0.0, value=float(defaults["commission_bps"])
        )
        slippage = capital_cols[2].number_input(
            "Slippage (bps)", min_value=0.0, value=float(defaults["slippage_bps"])
        )
        sizing_options = ["available_cash", "initial_capital"]
        sizing_default = str(defaults.get("buy_sizing_basis", "initial_capital"))
        buy_sizing_basis = st.selectbox(
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
        st.markdown("**Compras escalonadas**")
        buy_thresholds = []
        buy_pcts = []
        for index, column in enumerate(st.columns(3)):
            buy_thresholds.append(
                column.number_input(
                    f"Umbral compra {index + 1}",
                    value=float(defaults["buy_thresholds"][index]),
                )
            )
            buy_pcts.append(
                column.number_input(
                    f"% capital {index + 1}",
                    value=float(defaults["buy_capital_pcts"][index]) * 100,
                )
            )
        st.markdown("**Ventas escalonadas**")
        sell_thresholds = []
        sell_pcts = []
        for index, column in enumerate(st.columns(3)):
            sell_thresholds.append(
                column.number_input(
                    f"Umbral venta {index + 1}",
                    value=float(defaults["sell_thresholds"][index]),
                )
            )
            sell_pcts.append(
                column.number_input(
                    f"% posición {index + 1}",
                    value=float(defaults["sell_position_pcts"][index]) * 100,
                )
            )
        action_cols = st.columns(3)
        run_backtest = action_cols[0].form_submit_button("Lanzar backtest", width="stretch")
        run_sensitivity = action_cols[1].form_submit_button(
            "Sensibilidad", width="stretch"
        )
        run_oos = action_cols[2].form_submit_button("Train / Test", width="stretch")

    if run_backtest or run_sensitivity or run_oos:
        try:
            dates = pd.to_datetime(history["date"]).dt.date
            selected = history[(dates >= bt_start) & (dates <= bt_end)].copy()
            simulation = SP500OpportunityBacktestConfig(
                initial_capital=capital,
                buy_sizing_basis=buy_sizing_basis,
                buy_thresholds=tuple(buy_thresholds),
                buy_capital_pcts=tuple(value / 100 for value in buy_pcts),
                sell_thresholds=tuple(sell_thresholds),
                sell_position_pcts=tuple(value / 100 for value in sell_pcts),
                buy_reset_threshold=float(defaults["buy_reset_threshold"]),
                sell_reset_threshold=float(defaults["sell_reset_threshold"]),
                commission_bps=commission,
                slippage_bps=slippage,
                minimum_trade_value=float(defaults["minimum_trade_value"]),
            )
            backtester = SP500OpportunityBacktester()
            if run_backtest:
                st.session_state["sp500_backtest"] = backtester.run(selected, simulation)
            if run_sensitivity or run_oos:
                offsets = [float(value) for value in defaults["sensitivity_threshold_offsets"]]
                sensitivity = backtester.sensitivity_analysis(
                    selected, simulation, threshold_offsets=offsets
                )
                st.session_state["sp500_sensitivity"] = sensitivity
                if run_oos:
                    configs = [simulation]
                    for offset in offsets:
                        if offset == 0:
                            continue
                        configs.append(
                            SP500OpportunityBacktestConfig(
                                **{
                                    **asdict(simulation),
                                    "buy_thresholds": tuple(
                                        value + offset for value in simulation.buy_thresholds
                                    ),
                                    "sell_thresholds": tuple(
                                        value + offset for value in simulation.sell_thresholds
                                    ),
                                }
                            )
                        )
                    st.session_state["sp500_oos"] = backtester.in_sample_out_of_sample(
                        selected, configs, split_date=split_date
                    )
        except ValueError as exc:
            st.error(str(exc))

    backtest_result = st.session_state.get("sp500_backtest")
    if isinstance(backtest_result, SP500OpportunityBacktestResult):
        metrics = st.columns(6)
        metrics[0].metric("Retorno", f"{backtest_result.total_return_pct:.2f}%")
        metrics[1].metric("CAGR", f"{backtest_result.cagr_pct:.2f}%")
        metrics[2].metric("Max DD", f"{backtest_result.max_drawdown_pct:.2f}%")
        metrics[3].metric("Sharpe", f"{backtest_result.sharpe:.2f}")
        metrics[4].metric("Sortino", f"{backtest_result.sortino:.2f}")
        metrics[5].metric("Calmar", f"{backtest_result.calmar:.2f}")
        more = st.columns(6)
        more[0].metric("Buy & Hold", f"{backtest_result.benchmark_return_pct:.2f}%")
        more[1].metric("Compras", backtest_result.buy_count)
        more[2].metric("Ventas", backtest_result.sell_count)
        more[3].metric("Exposición media", f"{backtest_result.average_exposure_pct:.1f}%")
        more[4].metric("Tiempo en cash", f"{backtest_result.time_fully_in_cash_pct:.1f}%")
        more[5].metric("Turnover", f"{backtest_result.turnover_pct:.1f}%")
        st.plotly_chart(_backtest_chart(backtest_result), width="stretch")
        st.dataframe(backtest_result.events, hide_index=True, width="stretch")
    sensitivity = st.session_state.get("sp500_sensitivity")
    if isinstance(sensitivity, pd.DataFrame) and not sensitivity.empty:
        st.markdown("**Meseta de sensibilidad (Top 20, no solo ganador)**")
        st.dataframe(sensitivity.head(20), hide_index=True, width="stretch")
    oos = st.session_state.get("sp500_oos")
    if isinstance(oos, pd.DataFrame) and not oos.empty:
        st.markdown("**In-sample / Out-of-sample**")
        st.dataframe(oos, hide_index=True, width="stretch")

with st.expander("Fuentes, sesgos y limitaciones"):
    source_rows = [
        {
            "Componente": component.label,
            "Fuente": component.source,
            "Disponible": component.available,
            "PIT-safe": component.point_in_time_safe,
            "Fecha": component.as_of,
            "Error": component.error,
        }
        for component in report.components
    ]
    st.dataframe(pd.DataFrame(source_rows), hide_index=True, width="stretch")
    st.markdown(
        """
        - La señal del cierre de **D se ejecuta en D+1** en el backtest.
        - CAPE aplica un lag configurable, pero la serie histórica puede contener revisiones;
          se marca como no point-in-time-safe.
        - Breadth usa los componentes actuales disponibles en caché: introduce
          **survivorship bias** y se identifica explícitamente.
        - DFF se desplaza un día para respetar disponibilidad; no se sustituye una fuente
          ausente por score neutral.
        - El benchmark es el índice de precios S&P 500, sin dividendos. Los retornos futuros
          solapados no son observaciones independientes.
        """
    )
