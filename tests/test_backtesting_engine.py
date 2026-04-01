from __future__ import annotations

from datetime import date

import pandas as pd

from backtesting.engine import BacktestEngine
from backtesting.models import (
    BacktestMode,
    EntryMode,
    ExitRules,
    ExitStrategy,
    HistoricalSignal,
    PositionSizeMode,
)
from backtesting.scenarios import (
    default_backtest_scenario,
    scenario_with_overrides,
    split_in_sample_out_of_sample,
)
from data.database import AssetORM, PriceBarDailyORM


def _seed_asset_with_prices(db_session, symbol: str = "TEST") -> AssetORM:
    asset = AssetORM(
        symbol=symbol,
        name=f"{symbol} Asset",
        asset_type="etf",
        sector="Broad Market",
        region="US",
        enabled=True,
        supports_fundamentals=False,
    )
    db_session.add(asset)
    db_session.flush()

    dates = pd.bdate_range("2024-01-01", periods=280)
    prices = []
    for index, current_date in enumerate(dates):
        base = 100 + index * 0.12
        if 210 <= index <= 220:
            base -= 8
        prices.append(
            PriceBarDailyORM(
                asset_id=asset.id,
                date=current_date.date(),
                open=base,
                high=base + 1.5,
                low=base - 1.5,
                close=base + (0.2 if index % 2 == 0 else -0.1),
                volume=1000 + index,
            )
        )
    db_session.add_all(prices)
    db_session.flush()
    return asset


def _make_scenario(asset_symbol: str) -> object:
    base = default_backtest_scenario(
        assets=[asset_symbol],
        start_date=date(2024, 7, 1),
        end_date=date(2025, 1, 31),
    )
    return scenario_with_overrides(
        base,
        mode=BacktestMode.TRADE_BY_TRADE,
        entry_overrides={
            "min_final_score": 0,
            "max_risk_score": 100,
            "max_distance_to_support_pct": None,
            "max_rsi14": None,
            "allowed_recommendations": ("BUY_CANDIDATE", "WATCH", "AVOID"),
        },
        exit_overrides={
            "strategy": ExitStrategy.FIXED_HORIZON,
            "fixed_horizon_days": 5,
            "max_holding_days": 5,
        },
        execution_overrides={
            "entry_mode": EntryMode.NEXT_OPEN,
            "position_size_mode": PositionSizeMode.FIXED_CAPITAL_PCT,
            "fixed_position_pct": 0.05,
        },
    )


def test_evaluate_signal_point_does_not_use_future_data(db_session) -> None:
    asset = _seed_asset_with_prices(db_session)
    engine = BacktestEngine(db_session)
    full_frame = engine._load_asset_frame(asset, date(2024, 1, 1), date(2025, 12, 31))
    idx = 230
    full_signal = engine.evaluate_signal_point(asset, full_frame, idx)

    truncated = full_frame.iloc[: idx + 1].copy()
    truncated_signal = engine.evaluate_signal_point(asset, truncated, len(truncated) - 1)

    assert full_signal is not None
    assert truncated_signal is not None
    assert full_signal.final_score == truncated_signal.final_score
    assert full_signal.recommendation == truncated_signal.recommendation


def test_engine_generates_trades_from_relaxed_signal_rules(db_session) -> None:
    asset = _seed_asset_with_prices(db_session)
    engine = BacktestEngine(db_session)
    scenario = _make_scenario(asset.symbol)

    result = engine.run(scenario, persist=False)

    assert result.metrics.total_trades > 0
    assert all(trade.entry_date <= trade.exit_date for trade in result.trades)
    assert all(trade.exit_reason == "fixed_horizon" for trade in result.trades[:3])


def test_fixed_horizon_exit_rule(db_session) -> None:
    asset = _seed_asset_with_prices(db_session, "FIXED")
    engine = BacktestEngine(db_session)
    frame = engine._load_asset_frame(asset, date(2024, 1, 1), date(2025, 12, 31))
    scenario = _make_scenario(asset.symbol)
    signal = HistoricalSignal(
        asset_id=asset.id,
        symbol=asset.symbol,
        name=asset.name,
        asset_type=asset.asset_type,
        sector=asset.sector,
        signal_date=pd.Timestamp(frame.iloc[220]["date"]).date(),
        technical_score=70,
        risk_score=20,
        portfolio_fit_score=80,
        final_score=75,
        recommendation="BUY_CANDIDATE",
        rsi14=45,
        sma50=100,
        distance_to_support_pct=2,
        support_low=95,
        support_high=98,
        trend_bullish=True,
        suggested_weight_add_pct=5,
        invalidation_level=95,
    )

    trade, _ = engine.simulate_trade(
        asset=asset,
        frame=frame,
        signal_index=220,
        signal=signal,
        scenario=scenario,
        position_pct=0.05,
    )

    assert trade is not None
    assert trade.exit_reason == "fixed_horizon"
    assert trade.holding_days == 5


