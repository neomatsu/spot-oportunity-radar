from __future__ import annotations

from itertools import product

from sqlalchemy.orm import Session

from backtesting.engine import BacktestEngine
from backtesting.models import OptimizationResult
from backtesting.scenarios import (
    load_optimization_config,
    scenario_with_overrides,
    split_in_sample_out_of_sample,
)
from data.repositories.backtest_repo import BacktestRepository


class BacktestOptimizer:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.engine = BacktestEngine(session)
        self.repo = BacktestRepository(session)
        self.config = load_optimization_config()

    def run_grid_search(self, base_scenario) -> list[OptimizationResult]:
        evaluation_cfg = self.config["evaluation_score"]
        grid = self.config["grid"]
        parameter_names = list(grid)
        values_product = product(*(grid[name] for name in parameter_names))
        results: list[OptimizationResult] = []

        run = self.repo.create_run(
            {
                "name": f"{base_scenario.name}_optimization",
                "mode": base_scenario.mode.value,
                "start_date": base_scenario.start_date,
                "end_date": base_scenario.end_date,
                "train_start_date": base_scenario.start_date,
                "train_end_date": split_in_sample_out_of_sample(
                    base_scenario.start_date,
                    base_scenario.end_date,
                    base_scenario.evaluation_rules.train_ratio,
                )[0][1],
                "test_start_date": split_in_sample_out_of_sample(
                    base_scenario.start_date,
                    base_scenario.end_date,
                    base_scenario.evaluation_rules.train_ratio,
                )[1][0],
                "test_end_date": base_scenario.end_date,
                "assets_json": list(base_scenario.assets),
                "scenario_json": base_scenario.to_payload(),
                "status": "completed",
            }
        )

        (train_start, train_end), (test_start, test_end) = split_in_sample_out_of_sample(
            base_scenario.start_date,
            base_scenario.end_date,
            base_scenario.evaluation_rules.train_ratio,
        )

        for index, values in enumerate(values_product, start=1):
            params = dict(zip(parameter_names, values, strict=True))
            entry_overrides = {
                key: value
                for key, value in params.items()
                if key
                in {
                    "min_final_score",
                    "max_risk_score",
                    "max_distance_to_support_pct",
                    "max_rsi14",
                    "require_bullish_trend",
                }
            }
            exit_overrides = {
                key: value
                for key, value in params.items()
                if key in {"fixed_horizon_days", "take_profit_pct", "stop_loss_pct"}
            }

            train_scenario = scenario_with_overrides(
                base_scenario,
                name=f"grid_{index}_train",
                entry_overrides=entry_overrides,
                exit_overrides=exit_overrides,
            )
            train_scenario.start_date = train_start
            train_scenario.end_date = train_end

            test_scenario = scenario_with_overrides(
                base_scenario,
                name=f"grid_{index}_test",
                entry_overrides=entry_overrides,
                exit_overrides=exit_overrides,
            )
            test_scenario.start_date = test_start
            test_scenario.end_date = test_end

            train_result = self.engine.run(train_scenario, persist=False)
            test_result = self.engine.run(test_scenario, persist=False)
            evaluation_score = self._evaluation_score(
                train_result.metrics.to_dict(),
                test_result.metrics.to_dict(),
                evaluation_cfg,
            )
            warning = None
            if train_result.metrics.total_trades < evaluation_cfg["low_trade_threshold"]:
                warning = "Muy pocos trades en in-sample; riesgo elevado de sobreajuste."

            parameter_set = self.repo.create_parameter_set(
                {
                    "run_id": run.id,
                    "name": f"grid_{index}",
                    "parameters_json": params,
                    "evaluation_score": evaluation_score,
                    "in_sample_metrics_json": train_result.metrics.to_dict(),
                    "out_of_sample_metrics_json": test_result.metrics.to_dict(),
                }
            )
            self.repo.add_trades(
                self.engine._trade_payloads(
                    run.id,
                    parameter_set.id,
                    train_result.trades + test_result.trades,
                )
            )

            results.append(
                OptimizationResult(
                    run_id=run.id,
                    parameter_set_id=parameter_set.id,
                    parameters=params,
                    in_sample_metrics=train_result.metrics,
                    out_of_sample_metrics=test_result.metrics,
                    evaluation_score=round(evaluation_score, 2),
                    warning=warning,
                )
            )

        self.repo.add_metrics(
            [
                {
                    "run_id": run.id,
                    "scope": "optimization",
                    "segment_type": "summary",
                    "segment_value": f"grid_{result.parameter_set_id}",
                    "metrics_json": {
                        "evaluation_score": result.evaluation_score,
                        "warning": result.warning,
                        "in_sample": result.in_sample_metrics.to_dict(),
                        "out_of_sample": result.out_of_sample_metrics.to_dict(),
                    },
                }
                for result in results
            ]
        )
        return sorted(results, key=lambda result: result.evaluation_score, reverse=True)

    @staticmethod
    def _evaluation_score(
        in_sample: dict[str, float],
        out_of_sample: dict[str, float],
        cfg: dict[str, float],
    ) -> float:
        robustness = max(0.0, out_of_sample["expectancy_pct"])
        score = (
            cfg["expectancy_weight"] * in_sample["expectancy_pct"]
            + cfg["profit_factor_weight"] * in_sample["profit_factor"]
            + cfg["avg_return_weight"] * in_sample["avg_return_pct"]
            - cfg["drawdown_penalty_weight"] * in_sample["max_drawdown_pct"]
            + cfg["robustness_weight"] * robustness
        )
        if in_sample["total_trades"] < cfg["low_trade_threshold"]:
            score -= cfg["low_trade_penalty"]
        return score
