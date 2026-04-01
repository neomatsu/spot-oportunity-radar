from __future__ import annotations

import pandas as pd

from backtesting.models import BacktestRunResult, OptimizationResult, StrategyMetrics


def metrics_to_frame(metrics_map: dict[str, StrategyMetrics]) -> pd.DataFrame:
    if not metrics_map:
        return pd.DataFrame()
    rows = []
    for label, metrics in metrics_map.items():
        row = {"segment": label}
        row.update(metrics.to_dict())
        rows.append(row)
    return pd.DataFrame(rows).sort_values("total_trades", ascending=False)


def run_result_to_frames(result: BacktestRunResult) -> dict[str, pd.DataFrame]:
    trades_frame = pd.DataFrame(
        [
            {
                "symbol": trade.symbol,
                "asset_type": trade.asset_type,
                "sector": trade.sector,
                "entry_signal_date": trade.entry_signal_date,
                "entry_date": trade.entry_date,
                "exit_date": trade.exit_date,
                "entry_price": trade.entry_price,
                "exit_price": trade.exit_price,
                "gross_return_pct": trade.gross_return_pct,
                "net_return_pct": trade.net_return_pct,
                "holding_days": trade.holding_days,
                "max_drawdown_pct": trade.max_drawdown_pct,
                "mfe_pct": trade.mfe_pct,
                "mae_pct": trade.mae_pct,
                "technical_score": trade.technical_score,
                "risk_score": trade.risk_score,
                "portfolio_fit_score": trade.portfolio_fit_score,
                "final_score": trade.final_score,
                "recommendation": trade.recommendation,
                "exit_reason": trade.exit_reason,
            }
            for trade in result.trades
        ]
    )
    equity_frame = pd.DataFrame(result.equity_curve)
    cash_frame = pd.DataFrame(result.cash_curve)
    portfolio_events_frame = pd.DataFrame(result.portfolio_events)
    segment_frames = {
        segment: metrics_to_frame(metrics_map)
        for segment, metrics_map in result.segmented_metrics.items()
    }
    return {
        "trades": trades_frame,
        "equity": equity_frame,
        "cash": cash_frame,
        "portfolio_events": portfolio_events_frame,
        **{f"segment_{segment}": frame for segment, frame in segment_frames.items()},
    }


def optimization_results_frame(results: list[OptimizationResult]) -> pd.DataFrame:
    if not results:
        return pd.DataFrame()
    rows = []
    for result in results:
        row = {
            "parameter_set_id": result.parameter_set_id,
            "evaluation_score": result.evaluation_score,
            "warning": result.warning,
        }
        row.update({f"param_{key}": value for key, value in result.parameters.items()})
        row.update(
            {
                f"in_{key}": value
                for key, value in result.in_sample_metrics.to_dict().items()
            }
        )
        row.update(
            {
                f"out_{key}": value
                for key, value in result.out_of_sample_metrics.to_dict().items()
            }
        )
        rows.append(row)
    return pd.DataFrame(rows).sort_values("evaluation_score", ascending=False)
