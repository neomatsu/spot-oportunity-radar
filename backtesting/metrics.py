from __future__ import annotations

from collections import defaultdict
from statistics import median

import numpy as np

from backtesting.models import SimulatedTrade, StrategyMetrics


def compute_profit_factor(trades: list[SimulatedTrade]) -> float:
    gains = sum(max(trade.net_return_pct, 0.0) for trade in trades)
    losses = abs(sum(min(trade.net_return_pct, 0.0) for trade in trades))
    if losses == 0:
        return gains if gains > 0 else 0.0
    return gains / losses


def compute_expectancy(trades: list[SimulatedTrade]) -> float:
    if not trades:
        return 0.0
    return sum(trade.net_return_pct for trade in trades) / len(trades)


def compute_equity_curve(
    trades: list[SimulatedTrade],
    *,
    initial_capital: float = 1.0,
) -> list[dict[str, float | str]]:
    ordered = sorted(trades, key=lambda trade: (trade.exit_date, trade.entry_date, trade.symbol))
    equity = initial_capital
    curve: list[dict[str, float | str]] = []

    for trade in ordered:
        weighted_return = (trade.position_pct or 1.0) * (trade.net_return_pct / 100)
        equity *= 1 + weighted_return
        curve.append(
            {
                "date": trade.exit_date.isoformat(),
                "equity": round(equity, 6),
                "symbol": trade.symbol,
                "net_return_pct": round(trade.net_return_pct, 4),
            }
        )
    return curve


def compute_max_drawdown(equity_curve: list[dict[str, float | str]]) -> float:
    if not equity_curve:
        return 0.0
    values = np.array([float(point["equity"]) for point in equity_curve], dtype=float)
    peaks = np.maximum.accumulate(values)
    drawdowns = (values / peaks) - 1
    return abs(float(drawdowns.min())) * 100


def summarize_trades(
    trades: list[SimulatedTrade],
    *,
    initial_capital: float = 1.0,
) -> StrategyMetrics:
    if not trades:
        return StrategyMetrics(
            total_trades=0,
            win_rate_pct=0.0,
            avg_return_pct=0.0,
            median_return_pct=0.0,
            profit_factor=0.0,
            expectancy_pct=0.0,
            max_drawdown_pct=0.0,
            best_trade_pct=0.0,
            worst_trade_pct=0.0,
            avg_holding_days=0.0,
            return_to_drawdown=0.0,
            sharpe_like=0.0,
            total_net_return_pct=0.0,
            trades_per_month=0.0,
        )

    returns = [trade.net_return_pct for trade in trades]
    equity_curve = compute_equity_curve(trades, initial_capital=initial_capital)
    max_drawdown = compute_max_drawdown(equity_curve)
    total_months = max(
        1 / 12,
        (
            (
                max(trade.exit_date for trade in trades)
                - min(trade.entry_date for trade in trades)
            ).days
            + 1
        )
        / 30.5,
    )
    win_rate = (sum(1 for trade in trades if trade.net_return_pct > 0) / len(trades)) * 100
    sharpe_like = 0.0
    if len(returns) > 1 and np.std(returns) > 0:
        sharpe_like = float(np.mean(returns) / np.std(returns))

    total_net_return_pct = ((equity_curve[-1]["equity"] / initial_capital) - 1) * 100
    return_to_drawdown = (
        total_net_return_pct / max_drawdown if max_drawdown > 0 else total_net_return_pct
    )

    return StrategyMetrics(
        total_trades=len(trades),
        win_rate_pct=round(win_rate, 2),
        avg_return_pct=round(float(np.mean(returns)), 2),
        median_return_pct=round(float(median(returns)), 2),
        profit_factor=round(compute_profit_factor(trades), 2),
        expectancy_pct=round(compute_expectancy(trades), 2),
        max_drawdown_pct=round(max_drawdown, 2),
        best_trade_pct=round(max(returns), 2),
        worst_trade_pct=round(min(returns), 2),
        avg_holding_days=round(float(np.mean([trade.holding_days for trade in trades])), 2),
        return_to_drawdown=round(return_to_drawdown, 2),
        sharpe_like=round(sharpe_like, 2),
        total_net_return_pct=round(total_net_return_pct, 2),
        trades_per_month=round(len(trades) / total_months, 2),
    )


def segment_trades(
    trades: list[SimulatedTrade],
    *,
    initial_capital: float = 1.0,
) -> dict[str, dict[str, StrategyMetrics]]:
    segments: dict[str, dict[str, list[SimulatedTrade]]] = {
        "symbol": defaultdict(list),
        "asset_type": defaultdict(list),
        "sector": defaultdict(list),
        "recommendation": defaultdict(list),
        "score_band": defaultdict(list),
        "risk_band": defaultdict(list),
    }

    for trade in trades:
        segments["symbol"][trade.symbol].append(trade)
        segments["asset_type"][trade.asset_type].append(trade)
        segments["sector"][trade.sector].append(trade)
        segments["recommendation"][trade.recommendation].append(trade)
        segments["score_band"][trade.score_band].append(trade)
        segments["risk_band"][trade.risk_band].append(trade)

    return {
        segment_type: {
            segment_value: summarize_trades(segment_trades, initial_capital=initial_capital)
            for segment_value, segment_trades in segment_map.items()
        }
        for segment_type, segment_map in segments.items()
    }
