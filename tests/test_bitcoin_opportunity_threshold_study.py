from __future__ import annotations

import pandas as pd

from backtesting.bitcoin_opportunity import BitcoinOpportunityBacktestConfig
from jobs.run_bitcoin_opportunity_threshold_study import _period_results


def test_period_results_prefixes_annualized_metrics_once() -> None:
    history = pd.DataFrame(
        {
            "date": pd.date_range("2020-01-01", periods=10, freq="D"),
            "overall_score": [50, 66, 71, 76, 81, 50, 34, 29, 24, 19],
            "bitcoin_price": [100, 101, 102, 103, 104, 105, 104, 103, 102, 101],
        }
    )
    result = _period_results(
        history,
        BitcoinOpportunityBacktestConfig(
            buy_reset_threshold=60,
            sell_reset_threshold=40,
            commission_bps=0,
            slippage_bps=0,
        ),
        [65, 70, 75],
        [20, 25, 30],
        prefix="train",
    )

    assert "train_cagr_pct" in result.columns
    assert "train_benchmark_cagr_pct" in result.columns
    assert "train_train_cagr_pct" not in result.columns