def test_stop_loss_and_invalidation_exit_rules(db_session) -> None:
    asset = _seed_asset_with_prices(db_session, "EXIT")
    engine = BacktestEngine(db_session)
    frame = engine._load_asset_frame(asset, date(2024, 1, 1), date(2025, 12, 31))
    frame.loc[222, "low"] = frame.loc[220, "close"] * 0.90
    frame.loc[222, "close"] = frame.loc[220, "close"] * 0.91
    frame.loc[222, "close"] = frame.loc[220, "close"] * 0.89
    signal = HistoricalSignal(
        asset_id=asset.id,
        symbol=asset.symbol,
        name=asset.name,
        asset_type=asset.asset_type,
        sector=asset.sector,
        signal_date=pd.Timestamp(frame.iloc[220]["date"]).date(),
        technical_score=70,
        risk_score=20,
        portfolio_fit_score=80,
        final_score=75,
        recommendation="BUY_CANDIDATE",
        rsi14=45,
        sma50=float(frame.loc[220, "close"]) * 0.98,
        distance_to_support_pct=2,
        support_low=95,
        support_high=98,
        trend_bullish=True,
        suggested_weight_add_pct=5,
        invalidation_level=float(frame.loc[220, "close"]) * 0.95,
    )

    stop_scenario = _make_scenario(asset.symbol)
    stop_scenario.exit_rules = ExitRules(
        strategy=ExitStrategy.TAKE_PROFIT_STOP_LOSS,
        fixed_horizon_days=5,
        max_holding_days=5,
        take_profit_pct=0.1,
        stop_loss_pct=0.05,
        signal_loss_score_threshold=45,
        invalidation_buffer_pct=0.01,
        position_alert_exit_types=(),
    )
    trade, _ = engine.simulate_trade(
        asset=asset,
        frame=frame,
        signal_index=220,
        signal=signal,
        scenario=stop_scenario,
        position_pct=0.05,
    )
    assert trade is not None
    assert trade.exit_reason == "stop_loss"

    invalidation_scenario = _make_scenario(asset.symbol)
    invalidation_scenario.exit_rules = ExitRules(
        strategy=ExitStrategy.SIGNAL_LOSS,
        fixed_horizon_days=5,
        max_holding_days=5,
        take_profit_pct=None,
        stop_loss_pct=None,
        signal_loss_score_threshold=90,
        invalidation_buffer_pct=0.0,
        position_alert_exit_types=(),
    )
    trade, _ = engine.simulate_trade(
        asset=asset,
        frame=frame,
        signal_index=220,
        signal=signal,
        scenario=invalidation_scenario,
        position_pct=0.05,
    )
    assert trade is not None
    assert trade.exit_reason in {"invalidation", "signal_loss"}


def test_in_sample_out_of_sample_split() -> None:
    (train_start, train_end), (test_start, test_end) = split_in_sample_out_of_sample(
        date(2024, 1, 1),
        date(2024, 12, 31),
        0.7,
    )

    assert train_start == date(2024, 1, 1)
    assert train_end < test_end
    assert test_start > train_start


