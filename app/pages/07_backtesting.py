from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st

from backtesting.engine import BacktestEngine  # noqa: E402
from backtesting.optimizer import BacktestOptimizer  # noqa: E402
from backtesting.reporting import optimization_results_frame, run_result_to_frames  # noqa: E402
from backtesting.scenarios import (  # noqa: E402
    default_backtest_scenario,
    load_backtesting_config,
    scenario_with_overrides,
)
from data.database import init_db, session_scope  # noqa: E402
from data.repositories.assets_repo import AssetsRepository  # noqa: E402

st.title("Backtesting")
st.caption(
    "Backtesting trade-by-trade y simulacion de cartera basica, con reglas auditables, "
    "persistencia de resultados y optimizacion por grid search."
)

init_db()
config = load_backtesting_config()

with session_scope() as session:
    assets = AssetsRepository(session).list_enabled()

asset_symbols = [asset.symbol for asset in assets]
default_assets = asset_symbols[: min(8, len(asset_symbols))]

with st.form("backtesting_form"):
    col1, col2 = st.columns(2)
    selected_assets = col1.multiselect(
        "Activos",
        options=asset_symbols,
        default=default_assets,
    )
    mode = col2.selectbox("Modo", ["trade_by_trade", "portfolio"])

    col3, col4 = st.columns(2)
    start_date = col3.date_input(
        "Inicio",
        value=pd.Timestamp.today().date() - pd.Timedelta(days=365 * 2),
    )
    end_date = col4.date_input("Fin", value=pd.Timestamp.today().date())

    col5, col6, col7 = st.columns(3)
    min_final_score = col5.slider(
        "Min final score",
        0,
        100,
        int(config["entry_rules"]["min_final_score"]),
    )
    max_risk_score = col6.slider(
        "Max risk score",
        0,
        100,
        int(config["entry_rules"]["max_risk_score"]),
    )
    max_rsi14 = col7.slider("Max RSI14", 0, 100, int(config["entry_rules"]["max_rsi14"]))

    col8, col9, col10 = st.columns(3)
    exit_strategy = col8.selectbox(
        "Salida",
        ["fixed_horizon", "take_profit_stop_loss", "signal_loss", "hybrid"],
        index=["fixed_horizon", "take_profit_stop_loss", "signal_loss", "hybrid"].index(
            config["exit_rules"]["strategy"]
        ),
    )
    fixed_horizon = col9.selectbox("Horizon dias", [5, 10, 20, 40], index=2)
    require_bullish_trend = col10.checkbox(
        "Exigir SMA50 > SMA200",
        value=config["entry_rules"]["require_bullish_trend"],
    )

    col11, col12, col13 = st.columns(3)
    take_profit_pct = col11.number_input(
        "Take profit %",
        min_value=1.0,
        max_value=50.0,
        value=float(config["exit_rules"]["take_profit_pct"] * 100),
    )
    stop_loss_pct = col12.number_input(
        "Stop loss %",
        min_value=1.0,
        max_value=50.0,
        value=float(config["exit_rules"]["stop_loss_pct"] * 100),
    )
    signal_loss_threshold = col13.number_input(
        "Score perdida señal",
        min_value=0.0,
        max_value=100.0,
        value=float(config["exit_rules"]["signal_loss_score_threshold"]),
    )

    col14, col15 = st.columns(2)
    run_backtest = col14.form_submit_button("Lanzar backtest", use_container_width=True)
    run_optimization = col15.form_submit_button("Lanzar optimizacion", use_container_width=True)

if not selected_assets:
    st.info("Selecciona al menos un activo para ejecutar backtesting.")
    st.stop()

base_scenario = default_backtest_scenario(
    assets=selected_assets,
    start_date=start_date,
    end_date=end_date,
)
scenario = scenario_with_overrides(
    base_scenario,
    mode=base_scenario.mode.__class__(mode),
    entry_overrides={
        "min_final_score": float(min_final_score),
        "max_risk_score": float(max_risk_score),
        "max_rsi14": float(max_rsi14),
        "require_bullish_trend": require_bullish_trend,
    },
    exit_overrides={
        "strategy": exit_strategy,
        "fixed_horizon_days": fixed_horizon,
        "take_profit_pct": take_profit_pct / 100,
        "stop_loss_pct": stop_loss_pct / 100,
        "signal_loss_score_threshold": float(signal_loss_threshold),
        "max_holding_days": max(fixed_horizon, base_scenario.exit_rules.max_holding_days),
    },
)

