from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from backtesting.models import (
    BacktestMode,
    BacktestScenario,
    EntryMode,
    EntryRules,
    EvaluationRules,
    ExecutionRules,
    ExitRules,
    ExitStrategy,
    PortfolioSimulationRules,
    PositionSizeMode,
    RSICycleRules,
)
from core.config import load_yaml_config


def load_backtesting_config() -> dict[str, Any]:
    return load_yaml_config("backtesting.yaml")


def load_optimization_config() -> dict[str, Any]:
    return load_yaml_config("optimization.yaml")


def default_backtest_scenario(
    *,
    assets: list[str],
    start_date: date | None = None,
    end_date: date | None = None,
) -> BacktestScenario:
    config = load_backtesting_config()
    defaults = config["defaults"]
    execution = config["execution"]
    entry_rules = config["entry_rules"]
    exit_rules = config["exit_rules"]
    evaluation = config["evaluation"]
    portfolio = config["portfolio"]
    portfolio_sim = config.get("portfolio_simulation", {})
    rsi_cycle = config.get("rsi_cycle_strategy", {})

    default_end = end_date or date.today()
    default_start = start_date or (default_end - timedelta(days=365 * 2))

    return BacktestScenario(
        name="default",
        mode=BacktestMode(defaults["mode"]),
        start_date=default_start,
        end_date=default_end,
        assets=tuple(assets),
        initial_capital=float(defaults["initial_capital"]),
        max_open_positions=int(defaults["max_open_positions"]),
        allow_overlapping_per_asset=bool(defaults["allow_overlapping_per_asset"]),
        respect_sector_limits=bool(portfolio["respect_sector_limits"]),
        respect_asset_type_limits=bool(portfolio["respect_asset_type_limits"]),
        entry_rules=EntryRules(
            min_final_score=float(entry_rules["min_final_score"]),
            max_risk_score=float(entry_rules["max_risk_score"]),
            max_distance_to_support_pct=_optional_float(
                entry_rules.get("max_distance_to_support_pct")
            ),
            max_rsi14=_optional_float(entry_rules.get("max_rsi14")),
            require_bullish_trend=bool(entry_rules["require_bullish_trend"]),
            allowed_recommendations=tuple(entry_rules["allowed_recommendations"]),
        ),
        exit_rules=ExitRules(
            strategy=ExitStrategy(exit_rules["strategy"]),
            fixed_horizon_days=int(exit_rules["fixed_horizon_days"]),
            max_holding_days=int(exit_rules["max_holding_days"]),
            take_profit_pct=_optional_float(exit_rules.get("take_profit_pct")),
            stop_loss_pct=_optional_float(exit_rules.get("stop_loss_pct")),
            signal_loss_score_threshold=_optional_float(
                exit_rules.get("signal_loss_score_threshold")
            ),
            invalidation_buffer_pct=float(exit_rules["invalidation_buffer_pct"]),
            position_alert_exit_types=tuple(exit_rules.get("position_alert_exit_types", [])),
        ),
        execution_rules=ExecutionRules(
            entry_mode=EntryMode(execution["entry_mode"]),
            position_size_mode=PositionSizeMode(execution["position_size_mode"]),
            fixed_position_pct=float(execution["fixed_position_pct"]),
            commission_bps=float(execution["commission_bps"]),
            slippage_bps=float(execution["slippage_bps"]),
        ),
        evaluation_rules=EvaluationRules(
            train_ratio=float(evaluation["train_ratio"]),
            min_trades_warning_threshold=int(evaluation["min_trades_warning_threshold"]),
        ),
        portfolio_simulation_rules=PortfolioSimulationRules(
            cash_min_target_pct=float(portfolio_sim.get("cash_min_target_pct", 0.1)),
            max_asset_weight=float(
                portfolio_sim.get("max_asset_weight", portfolio["max_asset_weight"])
            ),
            max_sector_weight=float(
                portfolio_sim.get("max_sector_weight", portfolio["max_sector_weight"])
            ),
            max_asset_type_weight=dict(
                portfolio_sim.get(
                    "max_asset_type_weight",
                    portfolio["max_asset_type_weight"],
                )
            ),
            apply_portfolio_limits=bool(portfolio_sim.get("apply_portfolio_limits", True)),
            allow_add_to_existing=bool(portfolio_sim.get("allow_add_to_existing", True)),
            use_suggested_weight_add=bool(portfolio_sim.get("use_suggested_weight_add", True)),
            buy_weight_override_pct=_optional_float(portfolio_sim.get("buy_weight_override_pct")),
            min_trade_value=float(portfolio_sim.get("min_trade_value", 250.0)),
            min_residual_position_value=float(
                portfolio_sim.get("min_residual_position_value", 150.0)
            ),
            sell_reduction_by_alert_type=dict(
                portfolio_sim.get("sell_reduction_by_alert_type", {})
            ),
            sell_priority=tuple(
                portfolio_sim.get(
                    "sell_priority",
                    [
                        "stop_loss_warning",
                        "exit_candidate",
                        "reduce_risk",
                        "trim_position",
                        "take_profit",
                        "rebalance_sell",
                        "overbought_warning",
                    ],
                )
            ),
        ),
        rsi_cycle_rules=RSICycleRules(
            oversold_threshold=float(rsi_cycle.get("oversold_threshold", 30)),
            deep_oversold_threshold_1=float(rsi_cycle.get("deep_oversold_threshold_1", 25)),
            deep_oversold_threshold_2=float(rsi_cycle.get("deep_oversold_threshold_2", 20)),
            overbought_threshold=float(rsi_cycle.get("overbought_threshold", 70)),
            overbought_threshold_1=float(rsi_cycle.get("overbought_threshold_1", 75)),
            overbought_threshold_2=float(rsi_cycle.get("overbought_threshold_2", 80)),
            buy_pct_bullish_divergence=float(rsi_cycle.get("buy_pct_bullish_divergence", 0.35)),
            buy_pct_rsi_25=float(rsi_cycle.get("buy_pct_rsi_25", 0.25)),
            buy_pct_rsi_20=float(rsi_cycle.get("buy_pct_rsi_20", 0.40)),
            sell_pct_bearish_divergence=float(rsi_cycle.get("sell_pct_bearish_divergence", 0.35)),
            sell_pct_rsi_75=float(rsi_cycle.get("sell_pct_rsi_75", 0.25)),
            sell_pct_rsi_80=float(rsi_cycle.get("sell_pct_rsi_80", 0.40)),
            min_bars_between_pivots=int(rsi_cycle.get("min_bars_between_pivots", 3)),
            max_bars_between_pivots=int(rsi_cycle.get("max_bars_between_pivots", 20)),
            pivot_price_source=str(rsi_cycle.get("pivot_price_source", "close")),
            require_confirmation_cross=bool(rsi_cycle.get("require_confirmation_cross", True)),
            max_one_divergence_per_cycle=bool(rsi_cycle.get("max_one_divergence_per_cycle", True)),
        ),
    )


