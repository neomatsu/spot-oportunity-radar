from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Any


class BacktestMode(StrEnum):
    TRADE_BY_TRADE = "trade_by_trade"
    PORTFOLIO = "portfolio"
    PORTFOLIO_REALISTIC = "portfolio_realistic"


class EntryMode(StrEnum):
    CLOSE = "close_signal_day"
    NEXT_OPEN = "next_open"


class ExitStrategy(StrEnum):
    FIXED_HORIZON = "fixed_horizon"
    TAKE_PROFIT_STOP_LOSS = "take_profit_stop_loss"
    SIGNAL_LOSS = "signal_loss"
    HYBRID = "hybrid"
    POSITION_ALERTS = "position_alerts"
    HYBRID_POSITION_ALERTS = "hybrid_position_alerts"


class PositionSizeMode(StrEnum):
    FIXED = "fixed"
    SUGGESTED_WEIGHT = "suggested_weight_add"
    FIXED_CAPITAL_PCT = "fixed_capital_pct"


@dataclass(slots=True)
class EntryRules:
    min_final_score: float
    max_risk_score: float
    max_distance_to_support_pct: float | None
    max_rsi14: float | None
    require_bullish_trend: bool
    allowed_recommendations: tuple[str, ...]


@dataclass(slots=True)
class ExitRules:
    strategy: ExitStrategy
    fixed_horizon_days: int
    max_holding_days: int
    take_profit_pct: float | None
    stop_loss_pct: float | None
    signal_loss_score_threshold: float | None
    invalidation_buffer_pct: float
    position_alert_exit_types: tuple[str, ...]


@dataclass(slots=True)
class ExecutionRules:
    entry_mode: EntryMode
    position_size_mode: PositionSizeMode
    fixed_position_pct: float
    commission_bps: float
    slippage_bps: float


@dataclass(slots=True)
class EvaluationRules:
    train_ratio: float
    min_trades_warning_threshold: int


@dataclass(slots=True)
class PortfolioSimulationRules:
    cash_min_target_pct: float
    max_asset_weight: float
    max_sector_weight: float
    max_asset_type_weight: dict[str, float]
    apply_portfolio_limits: bool
    allow_add_to_existing: bool
    use_suggested_weight_add: bool
    buy_weight_override_pct: float | None
    min_trade_value: float
    min_residual_position_value: float
    sell_reduction_by_alert_type: dict[str, float]
    sell_priority: tuple[str, ...]


@dataclass(slots=True)
class BacktestScenario:
    name: str
    mode: BacktestMode
    start_date: date
    end_date: date
    assets: tuple[str, ...]
    initial_capital: float
    max_open_positions: int
    allow_overlapping_per_asset: bool
    respect_sector_limits: bool
    respect_asset_type_limits: bool
    entry_rules: EntryRules
    exit_rules: ExitRules
    execution_rules: ExecutionRules
    evaluation_rules: EvaluationRules
    portfolio_simulation_rules: PortfolioSimulationRules

    def to_payload(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "mode": self.mode.value,
            "start_date": self.start_date.isoformat(),
            "end_date": self.end_date.isoformat(),
            "assets": list(self.assets),
            "initial_capital": self.initial_capital,
            "max_open_positions": self.max_open_positions,
            "allow_overlapping_per_asset": self.allow_overlapping_per_asset,
            "respect_sector_limits": self.respect_sector_limits,
            "respect_asset_type_limits": self.respect_asset_type_limits,
            "entry_rules": {
                "min_final_score": self.entry_rules.min_final_score,
                "max_risk_score": self.entry_rules.max_risk_score,
                "max_distance_to_support_pct": self.entry_rules.max_distance_to_support_pct,
                "max_rsi14": self.entry_rules.max_rsi14,
                "require_bullish_trend": self.entry_rules.require_bullish_trend,
                "allowed_recommendations": list(self.entry_rules.allowed_recommendations),
            },
            "exit_rules": {
                "strategy": self.exit_rules.strategy.value,
                "fixed_horizon_days": self.exit_rules.fixed_horizon_days,
                "max_holding_days": self.exit_rules.max_holding_days,
                "take_profit_pct": self.exit_rules.take_profit_pct,
                "stop_loss_pct": self.exit_rules.stop_loss_pct,
                "signal_loss_score_threshold": self.exit_rules.signal_loss_score_threshold,
                "invalidation_buffer_pct": self.exit_rules.invalidation_buffer_pct,
                "position_alert_exit_types": list(self.exit_rules.position_alert_exit_types),
            },
            "execution_rules": {
                "entry_mode": self.execution_rules.entry_mode.value,
                "position_size_mode": self.execution_rules.position_size_mode.value,
                "fixed_position_pct": self.execution_rules.fixed_position_pct,
                "commission_bps": self.execution_rules.commission_bps,
                "slippage_bps": self.execution_rules.slippage_bps,
            },
            "evaluation_rules": {
                "train_ratio": self.evaluation_rules.train_ratio,
                "min_trades_warning_threshold": self.evaluation_rules.min_trades_warning_threshold,
            },
            "portfolio_simulation_rules": {
                "cash_min_target_pct": self.portfolio_simulation_rules.cash_min_target_pct,
                "max_asset_weight": self.portfolio_simulation_rules.max_asset_weight,
                "max_sector_weight": self.portfolio_simulation_rules.max_sector_weight,
                "max_asset_type_weight": self.portfolio_simulation_rules.max_asset_type_weight,
                "apply_portfolio_limits": self.portfolio_simulation_rules.apply_portfolio_limits,
                "allow_add_to_existing": self.portfolio_simulation_rules.allow_add_to_existing,
                "use_suggested_weight_add": (
                    self.portfolio_simulation_rules.use_suggested_weight_add
                ),
                "buy_weight_override_pct": self.portfolio_simulation_rules.buy_weight_override_pct,
                "min_trade_value": self.portfolio_simulation_rules.min_trade_value,
                "min_residual_position_value": (
                    self.portfolio_simulation_rules.min_residual_position_value
                ),
                "sell_reduction_by_alert_type": (
                    self.portfolio_simulation_rules.sell_reduction_by_alert_type
                ),
                "sell_priority": list(self.portfolio_simulation_rules.sell_priority),
            },
        }