if run_backtest:
    with st.spinner("Ejecutando backtest historico sin look-ahead..."):
        with session_scope() as session:
            result = BacktestEngine(session).run(scenario)
    frames = run_result_to_frames(result)

    metrics_cols = st.columns(6)
    metrics_cols[0].metric("Trades", result.metrics.total_trades)
    metrics_cols[1].metric("Win rate", f"{result.metrics.win_rate_pct:.1f}%")
    metrics_cols[2].metric("Expectancy", f"{result.metrics.expectancy_pct:.2f}%")
    metrics_cols[3].metric("Profit factor", f"{result.metrics.profit_factor:.2f}")
    metrics_cols[4].metric("Max DD", f"{result.metrics.max_drawdown_pct:.2f}%")
    metrics_cols[5].metric("Return/DD", f"{result.metrics.return_to_drawdown:.2f}")

    for warning in result.warnings:
        st.warning(warning)

    trades_df = frames["trades"]
    equity_df = frames["equity"]

    if not equity_df.empty:
        st.plotly_chart(
            px.line(equity_df, x="date", y="equity", title="Equity curve"),
            use_container_width=True,
        )
        equity_df["rolling_peak"] = equity_df["equity"].cummax()
        equity_df["drawdown_pct"] = ((equity_df["equity"] / equity_df["rolling_peak"]) - 1) * 100
        st.plotly_chart(
            px.area(equity_df, x="date", y="drawdown_pct", title="Drawdown chart"),
            use_container_width=True,
        )

    if not trades_df.empty:
        st.plotly_chart(
            px.histogram(trades_df, x="net_return_pct", nbins=30, title="Distribucion de retornos"),
            use_container_width=True,
        )
        score_chart = px.scatter(
            trades_df,
            x="final_score",
            y="net_return_pct",
            color="recommendation",
            hover_name="symbol",
            title="Final score vs retorno neto",
        )
        st.plotly_chart(score_chart, use_container_width=True)

        st.subheader("Top trades")
        st.dataframe(
            trades_df.sort_values("net_return_pct", ascending=False).head(10),
            use_container_width=True,
            hide_index=True,
        )
        st.subheader("Bottom trades")
        st.dataframe(
            trades_df.sort_values("net_return_pct", ascending=True).head(10),
            use_container_width=True,
            hide_index=True,
        )

        segment_choice = st.selectbox(
            "Segmentacion",
            ["symbol", "sector", "asset_type", "recommendation", "score_band", "risk_band"],
        )
        segment_df = frames.get(f"segment_{segment_choice}", pd.DataFrame())
        if not segment_df.empty:
            st.dataframe(segment_df, use_container_width=True, hide_index=True)
            st.plotly_chart(
                px.bar(
                    segment_df,
                    x="segment",
                    y="expectancy_pct",
                    title=f"Expectancy por {segment_choice}",
                ),
                use_container_width=True,
            )

        st.subheader("Detalle de operaciones")
        st.dataframe(trades_df, use_container_width=True, hide_index=True)

if run_optimization:
    with st.spinner("Ejecutando grid search de parametros..."):
        with session_scope() as session:
            results = BacktestOptimizer(session).run_grid_search(scenario)
    optimization_df = optimization_results_frame(results)
    if optimization_df.empty:
        st.info("No se han generado resultados de optimizacion.")
    else:
        st.subheader("Ranking de combinaciones")
        st.dataframe(optimization_df, use_container_width=True, hide_index=True)

        top_df = optimization_df.head(15)
        fig = go.Figure(
            data=[
                go.Bar(
                    x=top_df["parameter_set_id"].astype(str),
                    y=top_df["evaluation_score"],
                    text=top_df["warning"],
                )
            ]
        )
        fig.update_layout(title="Mejores combinaciones por evaluation score")
        st.plotly_chart(fig, use_container_width=True)