def scenario_with_overrides(
    base: BacktestScenario,
    *,
    name: str | None = None,
    mode: BacktestMode | None = None,
    entry_overrides: dict[str, Any] | None = None,
    exit_overrides: dict[str, Any] | None = None,
    execution_overrides: dict[str, Any] | None = None,
    portfolio_simulation_overrides: dict[str, Any] | None = None,
    rsi_cycle_overrides: dict[str, Any] | None = None,
) -> BacktestScenario:
    entry = {
        "min_final_score": base.entry_rules.min_final_score,
        "max_risk_score": base.entry_rules.max_risk_score,
        "max_distance_to_support_pct": base.entry_rules.max_distance_to_support_pct,
        "max_rsi14": base.entry_rules.max_rsi14,
        "require_bullish_trend": base.entry_rules.require_bullish_trend,
        "allowed_recommendations": base.entry_rules.allowed_recommendations,
    }
    entry.update(entry_overrides or {})

    exit_payload = {
        "strategy": base.exit_rules.strategy,
        "fixed_horizon_days": base.exit_rules.fixed_horizon_days,
        "max_holding_days": base.exit_rules.max_holding_days,
        "take_profit_pct": base.exit_rules.take_profit_pct,
        "stop_loss_pct": base.exit_rules.stop_loss_pct,
        "signal_loss_score_threshold": base.exit_rules.signal_loss_score_threshold,
        "invalidation_buffer_pct": base.exit_rules.invalidation_buffer_pct,
        "position_alert_exit_types": base.exit_rules.position_alert_exit_types,
    }
    exit_payload.update(exit_overrides or {})

    execution = {
        "entry_mode": base.execution_rules.entry_mode,
        "position_size_mode": base.execution_rules.position_size_mode,
        "fixed_position_pct": base.execution_rules.fixed_position_pct,
        "commission_bps": base.execution_rules.commission_bps,
        "slippage_bps": base.execution_rules.slippage_bps,
    }
    execution.update(execution_overrides or {})
    portfolio_sim = {
        "cash_min_target_pct": base.portfolio_simulation_rules.cash_min_target_pct,
        "max_asset_weight": base.portfolio_simulation_rules.max_asset_weight,
        "max_sector_weight": base.portfolio_simulation_rules.max_sector_weight,
        "max_asset_type_weight": base.portfolio_simulation_rules.max_asset_type_weight,
        "apply_portfolio_limits": base.portfolio_simulation_rules.apply_portfolio_limits,
        "allow_add_to_existing": base.portfolio_simulation_rules.allow_add_to_existing,
        "use_suggested_weight_add": base.portfolio_simulation_rules.use_suggested_weight_add,
        "buy_weight_override_pct": base.portfolio_simulation_rules.buy_weight_override_pct,
        "min_trade_value": base.portfolio_simulation_rules.min_trade_value,
        "min_residual_position_value": (
            base.portfolio_simulation_rules.min_residual_position_value
        ),
        "sell_reduction_by_alert_type": (
            base.portfolio_simulation_rules.sell_reduction_by_alert_type
        ),
        "sell_priority": base.portfolio_simulation_rules.sell_priority,
    }
    portfolio_sim.update(portfolio_simulation_overrides or {})
    rsi_cycle = {
        "oversold_threshold": base.rsi_cycle_rules.oversold_threshold,
        "deep_oversold_threshold_1": base.rsi_cycle_rules.deep_oversold_threshold_1,
        "deep_oversold_threshold_2": base.rsi_cycle_rules.deep_oversold_threshold_2,
        "overbought_threshold": base.rsi_cycle_rules.overbought_threshold,
        "overbought_threshold_1": base.rsi_cycle_rules.overbought_threshold_1,
        "overbought_threshold_2": base.rsi_cycle_rules.overbought_threshold_2,
        "buy_pct_bullish_divergence": base.rsi_cycle_rules.buy_pct_bullish_divergence,
        "buy_pct_rsi_25": base.rsi_cycle_rules.buy_pct_rsi_25,
        "buy_pct_rsi_20": base.rsi_cycle_rules.buy_pct_rsi_20,
        "sell_pct_bearish_divergence": base.rsi_cycle_rules.sell_pct_bearish_divergence,
        "sell_pct_rsi_75": base.rsi_cycle_rules.sell_pct_rsi_75,
        "sell_pct_rsi_80": base.rsi_cycle_rules.sell_pct_rsi_80,
        "min_bars_between_pivots": base.rsi_cycle_rules.min_bars_between_pivots,
        "max_bars_between_pivots": base.rsi_cycle_rules.max_bars_between_pivots,
        "pivot_price_source": base.rsi_cycle_rules.pivot_price_source,
        "require_confirmation_cross": base.rsi_cycle_rules.require_confirmation_cross,
        "max_one_divergence_per_cycle": base.rsi_cycle_rules.max_one_divergence_per_cycle,
    }
    rsi_cycle.update(rsi_cycle_overrides or {})

    return BacktestScenario(
        name=name or base.name,
        mode=mode or base.mode,
        start_date=base.start_date,
        end_date=base.end_date,
        assets=base.assets,
        initial_capital=base.initial_capital,
        max_open_positions=base.max_open_positions,
        allow_overlapping_per_asset=base.allow_overlapping_per_asset,
        respect_sector_limits=base.respect_sector_limits,
        respect_asset_type_limits=base.respect_asset_type_limits,
        entry_rules=EntryRules(
            min_final_score=float(entry["min_final_score"]),
            max_risk_score=float(entry["max_risk_score"]),
            max_distance_to_support_pct=_optional_float(entry["max_distance_to_support_pct"]),
            max_rsi14=_optional_float(entry["max_rsi14"]),
            require_bullish_trend=bool(entry["require_bullish_trend"]),
            allowed_recommendations=tuple(entry["allowed_recommendations"]),
        ),
        exit_rules=ExitRules(
            strategy=(
                exit_payload["strategy"]
                if isinstance(exit_payload["strategy"], ExitStrategy)
                else ExitStrategy(exit_payload["strategy"])
            ),
            fixed_horizon_days=int(exit_payload["fixed_horizon_days"]),
            max_holding_days=int(exit_payload["max_holding_days"]),
            take_profit_pct=_optional_float(exit_payload["take_profit_pct"]),
            stop_loss_pct=_optional_float(exit_payload["stop_loss_pct"]),
            signal_loss_score_threshold=_optional_float(
                exit_payload["signal_loss_score_threshold"]
            ),
            invalidation_buffer_pct=float(exit_payload["invalidation_buffer_pct"]),
            position_alert_exit_types=tuple(exit_payload["position_alert_exit_types"]),
        ),
        execution_rules=ExecutionRules(
            entry_mode=(
                execution["entry_mode"]
                if isinstance(execution["entry_mode"], EntryMode)
                else EntryMode(execution["entry_mode"])
            ),
            position_size_mode=(
                execution["position_size_mode"]
                if isinstance(execution["position_size_mode"], PositionSizeMode)
                else PositionSizeMode(execution["position_size_mode"])
            ),
            fixed_position_pct=float(execution["fixed_position_pct"]),
            commission_bps=float(execution["commission_bps"]),
            slippage_bps=float(execution["slippage_bps"]),
        ),
        evaluation_rules=base.evaluation_rules,
        portfolio_simulation_rules=PortfolioSimulationRules(
            cash_min_target_pct=float(portfolio_sim["cash_min_target_pct"]),
            max_asset_weight=float(portfolio_sim["max_asset_weight"]),
            max_sector_weight=float(portfolio_sim["max_sector_weight"]),
            max_asset_type_weight=dict(portfolio_sim["max_asset_type_weight"]),
            apply_portfolio_limits=bool(portfolio_sim["apply_portfolio_limits"]),
            allow_add_to_existing=bool(portfolio_sim["allow_add_to_existing"]),
            use_suggested_weight_add=bool(portfolio_sim["use_suggested_weight_add"]),
            buy_weight_override_pct=_optional_float(portfolio_sim["buy_weight_override_pct"]),
            min_trade_value=float(portfolio_sim["min_trade_value"]),
            min_residual_position_value=float(portfolio_sim["min_residual_position_value"]),
            sell_reduction_by_alert_type=dict(portfolio_sim["sell_reduction_by_alert_type"]),
            sell_priority=tuple(portfolio_sim["sell_priority"]),
        ),
        rsi_cycle_rules=RSICycleRules(
            oversold_threshold=float(rsi_cycle["oversold_threshold"]),
            deep_oversold_threshold_1=float(rsi_cycle["deep_oversold_threshold_1"]),
            deep_oversold_threshold_2=float(rsi_cycle["deep_oversold_threshold_2"]),
            overbought_threshold=float(rsi_cycle["overbought_threshold"]),
            overbought_threshold_1=float(rsi_cycle["overbought_threshold_1"]),
            overbought_threshold_2=float(rsi_cycle["overbought_threshold_2"]),
            buy_pct_bullish_divergence=float(rsi_cycle["buy_pct_bullish_divergence"]),
            buy_pct_rsi_25=float(rsi_cycle["buy_pct_rsi_25"]),
            buy_pct_rsi_20=float(rsi_cycle["buy_pct_rsi_20"]),
            sell_pct_bearish_divergence=float(rsi_cycle["sell_pct_bearish_divergence"]),
            sell_pct_rsi_75=float(rsi_cycle["sell_pct_rsi_75"]),
            sell_pct_rsi_80=float(rsi_cycle["sell_pct_rsi_80"]),
            min_bars_between_pivots=int(rsi_cycle["min_bars_between_pivots"]),
            max_bars_between_pivots=int(rsi_cycle["max_bars_between_pivots"]),
            pivot_price_source=str(rsi_cycle["pivot_price_source"]),
            require_confirmation_cross=bool(rsi_cycle["require_confirmation_cross"]),
            max_one_divergence_per_cycle=bool(rsi_cycle["max_one_divergence_per_cycle"]),
        ),
    )


def split_in_sample_out_of_sample(
    start_date: date,
    end_date: date,
    train_ratio: float,
) -> tuple[tuple[date, date], tuple[date, date]]:
    total_days = max(1, (end_date - start_date).days)
    split_offset = max(1, int(total_days * train_ratio))
    train_end = min(end_date, start_date + timedelta(days=split_offset))
    test_start = min(end_date, train_end + timedelta(days=1))
    return (start_date, train_end), (test_start, end_date)


def _optional_float(value: Any) -> float | None:
    if value is None:
        return None
    return float(value)
