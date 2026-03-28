from __future__ import annotations

from datetime import date

from backtesting.metrics import (
    compute_equity_curve,
    compute_expectancy,
    compute_max_drawdown,
    compute_profit_factor,
)
from backtesting.models import SimulatedTrade


def _trade(symbol: str, entry: date, exit: date, net_return_pct: float) -> SimulatedTrade:
    return SimulatedTrade(
        asset_id=1,
        symbol=symbol,
        name=symbol,
        asset_type="etf",
        sector="Broad Market",
        entry_signal_date=entry,
        entry_date=entry,
        exit_date=exit,
        entry_price=100,
        exit_price=100 * (1 + (net_return_pct / 100)),
        position_pct=1.0,
        gross_return_pct=net_return_pct,
        net_return_pct=net_return_pct,
        max_drawdown_pct=max(0.0, -net_return_pct),
        mae_pct=max(0.0, -net_return_pct),
        mfe_pct=max(0.0, net_return_pct),
        holding_days=5,
        exit_reason="fixed_horizon",
        recommendation="BUY_CANDIDATE",
        technical_score=70,
        risk_score=20,
        portfolio_fit_score=80,
        final_score=75,
        invalidation_level=None,
    )


def test_profit_factor_and_expectancy() -> None:
    trades = [
        _trade("A", date(2024, 1, 1), date(2024, 1, 5), 10),
        _trade("B", date(2024, 1, 6), date(2024, 1, 10), -5),
        _trade("C", date(2024, 1, 11), date(2024, 1, 15), 5),
    ]

    assert compute_profit_factor(trades) == 3.0
    assert round(compute_expectancy(trades), 2) == 3.33


def test_equity_curve_drawdown() -> None:
    trades = [
        _trade("A", date(2024, 1, 1), date(2024, 1, 5), 10),
        _trade("B", date(2024, 1, 6), date(2024, 1, 10), -20),
        _trade("C", date(2024, 1, 11), date(2024, 1, 15), 5),
    ]
    curve = compute_equity_curve(trades, initial_capital=1.0)

    assert len(curve) == 3
    assert round(compute_max_drawdown(curve), 2) == 20.0
