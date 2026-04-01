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

toggle_col, status_col = st.columns([2, 3])
override_thresholds = toggle_col.toggle(
    "Override thresholds de entrada",
    value=False,
    help="Si no se activa, el backtest usa la configuracion real actual del sistema.",
)
if override_thresholds:
    status_col.info(
        "Overrides activos: puedes limitar entrada a BUY_CANDIDATE y ajustar thresholds."
    )
else:
    status_col.caption(
        "Usando configuracion base del sistema para entrada. "
        "Activa el override para editar filtros."
    )

with st.form("backtesting_form"):
    col1, col2 = st.columns(2)
    selected_assets = col1.multiselect(
        "Activos",
        options=asset_symbols,
        default=default_assets,
    )
    mode = col2.selectbox(
        "Modo",
        ["trade_by_trade", "portfolio", "portfolio_realistic"],
        help=(
            "trade_by_trade evalua trades aislados; portfolio mantiene una simulacion "
            "basica; portfolio_realistic simula cash, ampliaciones y ventas parciales."
        ),
    )

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
        disabled=not override_thresholds,
    )
    max_risk_score = col6.slider(
        "Max risk score",
        0,
        100,
        int(config["entry_rules"]["max_risk_score"]),
        disabled=not override_thresholds,
    )
    max_rsi14 = col7.slider(
        "Max RSI14",
        0,
        100,
        int(config["entry_rules"]["max_rsi14"]),
        disabled=not override_thresholds,
    )
    allowed_recommendations = st.multiselect(
        "Recomendaciones permitidas para entrada",
        options=["BUY_CANDIDATE", "WATCH", "AVOID"],
        default=config["entry_rules"]["allowed_recommendations"],
        disabled=not override_thresholds,
    )

    col8, col9, col10 = st.columns(3)
    exit_options = [
        "fixed_horizon",
        "take_profit_stop_loss",
        "signal_loss",
        "hybrid",
        "position_alerts",
        "hybrid_position_alerts",
    ]
    exit_strategy = col8.selectbox(
        "Salida",
        exit_options,
        index=exit_options.index(config["exit_rules"]["strategy"]),
    )
    fixed_horizon = col9.selectbox("Horizon dias", [5, 10, 20, 40], index=2)
    require_bullish_trend = col10.checkbox(
        "Exigir SMA50 > SMA200",
        value=config["entry_rules"]["require_bullish_trend"],
        disabled=not override_thresholds,
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
    position_alert_exit_types = st.multiselect(
        "Alertas de posicion como salida",
        options=[
            "take_profit",
            "reduce_risk",
            "exit_candidate",
            "stop_loss_warning",
            "trim_position",
            "rebalance_sell",
            "overbought_warning",
        ],
        default=config["exit_rules"].get("position_alert_exit_types", []),
    )

    st.subheader("Cartera simulada")
    col16, col17, col18 = st.columns(3)
    initial_capital = col16.number_input(
        "Capital inicial (€)",
        min_value=1000.0,
        value=float(config["defaults"]["initial_capital"]),
        step=1000.0,
    )
    cash_min_target_pct = col17.number_input(
        "Cash minimo objetivo (%)",
        min_value=0.0,
        max_value=100.0,
        value=float(config["portfolio_simulation"]["cash_min_target_pct"] * 100),
        step=1.0,
    )
    max_open_positions = col18.number_input(
        "Max posiciones simultaneas",
        min_value=1,
        max_value=50,
        value=int(config["defaults"]["max_open_positions"]),
        step=1,
    )

    col19, col20, col21 = st.columns(3)
    max_asset_weight_pct = col19.number_input(
        "Max peso por activo (%)",
        min_value=1.0,
        max_value=100.0,
        value=float(config["portfolio_simulation"]["max_asset_weight"] * 100),
        step=1.0,
    )
    max_sector_weight_pct = col20.number_input(
        "Max peso por sector (%)",
        min_value=1.0,
        max_value=100.0,
        value=float(config["portfolio_simulation"]["max_sector_weight"] * 100),
        step=1.0,
    )
    allow_add_to_existing = col21.checkbox(
        "Permitir ampliar posiciones ya existentes",
        value=bool(config["portfolio_simulation"]["allow_add_to_existing"]),
    )

    col22, col23, col24 = st.columns(3)
    use_suggested_weight_add = col22.checkbox(
        "Usar suggested_weight_add",
        value=bool(config["portfolio_simulation"]["use_suggested_weight_add"]),
    )
    buy_weight_override_pct = col23.number_input(
        "Buy weight override (%)",
        min_value=0.0,
        max_value=100.0,
        value=float(config["portfolio_simulation"]["buy_weight_override_pct"] or 0.0),
        step=0.5,
        disabled=use_suggested_weight_add,
    )
    apply_portfolio_limits = col24.checkbox(
        "Aplicar limites de cartera",
        value=bool(config["portfolio_simulation"]["apply_portfolio_limits"]),
    )

    st.caption("Porcentaje de venta por tipo de alerta")
    sale_cfg = config["portfolio_simulation"]["sell_reduction_by_alert_type"]
    col25, col26, col27 = st.columns(3)
    sell_take_profit = col25.number_input(
        "TAKE_PROFIT (%)",
        0.0,
        100.0,
        float(sale_cfg["take_profit"] * 100),
        5.0,
    )
    sell_trim = col26.number_input(
        "TRIM_POSITION (%)",
        0.0,
        100.0,
        float(sale_cfg["trim_position"] * 100),
        5.0,
    )
    sell_reduce_risk = col27.number_input(
        "REDUCE_RISK (%)",
        0.0,
        100.0,
        float(sale_cfg["reduce_risk"] * 100),
        5.0,
    )
    col28, col29, col30 = st.columns(3)
    sell_exit = col28.number_input(
        "EXIT_CANDIDATE (%)",
        0.0,
        100.0,
        float(sale_cfg["exit_candidate"] * 100),
        5.0,
    )
    sell_stop = col29.number_input(
        "STOP_LOSS_WARNING (%)",
        0.0,
        100.0,
        float(sale_cfg["stop_loss_warning"] * 100),
        5.0,
    )
    sell_rebalance = col30.number_input(
        "REBALANCE_SELL (%)",
        0.0,
        100.0,
        float(sale_cfg["rebalance_sell"] * 100),
        5.0,
    )
    sell_overbought = st.number_input(
        "OVERBOUGHT_WARNING (%)",
        min_value=0.0,
        max_value=100.0,
        value=float(sale_cfg["overbought_warning"] * 100),
        step=5.0,
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
    entry_overrides=(
        {
            "min_final_score": float(min_final_score),
            "max_risk_score": float(max_risk_score),
            "max_rsi14": float(max_rsi14),
            "require_bullish_trend": require_bullish_trend,
            "allowed_recommendations": tuple(allowed_recommendations),
        }
        if override_thresholds
        else None
    ),
    exit_overrides={
        "strategy": exit_strategy,
        "fixed_horizon_days": fixed_horizon,
        "take_profit_pct": take_profit_pct / 100,
        "stop_loss_pct": stop_loss_pct / 100,
        "signal_loss_score_threshold": float(signal_loss_threshold),
        "max_holding_days": max(fixed_horizon, base_scenario.exit_rules.max_holding_days),
        "position_alert_exit_types": tuple(position_alert_exit_types),
    },
    portfolio_simulation_overrides={
        "cash_min_target_pct": cash_min_target_pct / 100,
        "max_asset_weight": max_asset_weight_pct / 100,
        "max_sector_weight": max_sector_weight_pct / 100,
        "apply_portfolio_limits": apply_portfolio_limits,
        "allow_add_to_existing": allow_add_to_existing,
        "use_suggested_weight_add": use_suggested_weight_add,
        "buy_weight_override_pct": (
            None
            if use_suggested_weight_add or buy_weight_override_pct <= 0
            else buy_weight_override_pct / 100
        ),
        "sell_reduction_by_alert_type": {
            "take_profit": sell_take_profit / 100,
            "trim_position": sell_trim / 100,
            "reduce_risk": sell_reduce_risk / 100,
            "exit_candidate": sell_exit / 100,
            "stop_loss_warning": sell_stop / 100,
            "rebalance_sell": sell_rebalance / 100,
            "overbought_warning": sell_overbought / 100,
        },
    },
)
scenario.initial_capital = float(initial_capital)
scenario.max_open_positions = int(max_open_positions)

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
    cash_df = frames.get("cash", pd.DataFrame())
    portfolio_events_df = frames.get("portfolio_events", pd.DataFrame())

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
        if not cash_df.empty:
            st.plotly_chart(
                px.line(cash_df, x="date", y="cash", title="Cash curve"),
                use_container_width=True,
            )

    if result.portfolio_summary:
        st.subheader("Resumen cartera simulada")
        summary = result.portfolio_summary
        summary_cols = st.columns(6)
        summary_cols[0].metric("Capital inicial", f"{summary['initial_capital']:.0f} €")
        summary_cols[1].metric("Capital final", f"{summary['final_capital']:.0f} €")
        summary_cols[2].metric("Retorno cartera", f"{summary['portfolio_return_pct']:.2f}%")
        summary_cols[3].metric("Cash final", f"{summary['final_cash']:.0f} €")
        summary_cols[4].metric("PnL realizado", f"{summary['realized_pnl']:.0f} €")
        summary_cols[5].metric("PnL no realizado", f"{summary['unrealized_pnl']:.0f} €")
        summary_cols = st.columns(6)
        summary_cols[0].metric("Compras", int(summary["buy_count"]))
        summary_cols[1].metric("Ventas parciales", int(summary["sell_partial_count"]))
        summary_cols[2].metric("Salidas completas", int(summary["sell_full_count"]))
        summary_cols[3].metric("Entrada media", f"{summary['avg_entry_size_pct']:.2f}%")
        summary_cols[4].metric("Reduccion media", f"{summary['avg_reduction_size_pct']:.2f}%")
        summary_cols[5].metric("Concentracion max", f"{summary['max_concentration_pct']:.2f}%")

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
        if not portfolio_events_df.empty:
            st.subheader("Eventos de cartera")
            st.dataframe(portfolio_events_df, use_container_width=True, hide_index=True)

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
