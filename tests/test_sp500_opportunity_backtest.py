from __future__ import annotations

import pandas as pd
import pytest

from backtesting.sp500_opportunity import (
    SP500OpportunityBacktestConfig,
    SP500OpportunityBacktester,
)
from core.config import load_yaml_config


def _history() -> pd.DataFrame:
    scores = [50, 66, 76, 86, 50, 44, 34, 24, 20]
    return pd.DataFrame(
        {
            "date": pd.date_range("2020-01-01", periods=len(scores), freq="B"),
            "overall_score": scores,
            "sp500_price": [100.0] * len(scores),
        }
    )


def test_sp500_backtest_reuses_next_session_execution_and_adds_metrics() -> None:
    config = SP500OpportunityBacktestConfig(
        commission_bps=0,
        slippage_bps=0,
        buy_sizing_basis="available_cash",
        buy_capital_pcts=(0.10, 0.20, 0.30),
        sell_position_pcts=(0.40, 0.30, 0.20),
    )
    result = SP500OpportunityBacktester().run(_history(), config)

    assert result.buy_count == 3
    assert result.sell_count == 3
    assert result.final_equity == pytest.approx(100_000)
    assert result.events.iloc[0]["execution_date"] > result.events.iloc[0]["signal_date"]
    assert result.cagr_pct == pytest.approx(0)
    assert 0 <= result.average_exposure_pct <= 100
    assert result.turnover_pct > 0
    buy_sizing = result.events.loc[
        result.events["action"] == "BUY", "sizing_basis"
    ]
    assert (buy_sizing == "available_cash").all()


def test_sensitivity_and_oos_keep_multiple_configs_visible() -> None:
    history = pd.concat([_history()] * 80, ignore_index=True)
    history["date"] = pd.date_range("2010-01-01", periods=len(history), freq="B")
    backtester = SP500OpportunityBacktester()
    base = SP500OpportunityBacktestConfig(commission_bps=0, slippage_bps=0)
    sensitivity = backtester.sensitivity_analysis(
        history,
        base,
        threshold_offsets=[-5, 0, 5],
        sizing_multipliers=[0.75, 1.0],
    )

    assert len(sensitivity) == 6
    oos = backtester.in_sample_out_of_sample(
        history,
        [base],
        split_date=pd.Timestamp("2011-06-01").date(),
    )
    assert len(oos) == 1
    assert {"train_robustness_score", "test_robustness_score", "robustness_gap"}.issubset(
        oos.columns
    )


def test_ui_defaults_match_latest_robust_optimization_winner() -> None:
    defaults = load_yaml_config("sp500_opportunity.yaml")["backtesting"]

    assert defaults["default_profile_config_id"] == "bb999ff432dfd479"
    assert defaults["buy_thresholds"] == [60, 62.5, 77.5]
    assert defaults["buy_capital_pcts"] == [0.50, 0.50, 0.50]
    assert defaults["sell_thresholds"] == [25, 40, 45]
    assert defaults["sell_position_pcts"] == [0.50, 0.20, 0.20]
