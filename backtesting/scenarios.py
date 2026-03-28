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
    PositionSizeMode,
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
    )


def scenario_with_overrides(
    base: BacktestScenario,
    *,
    name: str | None = None,
    mode: BacktestMode | None = None,
    entry_overrides: dict[str, Any] | None = None,
    exit_overrides: dict[str, Any] | None = None,
    execution_overrides: dict[str, Any] | None = None,
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