@dataclass(slots=True)
class HistoricalSignal:
    asset_id: int
    symbol: str
    name: str
    asset_type: str
    sector: str
    signal_date: date
    technical_score: float
    risk_score: float
    portfolio_fit_score: float
    final_score: float
    recommendation: str
    rsi14: float | None
    sma50: float | None
    distance_to_support_pct: float | None
    support_low: float | None
    support_high: float | None
    trend_bullish: bool
    suggested_weight_add_pct: float
    invalidation_level: float | None
    rationale: dict[str, Any] = field(default_factory=dict)
    score_breakdown: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class SimulatedTrade:
    asset_id: int
    symbol: str
    name: str
    asset_type: str
    sector: str
    entry_signal_date: date
    entry_date: date
    exit_date: date
    entry_price: float
    exit_price: float
    position_pct: float
    gross_return_pct: float
    net_return_pct: float
    max_drawdown_pct: float
    mae_pct: float
    mfe_pct: float
    holding_days: int
    exit_reason: str
    recommendation: str
    technical_score: float
    risk_score: float
    portfolio_fit_score: float
    final_score: float
    invalidation_level: float | None
    rationale: dict[str, Any] = field(default_factory=dict)
    parameters: dict[str, Any] = field(default_factory=dict)

    @property
    def score_band(self) -> str:
        if self.final_score >= 75:
            return "75+"
        if self.final_score >= 65:
            return "65-74"
        if self.final_score >= 55:
            return "55-64"
        return "<55"

    @property
    def risk_band(self) -> str:
        if self.risk_score <= 33:
            return "low"
        if self.risk_score <= 66:
            return "medium"
        return "high"


@dataclass(slots=True)
class StrategyMetrics:
    total_trades: int
    win_rate_pct: float
    avg_return_pct: float
    median_return_pct: float
    profit_factor: float
    expectancy_pct: float
    max_drawdown_pct: float
    best_trade_pct: float
    worst_trade_pct: float
    avg_holding_days: float
    return_to_drawdown: float
    sharpe_like: float
    total_net_return_pct: float
    trades_per_month: float

    def to_dict(self) -> dict[str, float]:
        return {
            "total_trades": self.total_trades,
            "win_rate_pct": self.win_rate_pct,
            "avg_return_pct": self.avg_return_pct,
            "median_return_pct": self.median_return_pct,
            "profit_factor": self.profit_factor,
            "expectancy_pct": self.expectancy_pct,
            "max_drawdown_pct": self.max_drawdown_pct,
            "best_trade_pct": self.best_trade_pct,
            "worst_trade_pct": self.worst_trade_pct,
            "avg_holding_days": self.avg_holding_days,
            "return_to_drawdown": self.return_to_drawdown,
            "sharpe_like": self.sharpe_like,
            "total_net_return_pct": self.total_net_return_pct,
            "trades_per_month": self.trades_per_month,
        }


@dataclass(slots=True)
class BacktestRunResult:
    run_id: int | None
    parameter_set_id: int | None
    scenario: BacktestScenario
    trades: list[SimulatedTrade]
    metrics: StrategyMetrics
    segmented_metrics: dict[str, dict[str, StrategyMetrics]]
    equity_curve: list[dict[str, Any]]
    warnings: list[str]
    cash_curve: list[dict[str, Any]] = field(default_factory=list)
    portfolio_events: list[dict[str, Any]] = field(default_factory=list)
    portfolio_summary: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class OptimizationResult:
    run_id: int | None
    parameter_set_id: int | None
    parameters: dict[str, Any]
    in_sample_metrics: StrategyMetrics
    out_of_sample_metrics: StrategyMetrics
    evaluation_score: float
    warning: str | None = None
