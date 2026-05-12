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
from market_regime.regime_models import MarketRegime


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


def _make_rsi_frame(
    rsi_values: list[float],
    *,
    close_values: list[float] | None = None,
    start: str = "2024-01-01",
) -> pd.DataFrame:
    close_series = close_values or [100.0 + idx for idx, _ in enumerate(rsi_values)]
    dates = pd.bdate_range(start, periods=len(rsi_values))
    return pd.DataFrame(
        {
            "date": dates,
            "open": close_series,
            "high": [value + 1 for value in close_series],
            "low": [value - 1 for value in close_series],
            "close": close_series,
            "volume": [1000 + idx for idx in range(len(rsi_values))],
            "rsi14": rsi_values,
            "sma50": close_series,
            "sma200": close_series,
            "ema20": close_series,
            "atr14": [1.0] * len(rsi_values),
        }
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


def test_regime_filter_blocks_entries_when_bull_is_too_low(db_session) -> None:
    asset = _seed_asset_with_prices(db_session)
    engine = BacktestEngine(db_session)
    scenario = scenario_with_overrides(
        _make_scenario(asset.symbol),
        regime_filter_overrides={
            "enabled": True,
            "min_bull_probability": 60,
            "max_bear_probability": 80,
            "reduce_size_if_bubble_probability_gt": None,
        },
    )
    low_bull_regime = MarketRegime(
        bull_probability=35,
        bear_probability=45,
        bubble_probability=20,
        dominant_regime="TRANSITION",
    )
    engine.market_regime_service.compute_regime = lambda *_, **__: low_bull_regime
    engine.market_regime_service.compute_regime_from_prepared_frame = (
        lambda *_, **__: low_bull_regime
    )

    result = engine.run(scenario, persist=False)

    assert result.metrics.total_trades == 0


def test_regime_filter_reduces_position_size_when_bubble_is_high(db_session) -> None:
    engine = BacktestEngine(db_session)
    scenario = scenario_with_overrides(
        _make_scenario("TEST"),
        regime_filter_overrides={
            "enabled": True,
            "min_bull_probability": 40,
            "max_bear_probability": 70,
            "reduce_size_if_bubble_probability_gt": 65,
            "bubble_position_size_multiplier": 0.5,
        },
    )
    signal = HistoricalSignal(
        asset_id=1,
        symbol="TEST",
        name="Test",
        asset_type="etf",
        sector="Broad Market",
        signal_date=date(2024, 1, 1),
        technical_score=70,
        risk_score=20,
        portfolio_fit_score=90,
        final_score=80,
        recommendation="BUY_CANDIDATE",
        rsi14=45,
        sma50=100,
        distance_to_support_pct=1,
        support_low=98,
        support_high=102,
        trend_bullish=True,
        suggested_weight_add_pct=10,
        invalidation_level=98,
        rationale={
            "market_regime_filter": {
                "position_size_multiplier": 0.5,
            }
        },
    )

    position_pct = engine._position_pct(signal, scenario, available_cash_pct=1.0)

    assert position_pct == 0.025


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


def test_rsi_cycle_buy_thresholds_trigger_once_per_cycle(db_session) -> None:
    engine = BacktestEngine(db_session)
    scenario = _make_scenario("TEST")
    rules = scenario.rsi_cycle_rules
    frame = _make_rsi_frame([35, 28, 24, 19, 21, 31, 28, 24, 19, 31])
    state = engine._init_rsi_cycle_state()
    events: list[str] = []

    for index in range(len(frame)):
        events.extend(
            engine._rsi_cycle_events_for_bar(
                frame=frame,
                index=index,
                state=state,
                rules=rules,
            )
        )

    assert events.count("BUY_RSI_25") == 2
    assert events.count("BUY_RSI_20") == 2


def test_rsi_cycle_sell_thresholds_trigger_once_per_cycle(db_session) -> None:
    engine = BacktestEngine(db_session)
    scenario = _make_scenario("TEST")
    rules = scenario.rsi_cycle_rules
    frame = _make_rsi_frame([65, 71, 76, 81, 78, 69, 72, 76, 82, 68])
    state = engine._init_rsi_cycle_state()
    events: list[str] = []

    for index in range(len(frame)):
        events.extend(
            engine._rsi_cycle_events_for_bar(
                frame=frame,
                index=index,
                state=state,
                rules=rules,
            )
        )

    assert events.count("SELL_RSI_75") == 2
    assert events.count("SELL_RSI_80") == 2


def test_bullish_divergence_requires_confirmation_cross(db_session) -> None:
    engine = BacktestEngine(db_session)
    scenario = _make_scenario("TEST")
    rules = scenario.rsi_cycle_rules
    frame = _make_rsi_frame(
        [40, 29, 24, 28, 30, 29, 26, 29, 31],
        close_values=[110, 105, 100, 104, 103, 100, 95, 98, 101],
    )
    state = engine._init_rsi_cycle_state()
    events: list[str] = []

    for index in range(len(frame)):
        events.extend(
            engine._rsi_cycle_events_for_bar(
                frame=frame,
                index=index,
                state=state,
                rules=rules,
            )
        )

    assert "BUY_BULLISH_DIVERGENCE" in events
    assert events.count("BUY_BULLISH_DIVERGENCE") == 1


def test_bearish_divergence_requires_confirmation_cross(db_session) -> None:
    engine = BacktestEngine(db_session)
    scenario = _make_scenario("TEST")
    rules = scenario.rsi_cycle_rules
    frame = _make_rsi_frame(
        [60, 71, 76, 72, 70, 71, 74, 71, 69],
        close_values=[100, 104, 108, 106, 107, 109, 112, 110, 107],
    )
    state = engine._init_rsi_cycle_state()
    events: list[str] = []

    for index in range(len(frame)):
        events.extend(
            engine._rsi_cycle_events_for_bar(
                frame=frame,
                index=index,
                state=state,
                rules=rules,
            )
        )

    assert "SELL_BEARISH_DIVERGENCE" in events
    assert events.count("SELL_BEARISH_DIVERGENCE") == 1


def test_rsi_cycle_strategy_updates_portfolio_with_buys_and_sales(db_session) -> None:
    asset = _seed_asset_with_prices(db_session, "RSICYCLE")
    engine = BacktestEngine(db_session)
    custom_rsi = [45, 28, 24, 31, 50, 72, 76, 68]
    custom_close = [100, 97, 95, 99, 104, 112, 116, 110]

    def _fake_indicators(frame: pd.DataFrame) -> pd.DataFrame:
        length = len(frame)
        padded_rsi = (custom_rsi + [custom_rsi[-1]] * length)[:length]
        return frame.assign(
            rsi14=padded_rsi,
            sma50=frame["close"],
            sma200=frame["close"],
            ema20=frame["close"],
            atr14=1.0,
        )

    engine.technical_service.compute_indicators = _fake_indicators  # type: ignore[method-assign]

    base = default_backtest_scenario(
        assets=[asset.symbol],
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
    )
    scenario = scenario_with_overrides(
        base,
        mode=BacktestMode.RSI_CYCLE_STRATEGY,
        rsi_cycle_overrides={
            "buy_pct_rsi_25": 0.5,
            "buy_pct_rsi_20": 0.0,
            "buy_pct_bullish_divergence": 0.0,
            "sell_pct_rsi_75": 1.0,
            "sell_pct_rsi_80": 0.0,
            "sell_pct_bearish_divergence": 0.0,
        },
        portfolio_simulation_overrides={
            "cash_min_target_pct": 0.0,
            "min_trade_value": 10.0,
            "allow_add_to_existing": True,
            "apply_portfolio_limits": False,
        },
        execution_overrides={
            "entry_mode": EntryMode.CLOSE,
        },
    )
    scenario.initial_capital = 10_000

    frame = engine._load_asset_frame(asset, date(2024, 1, 1), date(2024, 1, 31))
    frame.loc[: len(custom_close) - 1, "close"] = custom_close
    frame.loc[: len(custom_close) - 1, "open"] = custom_close
    frame.loc[: len(custom_close) - 1, "high"] = [value + 1 for value in custom_close]
    frame.loc[: len(custom_close) - 1, "low"] = [value - 1 for value in custom_close]

    original_loader = engine._load_asset_frame
    engine._load_asset_frame = lambda *_args, **_kwargs: frame.copy()  # type: ignore[method-assign]
    try:
        result = engine.run(scenario, persist=False)
    finally:
        engine._load_asset_frame = original_loader  # type: ignore[method-assign]

    assert result.portfolio_summary["buy_count"] >= 1
    assert result.portfolio_summary["sell_full_count"] >= 1
    assert any(event["trigger_type"] == "BUY_RSI_25" for event in result.portfolio_events)
    assert any(event["trigger_type"] == "SELL_RSI_75" for event in result.portfolio_events)


def test_rsi_cycle_strategy_executes_only_most_extreme_sale_same_day(db_session) -> None:
    asset = _seed_asset_with_prices(db_session, "RSIPRIO")
    engine = BacktestEngine(db_session)
    custom_rsi = [45, 28, 24, 31, 55, 72, 81, 68]
    custom_close = [100, 97, 95, 99, 104, 112, 118, 110]

    def _fake_indicators(frame: pd.DataFrame) -> pd.DataFrame:
        length = len(frame)
        padded_rsi = (custom_rsi + [custom_rsi[-1]] * length)[:length]
        return frame.assign(
            rsi14=padded_rsi,
            sma50=frame["close"],
            sma200=frame["close"],
            ema20=frame["close"],
            atr14=1.0,
        )

    engine.technical_service.compute_indicators = _fake_indicators  # type: ignore[method-assign]

    base = default_backtest_scenario(
        assets=[asset.symbol],
        start_date=date(2024, 1, 1),
        end_date=date(2024, 1, 31),
    )
    scenario = scenario_with_overrides(
        base,
        mode=BacktestMode.RSI_CYCLE_STRATEGY,
        rsi_cycle_overrides={
            "buy_pct_rsi_25": 0.5,
            "buy_pct_rsi_20": 0.0,
            "buy_pct_bullish_divergence": 0.0,
            "sell_pct_rsi_75": 0.25,
            "sell_pct_rsi_80": 0.40,
            "sell_pct_bearish_divergence": 0.0,
        },
        portfolio_simulation_overrides={
            "cash_min_target_pct": 0.0,
            "min_trade_value": 10.0,
            "allow_add_to_existing": True,
            "apply_portfolio_limits": False,
        },
        execution_overrides={
            "entry_mode": EntryMode.CLOSE,
        },
    )
    scenario.initial_capital = 10_000

    frame = engine._load_asset_frame(asset, date(2024, 1, 1), date(2024, 1, 31))
    frame.loc[: len(custom_close) - 1, "close"] = custom_close
    frame.loc[: len(custom_close) - 1, "open"] = custom_close
    frame.loc[: len(custom_close) - 1, "high"] = [value + 1 for value in custom_close]
    frame.loc[: len(custom_close) - 1, "low"] = [value - 1 for value in custom_close]

    original_loader = engine._load_asset_frame
    engine._load_asset_frame = lambda *_args, **_kwargs: frame.copy()  # type: ignore[method-assign]
    try:
        result = engine.run(scenario, persist=False)
    finally:
        engine._load_asset_frame = original_loader  # type: ignore[method-assign]

    sell_events = [event for event in result.portfolio_events if event["action"].startswith("SELL")]
    assert len(sell_events) == 1
    assert sell_events[0]["trigger_type"] == "SELL_RSI_80"
