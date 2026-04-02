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
from backtesting.models import BacktestMode  # noqa: E402
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
mode_options = [mode.value for mode in BacktestMode]

mode_col, help_col = st.columns([2, 3])
mode = mode_col.selectbox(
    "Modo",
    mode_options,
    index=mode_options.index(config["defaults"]["mode"]),
    help=(
        "trade_by_trade evalua trades aislados; portfolio mantiene una simulacion "
        "basica; portfolio_realistic simula cash, ampliaciones y ventas parciales; "
        "rsi_cycle_strategy usa solo ciclos RSI y divergencias confirmadas."
    ),
)
is_rsi_mode = mode == BacktestMode.RSI_CYCLE_STRATEGY.value

if is_rsi_mode:
    help_col.info(
        "Modo RSI-only: compras y ventas acumulativas por ciclos de sobreventa/sobrecompra "
        "y divergencias confirmadas. No usa stop loss ni las salidas clasicas."
    )
else:
    help_col.caption(
        "Modo general: usa la logica habitual de senales, filtros de entrada y salidas "
        "clasicas o por alertas de posicion."
    )

override_thresholds = False
if not is_rsi_mode:
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
    start_date = col2.date_input(
        "Inicio",
        value=pd.Timestamp.today().date() - pd.Timedelta(days=365 * 2),
    )
    end_date = st.date_input("Fin", value=pd.Timestamp.today().date())

    allowed_recommendations: list[str] = config["entry_rules"]["allowed_recommendations"]
    min_final_score = float(config["entry_rules"]["min_final_score"])
    max_risk_score = float(config["entry_rules"]["max_risk_score"])
    max_rsi14 = float(config["entry_rules"]["max_rsi14"])
    require_bullish_trend = bool(config["entry_rules"]["require_bullish_trend"])
    exit_strategy = config["exit_rules"]["strategy"]
    fixed_horizon = int(config["exit_rules"]["fixed_horizon_days"])
    take_profit_pct = float(config["exit_rules"]["take_profit_pct"] * 100)
    stop_loss_pct = float(config["exit_rules"]["stop_loss_pct"] * 100)
    signal_loss_threshold = float(config["exit_rules"]["signal_loss_score_threshold"])
    position_alert_exit_types = list(config["exit_rules"].get("position_alert_exit_types", []))

    if not is_rsi_mode:
        col3, col4, col5 = st.columns(3)
        min_final_score = col3.slider(
            "Min final score",
            0,
            100,
            int(config["entry_rules"]["min_final_score"]),
            disabled=not override_thresholds,
        )
        max_risk_score = col4.slider(
            "Max risk score",
            0,
            100,
            int(config["entry_rules"]["max_risk_score"]),
            disabled=not override_thresholds,
        )
        max_rsi14 = col5.slider(
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

        col6, col7, col8 = st.columns(3)
        exit_options = [
            "fixed_horizon",
            "take_profit_stop_loss",
            "signal_loss",
            "hybrid",
            "position_alerts",
            "hybrid_position_alerts",
        ]
        exit_strategy = col6.selectbox(
            "Salida",
            exit_options,
            index=exit_options.index(config["exit_rules"]["strategy"]),
        )
        fixed_horizon = col7.selectbox("Horizon dias", [5, 10, 20, 40], index=2)
        require_bullish_trend = col8.checkbox(
            "Exigir SMA50 > SMA200",
            value=config["entry_rules"]["require_bullish_trend"],
            disabled=not override_thresholds,
        )

        col9, col10, col11 = st.columns(3)
        take_profit_pct = col9.number_input(
            "Take profit %",
            min_value=1.0,
            max_value=50.0,
            value=float(config["exit_rules"]["take_profit_pct"] * 100),
        )
        stop_loss_pct = col10.number_input(
            "Stop loss %",
            min_value=1.0,
            max_value=50.0,
            value=float(config["exit_rules"]["stop_loss_pct"] * 100),
        )
        signal_loss_threshold = col11.number_input(
            "Score perdida senal",
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
    col12, col13, col14 = st.columns(3)
    initial_capital = col12.number_input(
        "Capital inicial (EUR)",
        min_value=1000.0,
        value=float(config["defaults"]["initial_capital"]),
        step=1000.0,
    )
    cash_min_target_pct = col13.number_input(
        "Cash minimo objetivo (%)",
        min_value=0.0,
        max_value=100.0,
        value=float(config["portfolio_simulation"]["cash_min_target_pct"] * 100),
        step=1.0,
    )
    max_open_positions = col14.number_input(
        "Max posiciones simultaneas",
        min_value=1,
        max_value=50,
        value=int(config["defaults"]["max_open_positions"]),
        step=1,
    )

    col15, col16, col17 = st.columns(3)
    max_asset_weight_pct = col15.number_input(
        "Max peso por activo (%)",
        min_value=1.0,
        max_value=100.0,
        value=float(config["portfolio_simulation"]["max_asset_weight"] * 100),
        step=1.0,
    )
    max_sector_weight_pct = col16.number_input(
        "Max peso por sector (%)",
        min_value=1.0,
        max_value=100.0,
        value=float(config["portfolio_simulation"]["max_sector_weight"] * 100),
        step=1.0,
    )
    allow_add_to_existing = col17.checkbox(
        "Permitir ampliar posiciones ya existentes",
        value=bool(config["portfolio_simulation"]["allow_add_to_existing"]),
    )

    use_suggested_weight_add = True
    buy_weight_override_pct = 0.0
    apply_portfolio_limits = True
    sale_cfg = config["portfolio_simulation"]["sell_reduction_by_alert_type"]
    sell_take_profit = float(sale_cfg["take_profit"] * 100)
    sell_trim = float(sale_cfg["trim_position"] * 100)
    sell_reduce_risk = float(sale_cfg["reduce_risk"] * 100)
    sell_exit = float(sale_cfg["exit_candidate"] * 100)
    sell_stop = float(sale_cfg["stop_loss_warning"] * 100)
    sell_rebalance = float(sale_cfg["rebalance_sell"] * 100)
    sell_overbought = float(sale_cfg["overbought_warning"] * 100)

    if not is_rsi_mode:
        col18, col19, col20 = st.columns(3)
        use_suggested_weight_add = col18.checkbox(
            "Usar suggested_weight_add",
            value=bool(config["portfolio_simulation"]["use_suggested_weight_add"]),
        )
        buy_weight_override_pct = col19.number_input(
            "Buy weight override (%)",
            min_value=0.0,
            max_value=100.0,
            value=float(config["portfolio_simulation"]["buy_weight_override_pct"] or 0.0),
            step=0.5,
            disabled=use_suggested_weight_add,
        )
        apply_portfolio_limits = col20.checkbox(
            "Aplicar limites de cartera",
            value=bool(config["portfolio_simulation"]["apply_portfolio_limits"]),
        )

        st.caption("Porcentaje de venta por tipo de alerta")
        col21, col22, col23 = st.columns(3)
        sell_take_profit = col21.number_input(
            "TAKE_PROFIT (%)",
            0.0,
            100.0,
            float(sale_cfg["take_profit"] * 100),
            5.0,
        )
        sell_trim = col22.number_input(
            "TRIM_POSITION (%)",
            0.0,
            100.0,
            float(sale_cfg["trim_position"] * 100),
            5.0,
        )
        sell_reduce_risk = col23.number_input(
            "REDUCE_RISK (%)",
            0.0,
            100.0,
            float(sale_cfg["reduce_risk"] * 100),
            5.0,
        )
        col24, col25, col26 = st.columns(3)
        sell_exit = col24.number_input(
            "EXIT_CANDIDATE (%)",
            0.0,
            100.0,
            float(sale_cfg["exit_candidate"] * 100),
            5.0,
        )
        sell_stop = col25.number_input(
            "STOP_LOSS_WARNING (%)",
            0.0,
            100.0,
            float(sale_cfg["stop_loss_warning"] * 100),
            5.0,
        )
        sell_rebalance = col26.number_input(
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

    rsi_cfg = config.get("rsi_cycle_strategy", {})
    oversold_threshold = float(rsi_cfg.get("oversold_threshold", 30))
    deep_oversold_1 = float(rsi_cfg.get("deep_oversold_threshold_1", 25))
    deep_oversold_2 = float(rsi_cfg.get("deep_oversold_threshold_2", 20))
    overbought_threshold = float(rsi_cfg.get("overbought_threshold", 70))
    overbought_1 = float(rsi_cfg.get("overbought_threshold_1", 75))
    overbought_2 = float(rsi_cfg.get("overbought_threshold_2", 80))
    buy_pct_div = float(rsi_cfg.get("buy_pct_bullish_divergence", 0.35) * 100)
    buy_pct_25 = float(rsi_cfg.get("buy_pct_rsi_25", 0.25) * 100)
    buy_pct_20 = float(rsi_cfg.get("buy_pct_rsi_20", 0.40) * 100)
    sell_pct_div = float(rsi_cfg.get("sell_pct_bearish_divergence", 0.35) * 100)
    sell_pct_75 = float(rsi_cfg.get("sell_pct_rsi_75", 0.25) * 100)
    sell_pct_80 = float(rsi_cfg.get("sell_pct_rsi_80", 0.40) * 100)
    min_pivot_gap = int(rsi_cfg.get("min_bars_between_pivots", 3))
    max_pivot_gap = int(rsi_cfg.get("max_bars_between_pivots", 20))
    pivot_price_source = str(rsi_cfg.get("pivot_price_source", "close"))
    require_confirmation_cross = bool(rsi_cfg.get("require_confirmation_cross", True))
    max_one_divergence_per_cycle = bool(rsi_cfg.get("max_one_divergence_per_cycle", True))

    if is_rsi_mode:
        st.subheader("RSI Cycle Strategy")
        buy_col1, buy_col2, buy_col3 = st.columns(3)
        oversold_threshold = buy_col1.number_input(
            "Oversold threshold",
            min_value=5.0,
            max_value=50.0,
            value=oversold_threshold,
            step=1.0,
        )
        deep_oversold_1 = buy_col2.number_input(
            "Deep oversold 1",
            min_value=5.0,
            max_value=50.0,
            value=deep_oversold_1,
            step=1.0,
        )
        deep_oversold_2 = buy_col3.number_input(
            "Deep oversold 2",
            min_value=5.0,
            max_value=50.0,
            value=deep_oversold_2,
            step=1.0,
        )
        buy_col4, buy_col5, buy_col6 = st.columns(3)
        buy_pct_div = buy_col4.number_input(
            "% compra divergencia alcista",
            min_value=0.0,
            max_value=100.0,
            value=buy_pct_div,
            step=5.0,
        )
        buy_pct_25 = buy_col5.number_input(
            "% compra RSI<=25",
            min_value=0.0,
            max_value=100.0,
            value=buy_pct_25,
            step=5.0,
        )
        buy_pct_20 = buy_col6.number_input(
            "% compra RSI<=20",
            min_value=0.0,
            max_value=100.0,
            value=buy_pct_20,
            step=5.0,
        )

        sell_col1, sell_col2, sell_col3 = st.columns(3)
        overbought_threshold = sell_col1.number_input(
            "Overbought threshold",
            min_value=50.0,
            max_value=95.0,
            value=overbought_threshold,
            step=1.0,
        )
        overbought_1 = sell_col2.number_input(
            "Overbought 1",
            min_value=50.0,
            max_value=95.0,
            value=overbought_1,
            step=1.0,
        )
        overbought_2 = sell_col3.number_input(
            "Overbought 2",
            min_value=50.0,
            max_value=99.0,
            value=overbought_2,
            step=1.0,
        )
        sell_col4, sell_col5, sell_col6 = st.columns(3)
        sell_pct_div = sell_col4.number_input(
            "% venta divergencia bajista",
            min_value=0.0,
            max_value=100.0,
            value=sell_pct_div,
            step=5.0,
        )
        sell_pct_75 = sell_col5.number_input(
            "% venta RSI>=75",
            min_value=0.0,
            max_value=100.0,
            value=sell_pct_75,
            step=5.0,
        )
        sell_pct_80 = sell_col6.number_input(
            "% venta RSI>=80",
            min_value=0.0,
            max_value=100.0,
            value=sell_pct_80,
            step=5.0,
        )

        div_col1, div_col2, div_col3, div_col4 = st.columns(4)
        min_pivot_gap = div_col1.number_input(
            "Min bars between pivots",
            min_value=1,
            max_value=20,
            value=min_pivot_gap,
            step=1,
        )
        max_pivot_gap = div_col2.number_input(
            "Max bars between pivots",
            min_value=2,
            max_value=60,
            value=max_pivot_gap,
            step=1,
        )
        pivot_price_source = div_col3.selectbox(
            "Pivot price source",
            ["close", "extremes"],
            index=0 if pivot_price_source == "close" else 1,
        )
        require_confirmation_cross = div_col4.checkbox(
            "Confirmacion por reentrada 30/70",
            value=require_confirmation_cross,
        )
        max_one_divergence_per_cycle = st.checkbox(
            "Una divergencia maxima por ciclo",
            value=max_one_divergence_per_cycle,
        )
        use_suggested_weight_add = False
        apply_portfolio_limits = True

    col_run, col_opt = st.columns(2)
    run_backtest = col_run.form_submit_button("Lanzar backtest", use_container_width=True)
    run_optimization = col_opt.form_submit_button(
        "Lanzar optimizacion",
        use_container_width=True,
        disabled=is_rsi_mode,
    )

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
    mode=BacktestMode(mode),
    entry_overrides=(
        {
            "min_final_score": float(min_final_score),
            "max_risk_score": float(max_risk_score),
            "max_rsi14": float(max_rsi14),
            "require_bullish_trend": require_bullish_trend,
            "allowed_recommendations": tuple(allowed_recommendations),
        }
        if override_thresholds and not is_rsi_mode
        else None
    ),
    exit_overrides=(
        None
        if is_rsi_mode
        else {
            "strategy": exit_strategy,
            "fixed_horizon_days": fixed_horizon,
            "take_profit_pct": take_profit_pct / 100,
            "stop_loss_pct": stop_loss_pct / 100,
            "signal_loss_score_threshold": float(signal_loss_threshold),
            "max_holding_days": max(fixed_horizon, base_scenario.exit_rules.max_holding_days),
            "position_alert_exit_types": tuple(position_alert_exit_types),
        }
    ),
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
    rsi_cycle_overrides=(
        {
            "oversold_threshold": oversold_threshold,
            "deep_oversold_threshold_1": deep_oversold_1,
            "deep_oversold_threshold_2": deep_oversold_2,
            "overbought_threshold": overbought_threshold,
            "overbought_threshold_1": overbought_1,
            "overbought_threshold_2": overbought_2,
            "buy_pct_bullish_divergence": buy_pct_div / 100,
            "buy_pct_rsi_25": buy_pct_25 / 100,
            "buy_pct_rsi_20": buy_pct_20 / 100,
            "sell_pct_bearish_divergence": sell_pct_div / 100,
            "sell_pct_rsi_75": sell_pct_75 / 100,
            "sell_pct_rsi_80": sell_pct_80 / 100,
            "min_bars_between_pivots": int(min_pivot_gap),
            "max_bars_between_pivots": int(max_pivot_gap),
            "pivot_price_source": pivot_price_source,
            "require_confirmation_cross": require_confirmation_cross,
            "max_one_divergence_per_cycle": max_one_divergence_per_cycle,
        }
        if is_rsi_mode
        else None
    ),
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

    frames_map = run_result_to_frames(result)
    trades_df = frames_map["trades"]
    equity_df = frames_map["equity"]
    cash_df = frames_map.get("cash", pd.DataFrame())
    portfolio_events_df = frames_map.get("portfolio_events", pd.DataFrame())

    if not equity_df.empty:
        st.plotly_chart(
            px.line(equity_df, x="date", y="equity", title="Equity curve"),
            width="stretch",
        )
        equity_df["rolling_peak"] = equity_df["equity"].cummax()
        equity_df["drawdown_pct"] = ((equity_df["equity"] / equity_df["rolling_peak"]) - 1) * 100
        st.plotly_chart(
            px.area(equity_df, x="date", y="drawdown_pct", title="Drawdown chart"),
            width="stretch",
        )
        if not cash_df.empty:
            st.plotly_chart(
                px.line(cash_df, x="date", y="cash", title="Cash curve"),
                width="stretch",
            )

    if result.portfolio_summary:
        st.subheader("Resumen cartera simulada")
        summary = result.portfolio_summary
        summary_cols = st.columns(6)
        summary_cols[0].metric("Capital inicial", f"{summary['initial_capital']:.0f} EUR")
        summary_cols[1].metric("Capital final", f"{summary['final_capital']:.0f} EUR")
        summary_cols[2].metric("Retorno cartera", f"{summary['portfolio_return_pct']:.2f}%")
        summary_cols[3].metric("Cash final", f"{summary['final_cash']:.0f} EUR")
        summary_cols[4].metric("PnL realizado", f"{summary['realized_pnl']:.0f} EUR")
        summary_cols[5].metric("PnL no realizado", f"{summary['unrealized_pnl']:.0f} EUR")
        summary_cols = st.columns(6)
        summary_cols[0].metric("Compras", int(summary["buy_count"]))
        summary_cols[1].metric("Ventas parciales", int(summary["sell_partial_count"]))
        summary_cols[2].metric("Salidas completas", int(summary["sell_full_count"]))
        summary_cols[3].metric("Entrada media", f"{summary['avg_entry_size_pct']:.2f}%")
        summary_cols[4].metric("Reduccion media", f"{summary['avg_reduction_size_pct']:.2f}%")
        summary_cols[5].metric("Concentracion max", f"{summary['max_concentration_pct']:.2f}%")
        if is_rsi_mode:
            rsi_cols = st.columns(6)
            rsi_cols[0].metric("BUY_RSI_25", int(summary.get("buy_rsi_25_count", 0)))
            rsi_cols[1].metric("BUY_RSI_20", int(summary.get("buy_rsi_20_count", 0)))
            rsi_cols[2].metric(
                "BUY_BULLISH_DIVERGENCE",
                int(summary.get("buy_bullish_divergence_count", 0)),
            )
            rsi_cols[3].metric("SELL_RSI_75", int(summary.get("sell_rsi_75_count", 0)))
            rsi_cols[4].metric("SELL_RSI_80", int(summary.get("sell_rsi_80_count", 0)))
            rsi_cols[5].metric(
                "SELL_BEARISH_DIVERGENCE",
                int(summary.get("sell_bearish_divergence_count", 0)),
            )

    if not trades_df.empty:
        st.plotly_chart(
            px.histogram(trades_df, x="net_return_pct", nbins=30, title="Distribucion de retornos"),
            width="stretch",
        )
        if not is_rsi_mode:
            score_chart = px.scatter(
                trades_df,
                x="final_score",
                y="net_return_pct",
                color="recommendation",
                hover_name="symbol",
                title="Final score vs retorno neto",
            )
            st.plotly_chart(score_chart, width="stretch")

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
        segment_df = frames_map.get(f"segment_{segment_choice}", pd.DataFrame())
        if not segment_df.empty:
            st.dataframe(segment_df, use_container_width=True, hide_index=True)
            st.plotly_chart(
                px.bar(
                    segment_df,
                    x="segment",
                    y="expectancy_pct",
                    title=f"Expectancy por {segment_choice}",
                ),
                width="stretch",
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
        st.plotly_chart(fig, width="stretch")