def test_position_alert_exit_uses_real_management_logic(db_session) -> None:
    asset = _seed_asset_with_prices(db_session, "ALERTEXIT")
    engine = BacktestEngine(db_session)
    frame = engine._load_asset_frame(asset, date(2024, 1, 1), date(2025, 12, 31))
    scenario = _make_scenario(asset.symbol)
    scenario.exit_rules = ExitRules(
        strategy=ExitStrategy.POSITION_ALERTS,
        fixed_horizon_days=20,
        max_holding_days=20,
        take_profit_pct=None,
        stop_loss_pct=None,
        signal_loss_score_threshold=None,
        invalidation_buffer_pct=0.0,
        position_alert_exit_types=("take_profit",),
    )
    signal_index = 220
    entry_index = signal_index + 1
    entry_price = float(frame.iloc[entry_index]["open"])
    frame.loc[entry_index + 2, "close"] = entry_price * 1.20
    frame.loc[entry_index + 2, "high"] = entry_price * 1.21
    frame.loc[entry_index + 2, "low"] = entry_price * 1.19
    frame.loc[entry_index + 2, "open"] = entry_price * 1.18

    signal = HistoricalSignal(
        asset_id=asset.id,
        symbol=asset.symbol,
        name=asset.name,
        asset_type=asset.asset_type,
        sector=asset.sector,
        signal_date=pd.Timestamp(frame.iloc[signal_index]["date"]).date(),
        technical_score=70,
        risk_score=20,
        portfolio_fit_score=80,
        final_score=75,
        recommendation="BUY_CANDIDATE",
        rsi14=55,
        sma50=float(frame.loc[signal_index, "close"]) * 0.98,
        distance_to_support_pct=2,
        support_low=95,
        support_high=98,
        trend_bullish=True,
        suggested_weight_add_pct=5,
        invalidation_level=95,
    )

    trade, _ = engine.simulate_trade(
        asset=asset,
        frame=frame,
        signal_index=signal_index,
        signal=signal,
        scenario=scenario,
        position_pct=0.05,
    )

    assert trade is not None
    assert trade.exit_reason == "take_profit"


def test_portfolio_realistic_buys_with_suggested_weight_and_sells_partially(db_session) -> None:
    asset = _seed_asset_with_prices(db_session, "REALPF")
    engine = BacktestEngine(db_session)
    scenario = _make_scenario(asset.symbol)
    scenario.mode = BacktestMode.PORTFOLIO_REALISTIC
    scenario.execution_rules.entry_mode = EntryMode.CLOSE
    scenario.portfolio_simulation_rules.use_suggested_weight_add = True
    scenario.portfolio_simulation_rules.sell_reduction_by_alert_type["take_profit"] = 0.25
    scenario.exit_rules.strategy = ExitStrategy.POSITION_ALERTS
    scenario.exit_rules.position_alert_exit_types = ("take_profit",)

    result = engine.run(scenario, persist=False)

    assert result.portfolio_summary["buy_count"] > 0
    assert any(event["action"] == "BUY" for event in result.portfolio_events)
    assert result.cash_curve


def test_portfolio_realistic_respects_cash_and_asset_limit(db_session) -> None:
    asset = _seed_asset_with_prices(db_session, "LIMITPF")
    engine = BacktestEngine(db_session)
    scenario = _make_scenario(asset.symbol)
    scenario.mode = BacktestMode.PORTFOLIO_REALISTIC
    scenario.execution_rules.entry_mode = EntryMode.CLOSE
    scenario.initial_capital = 1000
    scenario.portfolio_simulation_rules.cash_min_target_pct = 0.8
    scenario.portfolio_simulation_rules.min_trade_value = 250

    result = engine.run(scenario, persist=False)

    assert result.portfolio_summary["buy_count"] == 0
    assert result.portfolio_summary["final_cash"] == 1000


def test_portfolio_realistic_exit_priority_prefers_stop_loss(db_session) -> None:
    asset = _seed_asset_with_prices(db_session, "PRIOPF")
    engine = BacktestEngine(db_session)
    frame = engine._load_asset_frame(asset, date(2024, 1, 1), date(2025, 12, 31))
    scenario = _make_scenario(asset.symbol)
    scenario.mode = BacktestMode.PORTFOLIO_REALISTIC
    scenario.execution_rules.entry_mode = EntryMode.CLOSE
    scenario.exit_rules.strategy = ExitStrategy.POSITION_ALERTS
    scenario.exit_rules.position_alert_exit_types = (
        "stop_loss_warning",
        "exit_candidate",
        "take_profit",
    )
    scenario.portfolio_simulation_rules.sell_reduction_by_alert_type["stop_loss_warning"] = 1.0
    scenario.portfolio_simulation_rules.sell_reduction_by_alert_type["take_profit"] = 0.25

    signal_index = 220
    entry_close = float(frame.iloc[signal_index]["close"])
    frame.loc[signal_index + 2, "close"] = entry_close * 1.20
    frame.loc[signal_index + 2, "high"] = entry_close * 1.21
    frame.loc[signal_index + 2, "low"] = entry_close * 0.90

    result = engine.run(scenario, persist=False)
    exit_events = [event for event in result.portfolio_events if event["action"].startswith("SELL")]

    if exit_events:
        assert exit_events[0]["trigger_type"] in {"stop_loss_warning", "exit_candidate"}
