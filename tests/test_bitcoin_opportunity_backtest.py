from __future__ import annotations

import pandas as pd
import pytest

from backtesting.bitcoin_opportunity import (
    BitcoinOpportunityBacktestConfig,
    BitcoinOpportunityBacktester,
)


def _history() -> pd.DataFrame:
    scores = [50, 71, 76, 81, 50, 29, 24, 19, 18]
    return pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=len(scores), freq="D"),
            "overall_score": scores,
            "bitcoin_price": [100.0] * len(scores),
        }
    )


def test_executes_three_staged_buys_and_sales_on_next_day() -> None:
    config = BitcoinOpportunityBacktestConfig(
        initial_capital=100_000,
        buy_capital_pcts=(0.10, 0.20, 0.30),
        sell_position_pcts=(0.50, 0.30, 0.20),
        commission_bps=0,
        slippage_bps=0,
    )
    result = BitcoinOpportunityBacktester().run(_history(), config)

    assert result.buy_count == 3
    assert result.sell_count == 3
    assert result.final_equity == pytest.approx(100_000)
    assert result.final_quantity == pytest.approx(168)
    assert result.events.iloc[0]["signal_date"] == pd.Timestamp("2026-01-02")
    assert result.events.iloc[0]["execution_date"] == pd.Timestamp("2026-01-03")
    assert result.events["trigger_thresholds"].tolist() == ["70", "75", "80", "30", "25", "20"]


def test_combines_multiple_thresholds_crossed_on_same_day() -> None:
    history = pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=3, freq="D"),
            "overall_score": [50, 82, 82],
            "bitcoin_price": [100, 100, 100],
        }
    )
    config = BitcoinOpportunityBacktestConfig(
        buy_capital_pcts=(0.10, 0.20, 0.30),
        commission_bps=0,
        slippage_bps=0,
    )
    result = BitcoinOpportunityBacktester().run(history, config)

    assert result.buy_count == 1
    assert result.events.iloc[0]["applied_pct"] == pytest.approx(60)
    assert result.events.iloc[0]["trigger_thresholds"] == "70, 75, 80"


def test_optimizer_uses_monotonic_percentage_profiles() -> None:
    base = BitcoinOpportunityBacktestConfig(commission_bps=0, slippage_bps=0)
    results = BitcoinOpportunityBacktester().optimize_percentages(
        _history(), base, [0.10, 0.20]
    )

    assert len(results) == 16
    assert (results["buy_1_pct"] <= results["buy_2_pct"]).all()
    assert (results["buy_2_pct"] <= results["buy_3_pct"]).all()
    assert (results["sell_1_pct"] >= results["sell_2_pct"]).all()
    assert (results["sell_2_pct"] >= results["sell_3_pct"]).all()


def test_threshold_optimizer_evaluates_distinct_triplets() -> None:
    base = BitcoinOpportunityBacktestConfig(
        buy_capital_pcts=(0.10, 0.20, 0.40),
        sell_position_pcts=(0.10, 0.10, 0.10),
        commission_bps=0,
        slippage_bps=0,
    )
    results = BitcoinOpportunityBacktester().optimize_thresholds(
        _history(),
        base,
        buy_candidates=[65, 70, 75, 80],
        sell_candidates=[15, 20, 25, 30],
    )

    assert len(results) == 16
    assert (results["buy_threshold_1"] < results["buy_threshold_2"]).all()
    assert (results["buy_threshold_2"] < results["buy_threshold_3"]).all()
    assert (results["sell_threshold_1"] < results["sell_threshold_2"]).all()
    assert (results["sell_threshold_2"] < results["sell_threshold_3"]).all()


def test_threshold_does_not_repeat_until_cycle_is_rearmed() -> None:
    history = pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=8, freq="D"),
            "overall_score": [65, 71, 69, 71, 59, 71, 71, 71],
            "bitcoin_price": [100.0] * 8,
        }
    )
    result = BitcoinOpportunityBacktester().run(
        history,
        BitcoinOpportunityBacktestConfig(commission_bps=0, slippage_bps=0),
    )

    assert result.buy_count == 2
    assert result.events["trigger_thresholds"].tolist() == ["70", "70"]
