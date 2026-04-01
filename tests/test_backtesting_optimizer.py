from __future__ import annotations

from datetime import date

from backtesting.models import (
    BacktestMode,
    BacktestRunResult,
    BacktestScenario,
    EntryMode,
    EntryRules,
    EvaluationRules,
    ExecutionRules,
    ExitRules,
    ExitStrategy,
    PortfolioSimulationRules,
    PositionSizeMode,
    StrategyMetrics,
)
from backtesting.optimizer import BacktestOptimizer


def _scenario() -> BacktestScenario:
    return BacktestScenario(
        name="opt",
        mode=BacktestMode.TRADE_BY_TRADE,
        start_date=date(2024, 1, 1),
        end_date=date(2024, 12, 31),
        assets=("AAA",),
        initial_capital=100000,
        max_open_positions=5,
        allow_overlapping_per_asset=False,
        respect_sector_limits=False,
        respect_asset_type_limits=False,
        entry_rules=EntryRules(
            min_final_score=60,
            max_risk_score=65,
            max_distance_to_support_pct=6,
            max_rsi14=65,
            require_bullish_trend=False,
            allowed_recommendations=("BUY_CANDIDATE", "WATCH"),
        ),
        exit_rules=ExitRules(
            strategy=ExitStrategy.HYBRID,
            fixed_horizon_days=20,
            max_holding_days=20,
            take_profit_pct=0.1,
            stop_loss_pct=0.05,
            signal_loss_score_threshold=45,
            invalidation_buffer_pct=0.01,
            position_alert_exit_types=(),
        ),
        execution_rules=ExecutionRules(
            entry_mode=EntryMode.NEXT_OPEN,
            position_size_mode=PositionSizeMode.FIXED_CAPITAL_PCT,
            fixed_position_pct=0.05,
            commission_bps=8,
            slippage_bps=5,
        ),
        evaluation_rules=EvaluationRules(train_ratio=0.7, min_trades_warning_threshold=8),
        portfolio_simulation_rules=PortfolioSimulationRules(
            cash_min_target_pct=0.1,
            max_asset_weight=0.12,
            max_sector_weight=0.3,
            max_asset_type_weight={"stock": 0.65, "etf": 0.8, "crypto": 0.15},
            apply_portfolio_limits=True,
            allow_add_to_existing=True,
            use_suggested_weight_add=True,
            buy_weight_override_pct=None,
            min_trade_value=250.0,
            min_residual_position_value=150.0,
            sell_reduction_by_alert_type={"exit_candidate": 1.0},
            sell_priority=("exit_candidate",),
        ),
    )


def _metrics(expectancy: float, profit_factor: float) -> StrategyMetrics:
    return StrategyMetrics(
        total_trades=20,
        win_rate_pct=55,
        avg_return_pct=3,
        median_return_pct=2,
        profit_factor=profit_factor,
        expectancy_pct=expectancy,
        max_drawdown_pct=8,
        best_trade_pct=12,
        worst_trade_pct=-6,
        avg_holding_days=8,
        return_to_drawdown=2,
        sharpe_like=0.6,
        total_net_return_pct=25,
        trades_per_month=1.5,
    )


def test_optimizer_runs_multiple_combinations_and_sorts(db_session, monkeypatch) -> None:
    optimizer = BacktestOptimizer(db_session)
    optimizer.config = {
        "grid": {
            "min_final_score": [55, 70],
            "max_risk_score": [65],
            "max_distance_to_support_pct": [6],
            "max_rsi14": [65],
            "fixed_horizon_days": [10],
            "take_profit_pct": [0.08],
            "stop_loss_pct": [0.05],
            "require_bullish_trend": [False],
        },
        "evaluation_score": {
            "expectancy_weight": 0.3,
            "profit_factor_weight": 0.25,
            "avg_return_weight": 0.2,
            "drawdown_penalty_weight": 0.15,
            "robustness_weight": 0.1,
            "low_trade_threshold": 10,
            "low_trade_penalty": 10,
        },
    }

    def fake_run(scenario, persist=False):
        score = scenario.entry_rules.min_final_score
        if scenario.name.endswith("_train"):
            metrics = _metrics(expectancy=score / 20, profit_factor=1 + score / 100)
        else:
            metrics = _metrics(expectancy=score / 25, profit_factor=1 + score / 120)
        return BacktestRunResult(
            run_id=None,
            parameter_set_id=None,
            scenario=scenario,
            trades=[],
            metrics=metrics,
            segmented_metrics={},
            equity_curve=[],
            warnings=[],
        )

    monkeypatch.setattr(optimizer.engine, "run", fake_run)
    results = optimizer.run_grid_search(_scenario())

    assert len(results) == 2
    assert results[0].evaluation_score >= results[1].evaluation_score
    assert results[0].parameters["min_final_score"] == 70
