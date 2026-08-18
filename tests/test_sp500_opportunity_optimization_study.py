from __future__ import annotations

import pandas as pd

from backtesting.sp500_opportunity import SP500OpportunityBacktestConfig
from core.config import load_yaml_config
from jobs.run_sp500_opportunity_optimization_study import (
    StudyStore,
    _sizing_profiles,
    _task,
    run_stage,
    stage_1_tasks,
)


def test_staged_space_includes_zero_sizing_without_empty_profile() -> None:
    config = load_yaml_config("sp500_opportunity_study.yaml")

    stage_1 = stage_1_tasks(config)
    buy_profiles = _sizing_profiles(
        config["stage_2_sizing"]["percentage_candidates"], reverse=False
    )
    sell_profiles = _sizing_profiles(
        config["stage_2_sizing"]["percentage_candidates"], reverse=True
    )

    assert len(stage_1) == 1_120
    assert len(buy_profiles) == 55
    assert len(sell_profiles) == 55
    assert (0.0, 0.0, 0.1) in buy_profiles
    assert (0.1, 0.0, 0.0) in sell_profiles
    assert (0.0, 0.0, 0.0) not in buy_profiles


def test_stage_resumes_without_repeating_completed_configs(tmp_path) -> None:
    history = pd.DataFrame(
        {
            "date": pd.date_range("2020-01-01", periods=800, freq="B"),
            "overall_score": ([50, 66, 76, 86, 50, 44, 34, 24] * 100),
            "sp500_price": [100 + index * 0.02 for index in range(800)],
        }
    )
    periods = {
        "train": (pd.Timestamp("2020-01-01"), pd.Timestamp("2021-06-30")),
        "validation": (pd.Timestamp("2021-07-01"), pd.Timestamp("2023-01-31")),
    }
    configs = [
        SP500OpportunityBacktestConfig(
            buy_sizing_basis="available_cash",
            buy_thresholds=(60.0, 70.0, 80.0),
            buy_capital_pcts=(0.1, 0.2, pct),
            sell_thresholds=(20.0, 30.0, 40.0),
            sell_position_pcts=(0.4, 0.2, 0.1),
            commission_bps=0,
            slippage_bps=0,
        )
        for pct in (0.3, 0.4, 0.5)
    ]
    tasks = [_task("resume_test", config) for config in configs]
    store = StudyStore(tmp_path, {"study": "resume"})
    store.initialize(restart=False)

    partial, first_runs = run_stage(
        "resume_test",
        tasks,
        store,
        history,
        periods,
        workers=1,
        checkpoint_every=1,
        max_runs=2,
    )
    completed, second_runs = run_stage(
        "resume_test",
        tasks,
        store,
        history,
        periods,
        workers=1,
        checkpoint_every=1,
        max_runs=None,
    )

    assert first_runs == 2
    assert len(partial) == 2
    assert second_runs == 1
    assert len(completed) == 3
    assert completed["config_id"].is_unique
