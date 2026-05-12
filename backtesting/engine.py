from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

import pandas as pd
from sqlalchemy.orm import Session

from backtesting.metrics import compute_equity_curve, segment_trades, summarize_trades
from backtesting.models import (
    BacktestMode,
    BacktestRunResult,
    BacktestScenario,
    HistoricalSignal,
    SimulatedTrade,
)
from core.models import PortfolioExposureModel, TechnicalSnapshotModel
from data.database import AssetORM
from data.repositories.assets_repo import AssetsRepository
from data.repositories.backtest_repo import BacktestRepository
from data.repositories.prices_repo import PricesRepository
from market_regime.regime_models import MarketRegime
from market_regime.regime_service import MarketRegimeService
from services.position_management_alerts_service import (
    PositionContext,
    PositionManagementAlertsService,
)
from services.rebalance_service import RebalanceService
from services.recommendation_service import RecommendationService
from services.risk_service import RiskService
from services.scoring_service import ScoringService
from services.support_detection_service import SupportDetectionService
from services.technical_service import TechnicalService


class BacktestEngine:
    """Motor de backtesting basado en recalculo historico barra a barra."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.assets_repo = AssetsRepository(session)
        self.prices_repo = PricesRepository(session)
        self.backtest_repo = BacktestRepository(session)
        self.technical_service = TechnicalService()
        self.support_service = SupportDetectionService()
        self.risk_service = RiskService()
        self.scoring_service = ScoringService()
        self.rebalance_service = RebalanceService()
        self.recommendation_service = RecommendationService()
        self.position_management_alerts_service = PositionManagementAlertsService()
        self.market_regime_service = MarketRegimeService()
        self._market_regime_cache: dict[tuple[int, date], MarketRegime] = {}
        self._market_regime_prepared_frames: dict[int, pd.DataFrame] = {}

    def run(
        self,
        scenario: BacktestScenario,
        *,
        persist: bool = True,
        run_name: str | None = None,
    ) -> BacktestRunResult:
        self.scoring_service = (
            ScoringService(overrides=scenario.scoring_overrides)
            if scenario.scoring_overrides
            else ScoringService()
        )
        assets = self._load_assets(scenario.assets)
        frames = {
            asset.id: self._load_asset_frame(asset, scenario.start_date, scenario.end_date)
            for asset in assets
        }
        if scenario.regime_filter_rules.enabled:
            self._market_regime_prepared_frames.update(
                {
                    asset.id: self.market_regime_service._prepare_frame(frames[asset.id])
                    for asset in assets
                    if asset.id not in self._market_regime_prepared_frames
                    and not frames[asset.id].empty
                }
            )
        portfolio_events: list[dict[str, Any]] = []
        portfolio_summary: dict[str, Any] = {}
        cash_curve: list[dict[str, Any]] = []
        equity_curve: list[dict[str, Any]] = []

        if scenario.mode == BacktestMode.PORTFOLIO_REALISTIC:
            (
                trades,
                portfolio_events,
                equity_curve,
                cash_curve,
                portfolio_summary,
            ) = self._run_portfolio_realistic_mode(assets, frames, scenario)
        elif scenario.mode == BacktestMode.RSI_CYCLE_STRATEGY:
            (
                trades,
                portfolio_events,
                equity_curve,
                cash_curve,
                portfolio_summary,
            ) = self._run_rsi_cycle_strategy_mode(assets, frames, scenario)
        elif scenario.mode == BacktestMode.PORTFOLIO:
            trades = self._run_portfolio_mode(assets, frames, scenario)
        else:
            trades = self._run_trade_by_trade_mode(assets, frames, scenario)

        metrics = summarize_trades(trades, initial_capital=scenario.initial_capital)
        segmented = segment_trades(trades, initial_capital=scenario.initial_capital)
        if not equity_curve:
            equity_curve = compute_equity_curve(trades, initial_capital=scenario.initial_capital)
        warnings = self._build_warnings(metrics.total_trades, scenario)

        run_id: int | None = None
        parameter_set_id: int | None = None
        if persist:
            run_id, parameter_set_id = self._persist_result(
                scenario,
                trades,
                metrics.to_dict(),
                segmented,
                portfolio_events=portfolio_events,
                portfolio_summary=portfolio_summary,
                run_name=run_name or scenario.name,
            )

        return BacktestRunResult(
            run_id=run_id,
            parameter_set_id=parameter_set_id,
            scenario=scenario,
            trades=trades,
            metrics=metrics,
            segmented_metrics=segmented,
            equity_curve=equity_curve,
            warnings=warnings,
            cash_curve=cash_curve,
            portfolio_events=portfolio_events,
            portfolio_summary=portfolio_summary,
        )

    def evaluate_signal_point(
        self,
        asset: AssetORM,
        frame: pd.DataFrame,
        index: int,
        exposure: PortfolioExposureModel | None = None,
    ) -> HistoricalSignal | None:
        historical = frame.iloc[: index + 1].copy()
        if historical.empty or len(historical) < 30:
            return None

        enriched = self.technical_service.compute_indicators(historical)
        latest = enriched.iloc[-1]
        support = self.support_service.detect_support_zone(enriched)
        trend_score, trend_rationale = self.technical_service.trend_structure_score(latest)
        technical_score, technical_rationale = self.scoring_service.compute_technical_score(
            latest,
            distance_to_support_pct=support.distance_to_support_pct,
            trend_score=trend_score,
        )

        current_exposure = exposure or PortfolioExposureModel(
            total_invested_weight=0.0,
            by_asset={},
            by_sector={},
            by_asset_type={},
        )
        risk = self.risk_service.assess_risk(
            historical,
            asset_type=asset.asset_type,
            current_asset_weight=current_exposure.by_asset.get(asset.symbol, 0.0),
            current_sector_weight=current_exposure.by_sector.get(asset.sector, 0.0),
            current_asset_type_weight=current_exposure.by_asset_type.get(asset.asset_type, 0.0),
        )
        portfolio_fit_score, portfolio_fit_rationale = self.rebalance_service.portfolio_fit_score(
            asset,
            current_exposure,
        )
        score_breakdown = self.scoring_service.compute_final_score_details(
            technical_score=technical_score,
            risk_score=risk.risk_score,
            portfolio_fit_score=portfolio_fit_score,
        )
        technical_snapshot = TechnicalSnapshotModel(
            asset_id=asset.id,
            date=pd.Timestamp(latest["date"]).date(),
            rsi14=self._optional_float(latest.get("rsi14")),
            sma50=self._optional_float(latest.get("sma50")),
            sma200=self._optional_float(latest.get("sma200")),
            ema20=self._optional_float(latest.get("ema20")),
            atr14=self._optional_float(latest.get("atr14")),
            distance_52w_high_pct=self._optional_float(latest.get("distance_52w_high_pct")),
            distance_52w_low_pct=self._optional_float(latest.get("distance_52w_low_pct")),
            support_low=support.support_zone_low,
            support_high=support.support_zone_high,
            distance_to_support_pct=support.distance_to_support_pct,
            technical_score=technical_score,
            rationale={
                "trend": trend_rationale,
                **technical_rationale,
                "portfolio_fit": portfolio_fit_rationale,
            },
        )
        recommendation = self.recommendation_service.build_recommendation(
            asset_id=asset.id,
            technical_snapshot=technical_snapshot,
            risk=risk,
            final_score=score_breakdown["final"],
            portfolio_fit_score=portfolio_fit_score,
            score_breakdown=score_breakdown,
        )

        sma50 = latest.get("sma50")
        sma200 = latest.get("sma200")
        trend_bullish = bool(
            pd.notna(sma50) and pd.notna(sma200) and float(sma50) > float(sma200)
        )
        return HistoricalSignal(
            asset_id=asset.id,
            symbol=asset.symbol,
            name=asset.name,
            asset_type=asset.asset_type,
            sector=asset.sector,
            signal_date=pd.Timestamp(latest["date"]).date(),
            technical_score=round(technical_score, 2),
            risk_score=round(risk.risk_score, 2),
            portfolio_fit_score=round(portfolio_fit_score, 2),
            final_score=score_breakdown["final"],
            recommendation=recommendation.recommendation.value,
            rsi14=self._optional_float(latest.get("rsi14")),
            sma50=self._optional_float(latest.get("sma50")),
            distance_to_support_pct=support.distance_to_support_pct,
            support_low=support.support_zone_low,
            support_high=support.support_zone_high,
            trend_bullish=trend_bullish,
            suggested_weight_add_pct=round(recommendation.suggested_weight_add, 2),
            invalidation_level=technical_snapshot.support_low,
            rationale={
                "technical": technical_snapshot.rationale,
                "risk": risk.rationale,
                "recommendation": recommendation.rationale_json,
                "portfolio_fit": portfolio_fit_rationale,
            },
            score_breakdown=score_breakdown,
        )

    def simulate_trade(
        self,
        *,
        asset: AssetORM,
        frame: pd.DataFrame,
        signal_index: int,
        signal: HistoricalSignal,
        scenario: BacktestScenario,
        position_pct: float,
        future_exposure: PortfolioExposureModel | None = None,
    ) -> tuple[SimulatedTrade | None, int | None]:
        entry_index = signal_index
        if scenario.execution_rules.entry_mode.value == "next_open":
            entry_index += 1
        if entry_index >= len(frame):
            return None, None

        entry_row = frame.iloc[entry_index]
        entry_date = pd.Timestamp(entry_row["date"]).date()
        entry_price_raw = float(
            entry_row["close"]
            if scenario.execution_rules.entry_mode.value == "close_signal_day"
            else entry_row["open"]
        )
        entry_price = self._apply_slippage(
            entry_price_raw,
            scenario.execution_rules.slippage_bps,
            side="buy",
        )
        exit_trade, exit_index = self._resolve_exit(
            asset=asset,
            frame=frame,
            signal=signal,
            signal_index=signal_index,
            entry_index=entry_index,
            entry_price=entry_price,
            position_pct=position_pct,
            scenario=scenario,
            future_exposure=future_exposure,
        )
        if exit_trade is None or exit_index is None:
            return None, None

        commission_pct = (2 * scenario.execution_rules.commission_bps) / 100
        gross_return_pct = ((exit_trade["exit_price"] / entry_price) - 1) * 100
        net_return_pct = gross_return_pct - commission_pct
        trade = SimulatedTrade(
            asset_id=asset.id,
            symbol=asset.symbol,
            name=asset.name,
            asset_type=asset.asset_type,
            sector=asset.sector,
            entry_signal_date=signal.signal_date,
            entry_date=entry_date,
            exit_date=exit_trade["exit_date"],
            entry_price=round(entry_price, 4),
            exit_price=round(exit_trade["exit_price"], 4),
            position_pct=round(position_pct, 4),
            gross_return_pct=round(gross_return_pct, 2),
            net_return_pct=round(net_return_pct, 2),
            max_drawdown_pct=round(exit_trade["max_drawdown_pct"], 2),
            mae_pct=round(exit_trade["mae_pct"], 2),
            mfe_pct=round(exit_trade["mfe_pct"], 2),
            holding_days=int(exit_trade["holding_days"]),
            exit_reason=str(exit_trade["exit_reason"]),
            recommendation=signal.recommendation,
            technical_score=signal.technical_score,
            risk_score=signal.risk_score,
            portfolio_fit_score=signal.portfolio_fit_score,
            final_score=signal.final_score,
            invalidation_level=signal.invalidation_level,
            rationale=signal.rationale,
            parameters=scenario.to_payload(),
        )
        return trade, exit_index

    def _run_trade_by_trade_mode(
        self,
        assets: list[AssetORM],
        frames: dict[int, pd.DataFrame],
        scenario: BacktestScenario,
    ) -> list[SimulatedTrade]:
        trades: list[SimulatedTrade] = []
        next_available_index: dict[int, int] = defaultdict(lambda: -1)
        empty_exposure = PortfolioExposureModel(
            total_invested_weight=0.0,
            by_asset={},
            by_sector={},
            by_asset_type={},
        )

        for asset in assets:
            frame = frames[asset.id]
            for index in range(len(frame)):
                trade_date = pd.Timestamp(frame.iloc[index]["date"]).date()
                if trade_date < scenario.start_date or trade_date > scenario.end_date:
                    continue
                if (
                    not scenario.allow_overlapping_per_asset
                    and index <= next_available_index[asset.id]
                ):
                    continue
                signal = self.evaluate_signal_point(asset, frame, index, exposure=empty_exposure)
                if signal is None or not self._entry_allowed(
                    signal,
                    scenario,
                    asset=asset,
                    frame=frame,
                    index=index,
                ):
                    continue

                trade, exit_index = self.simulate_trade(
                    asset=asset,
                    frame=frame,
                    signal_index=index,
                    signal=signal,
                    scenario=scenario,
                    position_pct=self._position_pct(signal, scenario, available_cash_pct=1.0),
                    future_exposure=empty_exposure,
                )
                if trade is None or exit_index is None:
                    continue
                trades.append(trade)
                next_available_index[asset.id] = exit_index
        return sorted(trades, key=lambda trade: (trade.entry_date, trade.symbol))

    def _run_portfolio_mode(
        self,
        assets: list[AssetORM],
        frames: dict[int, pd.DataFrame],
        scenario: BacktestScenario,
    ) -> list[SimulatedTrade]:
        trades: list[SimulatedTrade] = []
        open_trades: list[SimulatedTrade] = []
        asset_by_id = {asset.id: asset for asset in assets}
        calendar = sorted(
            {
                pd.Timestamp(row["date"]).date()
                for frame in frames.values()
                for _, row in frame.iterrows()
                if scenario.start_date <= pd.Timestamp(row["date"]).date() <= scenario.end_date
            }
        )

        for current_date in calendar:
            open_trades = [trade for trade in open_trades if trade.exit_date >= current_date]
            current_exposure = self._exposure_from_open_trades(open_trades)
            invested_pct = sum(trade.position_pct for trade in open_trades)
            available_cash_pct = max(0.0, 1 - invested_pct)

            if len(open_trades) >= scenario.max_open_positions or available_cash_pct <= 0:
                continue

            for asset in assets:
                frame = frames[asset.id]
                matching = frame.index[frame["date"].dt.date == current_date]
                if len(matching) == 0:
                    continue
                if (
                    not scenario.allow_overlapping_per_asset
                    and any(trade.asset_id == asset.id for trade in open_trades)
                ):
                    continue

                index = int(matching[0])
                signal = self.evaluate_signal_point(
                    asset_by_id[asset.id],
                    frame,
                    index,
                    current_exposure,
                )
                if signal is None or not self._entry_allowed(
                    signal,
                    scenario,
                    asset=asset,
                    frame=frame,
                    index=index,
                ):
                    continue

                position_pct = self._position_pct(
                    signal,
                    scenario,
                    available_cash_pct=available_cash_pct,
                )
                if position_pct <= 0:
                    continue
                if self._violates_portfolio_constraints(
                    signal,
                    current_exposure,
                    position_pct,
                    scenario,
                ):
                    continue

                trade, _ = self.simulate_trade(
                    asset=asset_by_id[asset.id],
                    frame=frame,
                    signal_index=index,
                    signal=signal,
                    scenario=scenario,
                    position_pct=position_pct,
                    future_exposure=current_exposure,
                )
                if trade is None:
                    continue
                trades.append(trade)
                open_trades.append(trade)
                available_cash_pct = max(0.0, available_cash_pct - position_pct)
                if len(open_trades) >= scenario.max_open_positions or available_cash_pct <= 0:
                    break

        return sorted(trades, key=lambda trade: (trade.entry_date, trade.symbol))

    def _run_portfolio_realistic_mode(
        self,
        assets: list[AssetORM],
        frames: dict[int, pd.DataFrame],
        scenario: BacktestScenario,
    ) -> tuple[
        list[SimulatedTrade],
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        dict[str, Any],
    ]:
        trades: list[SimulatedTrade] = []
        portfolio_events: list[dict[str, Any]] = []
        equity_curve: list[dict[str, Any]] = []
        cash_curve: list[dict[str, Any]] = []
        holdings: dict[int, list[dict[str, Any]]] = defaultdict(list)
        pending_orders: list[dict[str, Any]] = []
        cash = float(scenario.initial_capital)
        realized_pnl = 0.0
        peak_equity = float(scenario.initial_capital)
        max_concentration = 0.0
        daily_position_counts: list[int] = []
        daily_exposures: list[float] = []

        asset_by_id = {asset.id: asset for asset in assets}
        calendar = sorted(
            {
                pd.Timestamp(row["date"]).date()
                for frame in frames.values()
                for _, row in frame.iterrows()
                if scenario.start_date <= pd.Timestamp(row["date"]).date() <= scenario.end_date
            }
        )

        for current_date in calendar:
            rows_by_asset: dict[int, tuple[int, pd.Series]] = {}
            for asset in assets:
                frame = frames[asset.id]
                matching = frame.index[frame["date"].dt.date == current_date]
                if len(matching) > 0:
                    rows_by_asset[asset.id] = (int(matching[0]), frame.iloc[int(matching[0])])

            cash = self._execute_pending_buy_orders(
                current_date=current_date,
                pending_orders=pending_orders,
                rows_by_asset=rows_by_asset,
                holdings=holdings,
                cash=cash,
                initial_capital=scenario.initial_capital,
                portfolio_events=portfolio_events,
                scenario=scenario,
            )

            market_values = self._current_market_values(holdings, rows_by_asset)
            total_equity = cash + sum(market_values.values())
            exposure = self._exposure_from_market_values(
                asset_by_id=asset_by_id,
                market_values=market_values,
                total_equity=total_equity,
            )

            for asset_id in list(holdings):
                if asset_id not in rows_by_asset or not holdings[asset_id]:
                    continue
                asset = asset_by_id[asset_id]
                index, row = rows_by_asset[asset_id]
                signal = self.evaluate_signal_point(asset, frames[asset_id], index, exposure)
                if signal is None:
                    continue
                chosen_alert = self._select_position_alert_for_sale(
                    asset=asset,
                    row=row,
                    signal=signal,
                    lots=holdings[asset_id],
                    market_value=market_values.get(asset_id, 0.0),
                    total_equity=total_equity,
                    scenario=scenario,
                )
                if chosen_alert is None:
                    continue
                cash_before = cash
                sale_result = self._execute_position_alert_sale(
                    current_date=current_date,
                    asset=asset,
                    row=row,
                    lots=holdings[asset_id],
                    signal=signal,
                    alert_type=chosen_alert,
                    cash=cash,
                    initial_capital=scenario.initial_capital,
                    portfolio_events=portfolio_events,
                    scenario=scenario,
                )
                cash = sale_result["cash"]
                realized_pnl += sale_result["realized_pnl"]
                trades.extend(sale_result["trades"])
                if not holdings[asset_id]:
                    holdings.pop(asset_id, None)
                if cash != cash_before:
                    market_values = self._current_market_values(holdings, rows_by_asset)
                    total_equity = cash + sum(market_values.values())
                    exposure = self._exposure_from_market_values(
                        asset_by_id=asset_by_id,
                        market_values=market_values,
                        total_equity=total_equity,
                    )

            for asset in assets:
                if asset.id not in rows_by_asset:
                    continue
                index, row = rows_by_asset[asset.id]
                existing_lots = holdings.get(asset.id, [])
                if existing_lots and not scenario.portfolio_simulation_rules.allow_add_to_existing:
                    continue
                if (
                    not existing_lots
                    and len([lots for lots in holdings.values() if lots])
                    >= scenario.max_open_positions
                ):
                    continue
                signal = self.evaluate_signal_point(asset, frames[asset.id], index, exposure)
                if signal is None or not self._entry_allowed(
                    signal,
                    scenario,
                    asset=asset,
                    frame=frames[asset.id],
                    index=index,
                ):
                    continue
                order = self._build_buy_order(
                    asset=asset,
                    signal=signal,
                    current_date=current_date,
                    row=row,
                    exposure=exposure,
                    cash=cash,
                    holdings=holdings,
                    asset_by_id=asset_by_id,
                    rows_by_asset=rows_by_asset,
                    scenario=scenario,
                )
                if order is None:
                    continue
                if scenario.execution_rules.entry_mode.value == "next_open":
                    pending_orders.append(order)
                else:
                    cash = self._execute_buy_order(
                        order=order,
                        execution_price=float(row["close"]),
                        current_date=current_date,
                        holdings=holdings,
                        cash=cash,
                        initial_capital=scenario.initial_capital,
                        portfolio_events=portfolio_events,
                        scenario=scenario,
                    )
                    market_values = self._current_market_values(holdings, rows_by_asset)
                    total_equity = cash + sum(market_values.values())
                    exposure = self._exposure_from_market_values(
                        asset_by_id=asset_by_id,
                        market_values=market_values,
                        total_equity=total_equity,
                    )

            market_values = self._current_market_values(holdings, rows_by_asset)
            unrealized_pnl = self._unrealized_pnl(holdings, rows_by_asset)
            total_equity = cash + sum(market_values.values())
            peak_equity = max(peak_equity, total_equity)
            max_concentration = max(
                max_concentration,
                (
                    max((value / total_equity) for value in market_values.values())
                    if total_equity > 0 and market_values
                    else 0.0
                ),
            )
            daily_position_counts.append(len([lots for lots in holdings.values() if lots]))
            daily_exposures.append(
                (sum(market_values.values()) / total_equity) if total_equity > 0 else 0.0
            )
            equity_curve.append(
                {
                    "date": current_date.isoformat(),
                    "equity": round(total_equity, 6),
                    "cash": round(cash, 6),
                    "invested_value": round(sum(market_values.values()), 6),
                    "realized_pnl": round(realized_pnl, 6),
                    "unrealized_pnl": round(unrealized_pnl, 6),
                    "open_positions": len([lots for lots in holdings.values() if lots]),
                }
            )
            cash_curve.append(
                {
                    "date": current_date.isoformat(),
                    "cash": round(cash, 6),
                    "cash_pct": round((cash / total_equity) * 100, 4) if total_equity > 0 else 0.0,
                }
            )

        final_market_values = self._current_market_values_with_last_known(holdings, frames)
        final_equity = equity_curve[-1]["equity"] if equity_curve else scenario.initial_capital
        portfolio_summary = {
            "initial_capital": round(float(scenario.initial_capital), 2),
            "final_capital": round(float(final_equity), 2),
            "portfolio_return_pct": round(
                ((float(final_equity) / scenario.initial_capital) - 1) * 100,
                2,
            )
            if scenario.initial_capital > 0
            else 0.0,
            "final_cash": round(cash, 2),
            "realized_pnl": round(realized_pnl, 2),
            "unrealized_pnl": round(self._unrealized_pnl_with_last_known(holdings, frames), 2),
            "buy_count": sum(1 for event in portfolio_events if event["action"] == "BUY"),
            "sell_partial_count": sum(
                1 for event in portfolio_events if event["action"] == "SELL_PARTIAL"
            ),
            "sell_full_count": sum(
                1 for event in portfolio_events if event["action"] == "SELL_FULL"
            ),
            "avg_entry_size_pct": round(
                pd.Series(
                    [
                        float(event["payload_json"].get("executed_weight_pct", 0.0))
                        for event in portfolio_events
                        if event["action"] == "BUY"
                    ]
                ).mean(),
                2,
            )
            if any(event["action"] == "BUY" for event in portfolio_events)
            else 0.0,
            "avg_reduction_size_pct": round(
                pd.Series(
                    [
                        float(event["payload_json"].get("sold_fraction_pct", 0.0))
                        for event in portfolio_events
                        if event["action"] in {"SELL_PARTIAL", "SELL_FULL"}
                    ]
                ).mean(),
                2,
            )
            if any(event["action"] in {"SELL_PARTIAL", "SELL_FULL"} for event in portfolio_events)
            else 0.0,
            "average_exposure_pct": round(float(pd.Series(daily_exposures).mean() * 100), 2)
            if daily_exposures
            else 0.0,
            "max_concentration_pct": round(max_concentration * 100, 2),
            "avg_open_positions": round(float(pd.Series(daily_position_counts).mean()), 2)
            if daily_position_counts
            else 0.0,
            "final_open_positions": len([lots for lots in holdings.values() if lots]),
            "final_holdings_value": round(sum(final_market_values.values()), 2),
        }

        return trades, portfolio_events, equity_curve, cash_curve, portfolio_summary

    def _run_rsi_cycle_strategy_mode(
        self,
        assets: list[AssetORM],
        frames: dict[int, pd.DataFrame],
        scenario: BacktestScenario,
    ) -> tuple[
        list[SimulatedTrade],
        list[dict[str, Any]],
        list[dict[str, Any]],
        list[dict[str, Any]],
        dict[str, Any],
    ]:
        trades: list[SimulatedTrade] = []
        portfolio_events: list[dict[str, Any]] = []
        equity_curve: list[dict[str, Any]] = []
        cash_curve: list[dict[str, Any]] = []
        holdings: dict[int, list[dict[str, Any]]] = defaultdict(list)
        cash = float(scenario.initial_capital)
        realized_pnl = 0.0
        max_concentration = 0.0
        daily_position_counts: list[int] = []
        daily_exposures: list[float] = []
        event_counts = defaultdict(int)

        asset_by_id = {asset.id: asset for asset in assets}
        enriched_frames: dict[int, pd.DataFrame] = {}
        state_by_asset: dict[int, dict[str, Any]] = {}

        for asset in assets:
            frame = frames[asset.id].copy()
            if frame.empty:
                continue
            enriched = self.technical_service.compute_indicators(frame)
            enriched_frames[asset.id] = enriched.reset_index(drop=True)
            state_by_asset[asset.id] = self._init_rsi_cycle_state()

        calendar = sorted(
            {
                pd.Timestamp(row["date"]).date()
                for frame in enriched_frames.values()
                for _, row in frame.iterrows()
                if scenario.start_date <= pd.Timestamp(row["date"]).date() <= scenario.end_date
            }
        )

        for current_date in calendar:
            rows_by_asset: dict[int, tuple[int, pd.Series]] = {}
            for asset in assets:
                frame = enriched_frames.get(asset.id)
                if frame is None or frame.empty:
                    continue
                matching = frame.index[frame["date"].dt.date == current_date]
                if len(matching) > 0:
                    rows_by_asset[asset.id] = (int(matching[0]), frame.iloc[int(matching[0])])

            market_values = self._current_market_values(holdings, rows_by_asset)
            total_equity = cash + sum(market_values.values())
            exposure = self._exposure_from_market_values(
                asset_by_id=asset_by_id,
                market_values=market_values,
                total_equity=total_equity,
            )

            for asset in assets:
                row_payload = rows_by_asset.get(asset.id)
                if row_payload is None:
                    continue
                index, row = row_payload
                state = state_by_asset[asset.id]
                events = self._rsi_cycle_events_for_bar(
                    frame=enriched_frames[asset.id],
                    index=index,
                    state=state,
                    rules=scenario.rsi_cycle_rules,
                )
                buy_event_type = self._select_rsi_cycle_event(events, side="buy")
                sell_event_type = self._select_rsi_cycle_event(events, side="sell")
                existing_lots = holdings.get(asset.id, [])

                if sell_event_type is not None and existing_lots:
                    sale_result = self._execute_rsi_cycle_sale(
                        asset=asset,
                        row=row,
                        event_type=sell_event_type,
                        lots=existing_lots,
                        cash=cash,
                        initial_capital=scenario.initial_capital,
                        scenario=scenario,
                    )
                    cash = sale_result["cash"]
                    realized_pnl += sale_result["realized_pnl"]
                    trades.extend(sale_result["trades"])
                    if sale_result["event"] is not None:
                        portfolio_events.append(sale_result["event"])
                        event_counts[sell_event_type] += 1
                    if not existing_lots:
                        holdings.pop(asset.id, None)
                    market_values = self._current_market_values(holdings, rows_by_asset)
                    total_equity = cash + sum(market_values.values())
                    exposure = self._exposure_from_market_values(
                        asset_by_id=asset_by_id,
                        market_values=market_values,
                        total_equity=total_equity,
                    )

                if buy_event_type is not None:
                    cash, buy_event = self._execute_rsi_cycle_buy(
                        asset=asset,
                        row=row,
                        event_type=buy_event_type,
                        cash=cash,
                        holdings=holdings,
                        total_equity=max(total_equity, cash),
                        exposure=exposure,
                        initial_capital=scenario.initial_capital,
                        scenario=scenario,
                    )
                    if buy_event is not None:
                        portfolio_events.append(buy_event)
                        event_counts[buy_event_type] += 1
                        market_values = self._current_market_values(holdings, rows_by_asset)
                        total_equity = cash + sum(market_values.values())
                        exposure = self._exposure_from_market_values(
                            asset_by_id=asset_by_id,
                            market_values=market_values,
                            total_equity=total_equity,
                        )

            market_values = self._current_market_values(holdings, rows_by_asset)
            total_equity = cash + sum(market_values.values())
            unrealized_pnl = self._unrealized_pnl(holdings, rows_by_asset)
            max_concentration = max(
                max_concentration,
                max((value / total_equity) for value in market_values.values())
                if total_equity > 0 and market_values
                else 0.0,
            )
            daily_position_counts.append(len([lots for lots in holdings.values() if lots]))
            daily_exposures.append(
                (sum(market_values.values()) / total_equity) if total_equity > 0 else 0.0
            )
            equity_curve.append(
                {
                    "date": current_date.isoformat(),
                    "equity": round(total_equity, 6),
                    "cash": round(cash, 6),
                    "invested_value": round(sum(market_values.values()), 6),
                    "realized_pnl": round(realized_pnl, 6),
                    "unrealized_pnl": round(unrealized_pnl, 6),
                    "open_positions": len([lots for lots in holdings.values() if lots]),
                }
            )
            cash_curve.append(
                {
                    "date": current_date.isoformat(),
                    "cash": round(cash, 6),
                    "cash_pct": round((cash / total_equity) * 100, 4) if total_equity > 0 else 0.0,
                }
            )

        final_market_values = self._current_market_values_with_last_known(holdings, enriched_frames)
        final_equity = equity_curve[-1]["equity"] if equity_curve else scenario.initial_capital
        portfolio_summary = {
            "initial_capital": round(float(scenario.initial_capital), 2),
            "final_capital": round(float(final_equity), 2),
            "portfolio_return_pct": round(
                ((float(final_equity) / scenario.initial_capital) - 1) * 100,
                2,
            )
            if scenario.initial_capital > 0
            else 0.0,
            "final_cash": round(cash, 2),
            "realized_pnl": round(realized_pnl, 2),
            "unrealized_pnl": round(
                self._unrealized_pnl_with_last_known(holdings, enriched_frames),
                2,
            ),
            "buy_count": sum(1 for event in portfolio_events if event["action"] == "BUY"),
            "sell_partial_count": sum(
                1 for event in portfolio_events if event["action"] == "SELL_PARTIAL"
            ),
            "sell_full_count": sum(
                1 for event in portfolio_events if event["action"] == "SELL_FULL"
            ),
            "avg_entry_size_pct": round(
                pd.Series(
                    [
                        float(event["payload_json"].get("executed_weight_pct", 0.0))
                        for event in portfolio_events
                        if event["action"] == "BUY"
                    ]
                ).mean(),
                2,
            )
            if any(event["action"] == "BUY" for event in portfolio_events)
            else 0.0,
            "avg_reduction_size_pct": round(
                pd.Series(
                    [
                        float(event["payload_json"].get("sold_fraction_pct", 0.0))
                        for event in portfolio_events
                        if event["action"] in {"SELL_PARTIAL", "SELL_FULL"}
                    ]
                ).mean(),
                2,
            )
            if any(event["action"] in {"SELL_PARTIAL", "SELL_FULL"} for event in portfolio_events)
            else 0.0,
            "average_exposure_pct": round(float(pd.Series(daily_exposures).mean() * 100), 2)
            if daily_exposures
            else 0.0,
            "max_concentration_pct": round(max_concentration * 100, 2),
            "avg_open_positions": round(float(pd.Series(daily_position_counts).mean()), 2)
            if daily_position_counts
            else 0.0,
            "final_open_positions": len([lots for lots in holdings.values() if lots]),
            "final_holdings_value": round(sum(final_market_values.values()), 2),
            "buy_rsi_25_count": int(event_counts["BUY_RSI_25"]),
            "buy_rsi_20_count": int(event_counts["BUY_RSI_20"]),
            "buy_bullish_divergence_count": int(event_counts["BUY_BULLISH_DIVERGENCE"]),
            "sell_rsi_75_count": int(event_counts["SELL_RSI_75"]),
            "sell_rsi_80_count": int(event_counts["SELL_RSI_80"]),
            "sell_bearish_divergence_count": int(event_counts["SELL_BEARISH_DIVERGENCE"]),
        }
        return trades, portfolio_events, equity_curve, cash_curve, portfolio_summary

    def _resolve_exit(
        self,
        *,
        asset: AssetORM,
        frame: pd.DataFrame,
        signal: HistoricalSignal,
        signal_index: int,
        entry_index: int,
        entry_price: float,
        position_pct: float,
        scenario: BacktestScenario,
        future_exposure: PortfolioExposureModel | None,
    ) -> tuple[dict[str, Any] | None, int | None]:
        max_end_index = min(
            len(frame) - 1,
            entry_index + max(1, scenario.exit_rules.max_holding_days),
        )
        mae_pct = 0.0
        mfe_pct = 0.0
        max_drawdown_pct = 0.0
        for index in range(entry_index + 1, max_end_index + 1):
            row = frame.iloc[index]
            low_return = ((float(row["low"]) / entry_price) - 1) * 100
            high_return = ((float(row["high"]) / entry_price) - 1) * 100
            close_return = ((float(row["close"]) / entry_price) - 1) * 100

            mae_pct = min(mae_pct, low_return)
            mfe_pct = max(mfe_pct, high_return)
            max_drawdown_pct = min(max_drawdown_pct, close_return)

            if scenario.exit_rules.strategy.value in {"take_profit_stop_loss", "hybrid"}:
                stop_hit = (
                    scenario.exit_rules.stop_loss_pct is not None
                    and float(row["low"]) <= entry_price * (1 - scenario.exit_rules.stop_loss_pct)
                )
                take_hit = (
                    scenario.exit_rules.take_profit_pct is not None
                    and float(row["high"])
                    >= entry_price * (1 + scenario.exit_rules.take_profit_pct)
                )
                if stop_hit or take_hit:
                    if stop_hit:
                        exit_price = entry_price * (1 - float(scenario.exit_rules.stop_loss_pct))
                        reason = "stop_loss"
                    else:
                        exit_price = entry_price * (1 + float(scenario.exit_rules.take_profit_pct))
                        reason = "take_profit"
                    return (
                        self._exit_payload(
                            frame=frame,
                            index=index,
                            entry_index=entry_index,
                            exit_price=exit_price,
                            exit_reason=reason,
                            mae_pct=mae_pct,
                            mfe_pct=mfe_pct,
                            max_drawdown_pct=max_drawdown_pct,
                            slippage_bps=scenario.execution_rules.slippage_bps,
                        ),
                        index,
                    )

            future_signal: HistoricalSignal | None = None
            if scenario.exit_rules.strategy.value in {
                "signal_loss",
                "hybrid",
                "position_alerts",
                "hybrid_position_alerts",
            }:
                future_signal = self.evaluate_signal_point(asset, frame, index, future_exposure)

            if scenario.exit_rules.strategy.value in {"signal_loss", "hybrid"}:
                if future_signal is not None:
                    invalidated = (
                        signal.invalidation_level is not None
                        and float(row["close"])
                        < signal.invalidation_level
                        * (1 - scenario.exit_rules.invalidation_buffer_pct)
                    )
                    score_lost = (
                        scenario.exit_rules.signal_loss_score_threshold is not None
                        and future_signal.final_score
                        < scenario.exit_rules.signal_loss_score_threshold
                    )
                    if invalidated or score_lost:
                        reason = "invalidation" if invalidated else "signal_loss"
                        return (
                            self._exit_payload(
                                frame=frame,
                                index=index,
                                entry_index=entry_index,
                                exit_price=float(row["close"]),
                                exit_reason=reason,
                                mae_pct=mae_pct,
                                mfe_pct=mfe_pct,
                                max_drawdown_pct=max_drawdown_pct,
                                slippage_bps=scenario.execution_rules.slippage_bps,
                            ),
                            index,
                        )

            if scenario.exit_rules.strategy.value in {
                "position_alerts",
                "hybrid_position_alerts",
            } and future_signal is not None:
                exit_alert_type = self._position_alert_exit_type(
                    asset=asset,
                    row=row,
                    signal=future_signal,
                    entry_price=entry_price,
                    position_pct=position_pct,
                    scenario=scenario,
                )
                if exit_alert_type is not None:
                    return (
                        self._exit_payload(
                            frame=frame,
                            index=index,
                            entry_index=entry_index,
                            exit_price=float(row["close"]),
                            exit_reason=exit_alert_type,
                            mae_pct=mae_pct,
                            mfe_pct=mfe_pct,
                            max_drawdown_pct=max_drawdown_pct,
                            slippage_bps=scenario.execution_rules.slippage_bps,
                        ),
                        index,
                    )

            horizon_hit = index >= entry_index + scenario.exit_rules.fixed_horizon_days
            if (
                scenario.exit_rules.strategy.value == "fixed_horizon"
                or scenario.exit_rules.strategy.value == "hybrid"
                or scenario.exit_rules.strategy.value == "hybrid_position_alerts"
            ) and horizon_hit:
                return (
                    self._exit_payload(
                        frame=frame,
                        index=index,
                        entry_index=entry_index,
                        exit_price=float(row["close"]),
                        exit_reason="fixed_horizon",
                        mae_pct=mae_pct,
                        mfe_pct=mfe_pct,
                        max_drawdown_pct=max_drawdown_pct,
                        slippage_bps=scenario.execution_rules.slippage_bps,
                    ),
                    index,
                )

        if entry_index < len(frame) - 1:
            return (
                self._exit_payload(
                    frame=frame,
                    index=max_end_index,
                    entry_index=entry_index,
                    exit_price=float(frame.iloc[max_end_index]["close"]),
                    exit_reason="end_of_data",
                    mae_pct=mae_pct,
                    mfe_pct=mfe_pct,
                    max_drawdown_pct=max_drawdown_pct,
                    slippage_bps=scenario.execution_rules.slippage_bps,
                ),
                max_end_index,
            )
        return None, None

    def _execute_pending_buy_orders(
        self,
        *,
        current_date: date,
        pending_orders: list[dict[str, Any]],
        rows_by_asset: dict[int, tuple[int, pd.Series]],
        holdings: dict[int, list[dict[str, Any]]],
        cash: float,
        initial_capital: float,
        portfolio_events: list[dict[str, Any]],
        scenario: BacktestScenario,
    ) -> float:
        remaining_orders: list[dict[str, Any]] = []
        for order in pending_orders:
            row_payload = rows_by_asset.get(order["asset_id"])
            if row_payload is None:
                remaining_orders.append(order)
                continue
            _, row = row_payload
            cash = self._execute_buy_order(
                order=order,
                execution_price=float(row["open"]),
                current_date=current_date,
                holdings=holdings,
                cash=cash,
                initial_capital=initial_capital,
                portfolio_events=portfolio_events,
                scenario=scenario,
            )
        pending_orders[:] = remaining_orders
        return cash

    @staticmethod
    def _init_rsi_cycle_state() -> dict[str, Any]:
        return {
            "oversold_active": False,
            "overbought_active": False,
            "buy_rsi_25_used": False,
            "buy_rsi_20_used": False,
            "buy_div_used": False,
            "sell_rsi_75_used": False,
            "sell_rsi_80_used": False,
            "sell_div_used": False,
            "oversold_pivots": [],
            "overbought_pivots": [],
            "bullish_divergence_pending": False,
            "bearish_divergence_pending": False,
        }

    def _rsi_cycle_events_for_bar(
        self,
        *,
        frame: pd.DataFrame,
        index: int,
        state: dict[str, Any],
        rules,
    ) -> list[str]:
        row = frame.iloc[index]
        rsi = self._optional_float(row.get("rsi14"))
        if rsi is None:
            return []

        prev_rsi = None
        if index > 0:
            prev_rsi = self._optional_float(frame.iloc[index - 1].get("rsi14"))

        events: list[str] = []

        if rsi < rules.oversold_threshold and not state["oversold_active"]:
            state["oversold_active"] = True
            state["buy_rsi_25_used"] = False
            state["buy_rsi_20_used"] = False
            state["buy_div_used"] = False
            state["oversold_pivots"] = []
            state["bullish_divergence_pending"] = False

        if rsi > rules.overbought_threshold and not state["overbought_active"]:
            state["overbought_active"] = True
            state["sell_rsi_75_used"] = False
            state["sell_rsi_80_used"] = False
            state["sell_div_used"] = False
            state["overbought_pivots"] = []
            state["bearish_divergence_pending"] = False

        if state["oversold_active"]:
            if not state["buy_rsi_25_used"] and rsi <= rules.deep_oversold_threshold_1:
                events.append("BUY_RSI_25")
                state["buy_rsi_25_used"] = True
            if not state["buy_rsi_20_used"] and rsi <= rules.deep_oversold_threshold_2:
                events.append("BUY_RSI_20")
                state["buy_rsi_20_used"] = True
            pivot = self._confirm_pivot_low(frame, index, rules)
            if pivot is not None:
                state["oversold_pivots"].append(pivot)
                state["oversold_pivots"] = state["oversold_pivots"][-5:]
                if self._has_bullish_divergence(state["oversold_pivots"], rules):
                    state["bullish_divergence_pending"] = True
            bull_confirmed = (
                state["bullish_divergence_pending"]
                and not state["buy_div_used"]
                and (
                    (not rules.require_confirmation_cross and rsi > rules.oversold_threshold)
                    or (
                        rules.require_confirmation_cross
                        and prev_rsi is not None
                        and prev_rsi <= rules.oversold_threshold
                        and rsi > rules.oversold_threshold
                    )
                )
            )
            if bull_confirmed:
                events.append("BUY_BULLISH_DIVERGENCE")
                state["buy_div_used"] = True
                if rules.max_one_divergence_per_cycle:
                    state["bullish_divergence_pending"] = False
            if rsi > rules.oversold_threshold:
                state["oversold_active"] = False
                state["oversold_pivots"] = []
                state["bullish_divergence_pending"] = False

        if state["overbought_active"]:
            if not state["sell_rsi_75_used"] and rsi >= rules.overbought_threshold_1:
                events.append("SELL_RSI_75")
                state["sell_rsi_75_used"] = True
            if not state["sell_rsi_80_used"] and rsi >= rules.overbought_threshold_2:
                events.append("SELL_RSI_80")
                state["sell_rsi_80_used"] = True
            pivot = self._confirm_pivot_high(frame, index, rules)
            if pivot is not None:
                state["overbought_pivots"].append(pivot)
                state["overbought_pivots"] = state["overbought_pivots"][-5:]
                if self._has_bearish_divergence(state["overbought_pivots"], rules):
                    state["bearish_divergence_pending"] = True
            bear_confirmed = (
                state["bearish_divergence_pending"]
                and not state["sell_div_used"]
                and (
                    (not rules.require_confirmation_cross and rsi < rules.overbought_threshold)
                    or (
                        rules.require_confirmation_cross
                        and prev_rsi is not None
                        and prev_rsi >= rules.overbought_threshold
                        and rsi < rules.overbought_threshold
                    )
                )
            )
            if bear_confirmed:
                events.append("SELL_BEARISH_DIVERGENCE")
                state["sell_div_used"] = True
                if rules.max_one_divergence_per_cycle:
                    state["bearish_divergence_pending"] = False
            if rsi < rules.overbought_threshold:
                state["overbought_active"] = False
                state["overbought_pivots"] = []
                state["bearish_divergence_pending"] = False

        return events

    def _confirm_pivot_low(self, frame: pd.DataFrame, index: int, rules) -> dict[str, Any] | None:
        if index < 2:
            return None
        rsi_prev2 = self._optional_float(frame.iloc[index - 2].get("rsi14"))
        rsi_prev1 = self._optional_float(frame.iloc[index - 1].get("rsi14"))
        rsi_now = self._optional_float(frame.iloc[index].get("rsi14"))
        if rsi_prev2 is None or rsi_prev1 is None or rsi_now is None:
            return None
        if not (rsi_prev2 > rsi_prev1 <= rsi_now and rsi_prev1 <= rules.oversold_threshold):
            return None
        pivot_index = index - 1
        pivot_row = frame.iloc[pivot_index]
        price_field = "low" if rules.pivot_price_source == "extremes" else "close"
        return {
            "index": pivot_index,
            "date": pd.Timestamp(pivot_row["date"]).date(),
            "rsi": float(rsi_prev1),
            "price": float(pivot_row[price_field]),
        }

    def _confirm_pivot_high(self, frame: pd.DataFrame, index: int, rules) -> dict[str, Any] | None:
        if index < 2:
            return None
        rsi_prev2 = self._optional_float(frame.iloc[index - 2].get("rsi14"))
        rsi_prev1 = self._optional_float(frame.iloc[index - 1].get("rsi14"))
        rsi_now = self._optional_float(frame.iloc[index].get("rsi14"))
        if rsi_prev2 is None or rsi_prev1 is None or rsi_now is None:
            return None
        if not (rsi_prev2 < rsi_prev1 >= rsi_now and rsi_prev1 >= rules.overbought_threshold):
            return None
        pivot_index = index - 1
        pivot_row = frame.iloc[pivot_index]
        price_field = "high" if rules.pivot_price_source == "extremes" else "close"
        return {
            "index": pivot_index,
            "date": pd.Timestamp(pivot_row["date"]).date(),
            "rsi": float(rsi_prev1),
            "price": float(pivot_row[price_field]),
        }

    @staticmethod
    def _has_bullish_divergence(pivots: list[dict[str, Any]], rules) -> bool:
        if len(pivots) < 2:
            return False
        first, second = pivots[-2], pivots[-1]
        gap = second["index"] - first["index"]
        return (
            rules.min_bars_between_pivots <= gap <= rules.max_bars_between_pivots
            and second["price"] < first["price"]
            and second["rsi"] > first["rsi"]
        )

    @staticmethod
    def _has_bearish_divergence(pivots: list[dict[str, Any]], rules) -> bool:
        if len(pivots) < 2:
            return False
        first, second = pivots[-2], pivots[-1]
        gap = second["index"] - first["index"]
        return (
            rules.min_bars_between_pivots <= gap <= rules.max_bars_between_pivots
            and second["price"] > first["price"]
            and second["rsi"] < first["rsi"]
        )

    @staticmethod
    def _select_rsi_cycle_event(events: list[str], *, side: str) -> str | None:
        filtered = [
            event
            for event in events
            if event.startswith("BUY_" if side == "buy" else "SELL_")
        ]
        if not filtered:
            return None

        if side == "buy":
            priority = {
                "BUY_RSI_20": 0,
                "BUY_BULLISH_DIVERGENCE": 1,
                "BUY_RSI_25": 2,
            }
        else:
            priority = {
                "SELL_RSI_80": 0,
                "SELL_BEARISH_DIVERGENCE": 1,
                "SELL_RSI_75": 2,
            }
        return min(filtered, key=lambda event: priority.get(event, 99))

    def _build_buy_order(
        self,
        *,
        asset: AssetORM,
        signal: HistoricalSignal,
        current_date: date,
        row: pd.Series,
        exposure: PortfolioExposureModel,
        cash: float,
        holdings: dict[int, list[dict[str, Any]]],
        asset_by_id: dict[int, AssetORM],
        rows_by_asset: dict[int, tuple[int, pd.Series]],
        scenario: BacktestScenario,
    ) -> dict[str, Any] | None:
        total_equity = cash + sum(self._current_market_values(holdings, rows_by_asset).values())
        rules = scenario.portfolio_simulation_rules
        desired_weight = (
            signal.suggested_weight_add_pct / 100
            if rules.use_suggested_weight_add
            else (rules.buy_weight_override_pct or scenario.execution_rules.fixed_position_pct)
        )
        desired_weight *= self._regime_position_size_multiplier(signal, scenario)
        desired_value = total_equity * desired_weight
        available_cash = max(0.0, cash - (rules.cash_min_target_pct * total_equity))
        desired_value = min(desired_value, available_cash)
        if desired_value < rules.min_trade_value:
            return None

        if rules.apply_portfolio_limits and total_equity > 0:
            current_asset_value = sum(
                lot["remaining_qty"] * float(row["close"]) for lot in holdings.get(asset.id, [])
            )
            sector_value = sum(
                self._holding_market_value(holdings.get(other_id, []), rows_by_asset.get(other_id))
                for other_id, other_asset in asset_by_id.items()
                if other_asset.sector == asset.sector
            )
            type_value = sum(
                self._holding_market_value(holdings.get(other_id, []), rows_by_asset.get(other_id))
                for other_id, other_asset in asset_by_id.items()
                if other_asset.asset_type == asset.asset_type
            )
            desired_value = min(
                desired_value,
                max(0.0, rules.max_asset_weight * total_equity - current_asset_value),
                max(0.0, rules.max_sector_weight * total_equity - sector_value),
                max(
                    0.0,
                    float(rules.max_asset_type_weight.get(asset.asset_type, 1.0)) * total_equity
                    - type_value,
                ),
            )
            if desired_value < rules.min_trade_value:
                return None

        return {
            "asset_id": asset.id,
            "symbol": asset.symbol,
            "signal_date": signal.signal_date,
            "order_date": current_date,
            "desired_value": desired_value,
            "signal": signal,
            "execution_side": "buy",
        }

    def _execute_rsi_cycle_buy(
        self,
        *,
        asset: AssetORM,
        row: pd.Series,
        event_type: str,
        cash: float,
        holdings: dict[int, list[dict[str, Any]]],
        total_equity: float,
        exposure: PortfolioExposureModel,
        initial_capital: float,
        scenario: BacktestScenario,
    ) -> tuple[float, dict[str, Any] | None]:
        rules = scenario.portfolio_simulation_rules
        rsi_rules = scenario.rsi_cycle_rules
        has_existing_position = bool(holdings.get(asset.id))
        if has_existing_position and not rules.allow_add_to_existing:
            return cash, None
        if (
            not has_existing_position
            and len([lots for lots in holdings.values() if lots]) >= scenario.max_open_positions
        ):
            return cash, None
        event_weight = {
            "BUY_BULLISH_DIVERGENCE": rsi_rules.buy_pct_bullish_divergence,
            "BUY_RSI_25": rsi_rules.buy_pct_rsi_25,
            "BUY_RSI_20": rsi_rules.buy_pct_rsi_20,
        }.get(event_type, 0.0)
        desired_value = total_equity * event_weight
        available_cash = max(0.0, cash - (rules.cash_min_target_pct * total_equity))
        desired_value = min(desired_value, available_cash)
        if desired_value < rules.min_trade_value:
            return cash, None
        current_price = float(row["close"])
        current_asset_value = sum(
            lot["remaining_qty"] * current_price for lot in holdings.get(asset.id, [])
        )
        if rules.apply_portfolio_limits and total_equity > 0:
            desired_value = min(
                desired_value,
                max(0.0, rules.max_asset_weight * total_equity - current_asset_value),
                max(
                    0.0,
                    rules.max_sector_weight * total_equity
                    - exposure.by_sector.get(asset.sector, 0.0) * total_equity,
                ),
                max(
                    0.0,
                    float(rules.max_asset_type_weight.get(asset.asset_type, 1.0)) * total_equity
                    - exposure.by_asset_type.get(asset.asset_type, 0.0) * total_equity,
                ),
            )
        if desired_value < rules.min_trade_value:
            return cash, None

        exec_price = self._apply_slippage(
            current_price,
            scenario.execution_rules.slippage_bps,
            side="buy",
        )
        commission_rate = scenario.execution_rules.commission_bps / 10000
        quantity = desired_value / (exec_price * (1 + commission_rate))
        if quantity <= 0:
            return cash, None
        notional = quantity * exec_price
        commission = notional * commission_rate
        cash_before = cash
        cash -= notional + commission
        holdings[asset.id].append(
            {
                "entry_signal_date": pd.Timestamp(row["date"]).date(),
                "entry_date": pd.Timestamp(row["date"]).date(),
                "entry_price": exec_price,
                "entry_cost_total": notional + commission,
                "entry_notional": notional,
                "remaining_qty": quantity,
                "asset_type": asset.asset_type,
                "sector": asset.sector,
                "recommendation": event_type,
                "technical_score": 0.0,
                "risk_score": 0.0,
                "portfolio_fit_score": 0.0,
                "final_score": 0.0,
                "invalidation_level": None,
                "rationale": {"rsi_cycle_event": event_type},
                "parameters": scenario.to_payload(),
            }
        )
        event = {
            "asset_id": asset.id,
            "symbol": asset.symbol,
            "event_date": pd.Timestamp(row["date"]).date(),
            "action": "BUY",
            "trigger_type": event_type,
            "quantity": round(quantity, 8),
            "price": round(exec_price, 4),
            "gross_value": round(notional, 2),
            "cash_before": round(cash_before, 2),
            "cash_after": round(cash, 2),
            "position_weight_before": exposure.by_asset.get(asset.symbol),
            "position_weight_after": None,
            "payload_json": {
                "executed_weight_pct": round((notional / initial_capital) * 100, 2),
                "rsi14": self._optional_float(row.get("rsi14")),
                "event_type": event_type,
            },
        }
        return cash, event

    def _execute_rsi_cycle_sale(
        self,
        *,
        asset: AssetORM,
        row: pd.Series,
        event_type: str,
        lots: list[dict[str, Any]],
        cash: float,
        initial_capital: float,
        scenario: BacktestScenario,
    ) -> dict[str, Any]:
        trades: list[SimulatedTrade] = []
        commission_rate = scenario.execution_rules.commission_bps / 10000
        exit_price = self._apply_slippage(
            float(row["close"]),
            scenario.execution_rules.slippage_bps,
            side="sell",
        )
        current_value = sum(lot["remaining_qty"] * exit_price for lot in lots)
        reduction_pct = {
            "SELL_BEARISH_DIVERGENCE": scenario.rsi_cycle_rules.sell_pct_bearish_divergence,
            "SELL_RSI_75": scenario.rsi_cycle_rules.sell_pct_rsi_75,
            "SELL_RSI_80": scenario.rsi_cycle_rules.sell_pct_rsi_80,
        }.get(event_type, 0.0)
        sell_value = current_value * reduction_pct
        residual = current_value - sell_value
        if residual < scenario.portfolio_simulation_rules.min_residual_position_value:
            sell_value = current_value
        quantity_to_sell = min(
            sum(lot["remaining_qty"] for lot in lots),
            sell_value / exit_price if exit_price > 0 else 0.0,
        )
        if quantity_to_sell <= 0:
            return {"cash": cash, "realized_pnl": 0.0, "trades": [], "event": None}
        cash_before = cash
        realized_pnl = 0.0
        remaining_to_sell = quantity_to_sell
        total_qty_before = sum(lot["remaining_qty"] for lot in lots)
        while remaining_to_sell > 1e-9 and lots:
            lot = lots[0]
            original_qty = lot["remaining_qty"]
            sell_qty = min(original_qty, remaining_to_sell)
            entry_cost_alloc = lot["entry_cost_total"] * (sell_qty / original_qty)
            entry_notional_alloc = lot["entry_notional"] * (sell_qty / original_qty)
            gross_proceeds = sell_qty * exit_price
            sell_commission = gross_proceeds * commission_rate
            net_proceeds = gross_proceeds - sell_commission
            gross_return_pct = ((exit_price / lot["entry_price"]) - 1) * 100
            net_return_pct = ((net_proceeds / entry_cost_alloc) - 1) * 100
            realized_pnl += net_proceeds - entry_cost_alloc
            cash += net_proceeds
            trades.append(
                SimulatedTrade(
                    asset_id=asset.id,
                    symbol=asset.symbol,
                    name=asset.name,
                    asset_type=asset.asset_type,
                    sector=asset.sector,
                    entry_signal_date=lot["entry_signal_date"],
                    entry_date=lot["entry_date"],
                    exit_date=pd.Timestamp(row["date"]).date(),
                    entry_price=round(lot["entry_price"], 4),
                    exit_price=round(exit_price, 4),
                    position_pct=round(entry_notional_alloc / initial_capital, 4),
                    gross_return_pct=round(gross_return_pct, 2),
                    net_return_pct=round(net_return_pct, 2),
                    max_drawdown_pct=0.0,
                    mae_pct=0.0,
                    mfe_pct=0.0,
                    holding_days=(pd.Timestamp(row["date"]).date() - lot["entry_date"]).days,
                    exit_reason=event_type,
                    recommendation=lot["recommendation"],
                    technical_score=lot["technical_score"],
                    risk_score=lot["risk_score"],
                    portfolio_fit_score=lot["portfolio_fit_score"],
                    final_score=lot["final_score"],
                    invalidation_level=lot["invalidation_level"],
                    rationale=lot["rationale"],
                    parameters=lot["parameters"],
                )
            )
            lot["entry_cost_total"] -= entry_cost_alloc
            lot["entry_notional"] -= entry_notional_alloc
            lot["remaining_qty"] -= sell_qty
            remaining_to_sell -= sell_qty
            if lot["remaining_qty"] <= 1e-9:
                lots.pop(0)

        position_value_after = sum(lot["remaining_qty"] * exit_price for lot in lots)
        event = {
            "asset_id": asset.id,
            "symbol": asset.symbol,
            "event_date": pd.Timestamp(row["date"]).date(),
            "action": "SELL_FULL" if position_value_after <= 0 else "SELL_PARTIAL",
            "trigger_type": event_type,
            "quantity": round(quantity_to_sell, 8),
            "price": round(exit_price, 4),
            "gross_value": round(quantity_to_sell * exit_price, 2),
            "cash_before": round(cash_before, 2),
            "cash_after": round(cash, 2),
            "position_weight_before": None,
            "position_weight_after": None,
            "payload_json": {
                "sold_fraction_pct": round((quantity_to_sell / total_qty_before) * 100, 2),
                "rsi14": self._optional_float(row.get("rsi14")),
            },
        }
        return {"cash": cash, "realized_pnl": realized_pnl, "trades": trades, "event": event}

    def _execute_buy_order(
        self,
        *,
        order: dict[str, Any],
        execution_price: float,
        current_date: date,
        holdings: dict[int, list[dict[str, Any]]],
        cash: float,
        initial_capital: float,
        portfolio_events: list[dict[str, Any]],
        scenario: BacktestScenario,
    ) -> float:
        exec_price = self._apply_slippage(
            execution_price,
            scenario.execution_rules.slippage_bps,
            side="buy",
        )
        commission_rate = scenario.execution_rules.commission_bps / 10000
        affordable_value = min(order["desired_value"], cash)
        quantity = affordable_value / (exec_price * (1 + commission_rate))
        if quantity <= 0:
            return cash
        notional = quantity * exec_price
        commission = notional * commission_rate
        cash_before = cash
        cash -= notional + commission
        signal: HistoricalSignal = order["signal"]
        holdings[order["asset_id"]].append(
            {
                "entry_signal_date": signal.signal_date,
                "entry_date": current_date,
                "entry_price": exec_price,
                "entry_cost_total": notional + commission,
                "entry_notional": notional,
                "remaining_qty": quantity,
                "asset_type": signal.asset_type,
                "sector": signal.sector,
                "recommendation": signal.recommendation,
                "technical_score": signal.technical_score,
                "risk_score": signal.risk_score,
                "portfolio_fit_score": signal.portfolio_fit_score,
                "final_score": signal.final_score,
                "invalidation_level": signal.invalidation_level,
                "rationale": signal.rationale,
                "parameters": scenario.to_payload(),
            }
        )
        portfolio_events.append(
            {
                "asset_id": order["asset_id"],
                "symbol": order["symbol"],
                "event_date": current_date,
                "action": "BUY",
                "trigger_type": "entry_signal",
                "quantity": round(quantity, 8),
                "price": round(exec_price, 4),
                "gross_value": round(notional, 2),
                "cash_before": round(cash_before, 2),
                "cash_after": round(cash, 2),
                "position_weight_before": None,
                "position_weight_after": None,
                "payload_json": {
                    "executed_weight_pct": round((notional / initial_capital) * 100, 2),
                    "signal_final_score": signal.final_score,
                    "recommendation": signal.recommendation,
                },
            }
        )
        return cash

    def _select_position_alert_for_sale(
        self,
        *,
        asset: AssetORM,
        row: pd.Series,
        signal: HistoricalSignal,
        lots: list[dict[str, Any]],
        market_value: float,
        total_equity: float,
        scenario: BacktestScenario,
    ) -> str | None:
        if total_equity <= 0 or market_value <= 0:
            return None
        avg_cost = sum(lot["entry_cost_total"] for lot in lots) / max(
            1e-9, sum(lot["remaining_qty"] for lot in lots)
        )
        target_weight = max(
            scenario.portfolio_simulation_rules.buy_weight_override_pct
            or 0.0,
            signal.suggested_weight_add_pct / 100,
        )
        alerts = self.position_management_alerts_service.detect_alerts(
            asset=asset,
            row={
                "last_price": float(row["close"]),
                "rsi14": signal.rsi14,
                "sma50": signal.sma50,
                "support_low": signal.support_low,
                "final_opportunity_score": signal.final_score,
                "risk_score": signal.risk_score,
                "recommendation": signal.recommendation,
            },
            position=PositionContext(
                quantity=sum(lot["remaining_qty"] for lot in lots),
                avg_cost=avg_cost,
                current_weight=market_value / total_equity,
                target_weight=target_weight,
            ),
        )
        alert_types = {alert.event_type for alert in alerts}
        for event_type in scenario.portfolio_simulation_rules.sell_priority:
            if (
                event_type in alert_types
                and scenario.portfolio_simulation_rules.sell_reduction_by_alert_type.get(
                    event_type,
                    0.0,
                )
                > 0
            ):
                return event_type
        return None

    def _execute_position_alert_sale(
        self,
        *,
        current_date: date,
        asset: AssetORM,
        row: pd.Series,
        lots: list[dict[str, Any]],
        signal: HistoricalSignal,
        alert_type: str,
        cash: float,
        initial_capital: float,
        portfolio_events: list[dict[str, Any]],
        scenario: BacktestScenario,
    ) -> dict[str, Any]:
        trades: list[SimulatedTrade] = []
        commission_rate = scenario.execution_rules.commission_bps / 10000
        exit_price = self._apply_slippage(
            float(row["close"]),
            scenario.execution_rules.slippage_bps,
            side="sell",
        )
        current_value = sum(lot["remaining_qty"] * exit_price for lot in lots)
        reduction_pct = float(
            scenario.portfolio_simulation_rules.sell_reduction_by_alert_type.get(alert_type, 0.0)
        )
        sell_value = current_value * reduction_pct
        residual = current_value - sell_value
        if residual < scenario.portfolio_simulation_rules.min_residual_position_value:
            sell_value = current_value
        quantity_to_sell = min(
            sum(lot["remaining_qty"] for lot in lots),
            sell_value / exit_price if exit_price > 0 else 0.0,
        )
        if quantity_to_sell <= 0:
            return {"cash": cash, "realized_pnl": 0.0, "trades": []}

        cash_before = cash
        realized_pnl = 0.0
        remaining_to_sell = quantity_to_sell
        total_qty_before = sum(lot["remaining_qty"] for lot in lots)
        while remaining_to_sell > 1e-9 and lots:
            lot = lots[0]
            sell_qty = min(lot["remaining_qty"], remaining_to_sell)
            entry_cost_alloc = lot["entry_cost_total"] * (sell_qty / lot["remaining_qty"])
            gross_proceeds = sell_qty * exit_price
            sell_commission = gross_proceeds * commission_rate
            net_proceeds = gross_proceeds - sell_commission
            gross_return_pct = ((exit_price / lot["entry_price"]) - 1) * 100
            net_return_pct = ((net_proceeds / entry_cost_alloc) - 1) * 100
            realized_pnl += net_proceeds - entry_cost_alloc
            cash += net_proceeds
            trade = SimulatedTrade(
                asset_id=asset.id,
                symbol=asset.symbol,
                name=asset.name,
                asset_type=asset.asset_type,
                sector=asset.sector,
                entry_signal_date=lot["entry_signal_date"],
                entry_date=lot["entry_date"],
                exit_date=current_date,
                entry_price=round(lot["entry_price"], 4),
                exit_price=round(exit_price, 4),
                position_pct=round(entry_cost_alloc / initial_capital, 4),
                gross_return_pct=round(gross_return_pct, 2),
                net_return_pct=round(net_return_pct, 2),
                max_drawdown_pct=0.0,
                mae_pct=0.0,
                mfe_pct=0.0,
                holding_days=(current_date - lot["entry_date"]).days,
                exit_reason=alert_type,
                recommendation=lot["recommendation"],
                technical_score=lot["technical_score"],
                risk_score=lot["risk_score"],
                portfolio_fit_score=lot["portfolio_fit_score"],
                final_score=lot["final_score"],
                invalidation_level=lot["invalidation_level"],
                rationale=lot["rationale"],
                parameters=lot["parameters"],
            )
            trades.append(trade)
            lot["entry_cost_total"] -= entry_cost_alloc
            lot["entry_notional"] -= lot["entry_notional"] * (sell_qty / lot["remaining_qty"])
            lot["remaining_qty"] -= sell_qty
            remaining_to_sell -= sell_qty
            if lot["remaining_qty"] <= 1e-9:
                lots.pop(0)

        position_value_after = sum(lot["remaining_qty"] * exit_price for lot in lots)
        portfolio_events.append(
            {
                "asset_id": asset.id,
                "symbol": asset.symbol,
                "event_date": current_date,
                "action": "SELL_FULL" if position_value_after <= 0 else "SELL_PARTIAL",
                "trigger_type": alert_type,
                "quantity": round(quantity_to_sell, 8),
                "price": round(exit_price, 4),
                "gross_value": round(quantity_to_sell * exit_price, 2),
                "cash_before": round(cash_before, 2),
                "cash_after": round(cash, 2),
                "position_weight_before": None,
                "position_weight_after": None,
                "payload_json": {
                    "sold_fraction_pct": round((quantity_to_sell / total_qty_before) * 100, 2),
                    "signal_final_score": signal.final_score,
                    "risk_score": signal.risk_score,
                },
            }
        )
        return {"cash": cash, "realized_pnl": realized_pnl, "trades": trades}

    def _current_market_values(
        self,
        holdings: dict[int, list[dict[str, Any]]],
        rows_by_asset: dict[int, tuple[int, pd.Series]],
    ) -> dict[int, float]:
        market_values: dict[int, float] = {}
        for asset_id, lots in holdings.items():
            row_payload = rows_by_asset.get(asset_id)
            if row_payload is None:
                continue
            _, row = row_payload
            market_values[asset_id] = (
                sum(lot["remaining_qty"] for lot in lots) * float(row["close"])
            )
        return market_values

    def _current_market_values_with_last_known(
        self,
        holdings: dict[int, list[dict[str, Any]]],
        frames: dict[int, pd.DataFrame],
    ) -> dict[int, float]:
        values: dict[int, float] = {}
        for asset_id, lots in holdings.items():
            frame = frames.get(asset_id)
            if frame is None or frame.empty:
                continue
            close = float(frame.iloc[-1]["close"])
            values[asset_id] = sum(lot["remaining_qty"] for lot in lots) * close
        return values

    def _unrealized_pnl(
        self,
        holdings: dict[int, list[dict[str, Any]]],
        rows_by_asset: dict[int, tuple[int, pd.Series]],
    ) -> float:
        pnl = 0.0
        for asset_id, lots in holdings.items():
            row_payload = rows_by_asset.get(asset_id)
            if row_payload is None:
                continue
            _, row = row_payload
            close = float(row["close"])
            pnl += sum((close * lot["remaining_qty"]) - lot["entry_cost_total"] for lot in lots)
        return pnl

    def _unrealized_pnl_with_last_known(
        self,
        holdings: dict[int, list[dict[str, Any]]],
        frames: dict[int, pd.DataFrame],
    ) -> float:
        pnl = 0.0
        for asset_id, lots in holdings.items():
            frame = frames.get(asset_id)
            if frame is None or frame.empty:
                continue
            close = float(frame.iloc[-1]["close"])
            pnl += sum((close * lot["remaining_qty"]) - lot["entry_cost_total"] for lot in lots)
        return pnl

    def _exposure_from_market_values(
        self,
        *,
        asset_by_id: dict[int, AssetORM],
        market_values: dict[int, float],
        total_equity: float,
    ) -> PortfolioExposureModel:
        by_asset: dict[str, float] = {}
        by_sector: dict[str, float] = defaultdict(float)
        by_asset_type: dict[str, float] = defaultdict(float)
        if total_equity <= 0:
            return PortfolioExposureModel(
                total_invested_weight=0.0,
                by_asset={},
                by_sector={},
                by_asset_type={},
            )
        for asset_id, value in market_values.items():
            asset = asset_by_id[asset_id]
            weight = value / total_equity
            by_asset[asset.symbol] = weight
            by_sector[asset.sector] += weight
            by_asset_type[asset.asset_type] += weight
        return PortfolioExposureModel(
            total_invested_weight=sum(by_asset.values()),
            by_asset=by_asset,
            by_sector=dict(by_sector),
            by_asset_type=dict(by_asset_type),
        )

    @staticmethod
    def _holding_market_value(
        lots: list[dict[str, Any]],
        row_payload: tuple[int, pd.Series] | None,
    ) -> float:
        if row_payload is None:
            return 0.0
        _, row = row_payload
        return sum(lot["remaining_qty"] for lot in lots) * float(row["close"])

    def _persist_result(
        self,
        scenario: BacktestScenario,
        trades: list[SimulatedTrade],
        metrics: dict[str, float],
        segmented: dict[str, dict[str, Any]],
        *,
        portfolio_events: list[dict[str, Any]] | None = None,
        portfolio_summary: dict[str, Any] | None = None,
        run_name: str,
    ) -> tuple[int, int]:
        run = self.backtest_repo.create_run(
            {
                "name": run_name,
                "mode": scenario.mode.value,
                "start_date": scenario.start_date,
                "end_date": scenario.end_date,
                "assets_json": list(scenario.assets),
                "scenario_json": scenario.to_payload(),
                "status": "completed",
            }
        )
        parameter_set = self.backtest_repo.create_parameter_set(
            {
                "run_id": run.id,
                "name": run_name,
                "parameters_json": scenario.to_payload(),
                "evaluation_score": None,
                "in_sample_metrics_json": metrics,
                "out_of_sample_metrics_json": None,
            }
        )
        metric_payloads = [
            {
                "run_id": run.id,
                "scope": "all",
                "segment_type": "summary",
                "segment_value": "all",
                "metrics_json": metrics,
            }
        ]
        for segment_type, metric_map in segmented.items():
            for segment_value, segment_metrics in metric_map.items():
                metric_payloads.append(
                    {
                        "run_id": run.id,
                        "scope": "all",
                        "segment_type": segment_type,
                        "segment_value": segment_value,
                        "metrics_json": segment_metrics.to_dict(),
                    }
                )
        if portfolio_summary:
            metric_payloads.append(
                {
                    "run_id": run.id,
                    "scope": "portfolio",
                    "segment_type": "summary",
                    "segment_value": "portfolio_realistic",
                    "metrics_json": portfolio_summary,
                }
            )
        self.backtest_repo.add_metrics(metric_payloads)
        self.backtest_repo.add_trades(self._trade_payloads(run.id, parameter_set.id, trades))
        if portfolio_events:
            self.backtest_repo.add_portfolio_events(
                [
                    {
                        "run_id": run.id,
                        **event,
                    }
                    for event in portfolio_events
                ]
            )
        return run.id, parameter_set.id

    def _trade_payloads(
        self,
        run_id: int,
        parameter_set_id: int,
        trades: list[SimulatedTrade],
    ) -> list[dict[str, Any]]:
        payloads: list[dict[str, Any]] = []
        for trade in trades:
            payloads.append(
                {
                    "run_id": run_id,
                    "parameter_set_id": parameter_set_id,
                    "asset_id": trade.asset_id,
                    "symbol": trade.symbol,
                    "asset_type": trade.asset_type,
                    "sector": trade.sector,
                    "recommendation": trade.recommendation,
                    "score_band": self._score_band(trade.final_score),
                    "risk_band": self._risk_band(trade.risk_score),
                    "entry_signal_date": trade.entry_signal_date,
                    "entry_date": trade.entry_date,
                    "exit_date": trade.exit_date,
                    "entry_price": trade.entry_price,
                    "exit_price": trade.exit_price,
                    "position_pct": trade.position_pct,
                    "gross_return_pct": trade.gross_return_pct,
                    "net_return_pct": trade.net_return_pct,
                    "max_drawdown_pct": trade.max_drawdown_pct,
                    "mae_pct": trade.mae_pct,
                    "mfe_pct": trade.mfe_pct,
                    "holding_days": trade.holding_days,
                    "exit_reason": trade.exit_reason,
                    "technical_score": trade.technical_score,
                    "risk_score": trade.risk_score,
                    "portfolio_fit_score": trade.portfolio_fit_score,
                    "final_score": trade.final_score,
                    "invalidation_level": trade.invalidation_level,
                    "rationale_json": trade.rationale,
                    "parameters_json": trade.parameters,
                }
            )
        return payloads

    def _load_assets(self, symbols: tuple[str, ...]) -> list[AssetORM]:
        enabled = {asset.symbol: asset for asset in self.assets_repo.list_enabled()}
        return [enabled[symbol] for symbol in symbols if symbol in enabled]

    def _load_asset_frame(
        self,
        asset: AssetORM,
        start_date: date,
        end_date: date,
    ) -> pd.DataFrame:
        frame = self.prices_repo.get_asset_prices(asset.id)
        if frame.empty:
            return frame
        working = frame.copy()
        working["date"] = pd.to_datetime(working["date"])
        warmup_start = pd.Timestamp(start_date) - pd.Timedelta(days=400)
        working = working[
            (working["date"] >= warmup_start)
            & (working["date"] <= pd.Timestamp(end_date))
        ]
        return working.reset_index(drop=True)

    def _entry_allowed(
        self,
        signal: HistoricalSignal,
        scenario: BacktestScenario,
        *,
        asset: AssetORM | None = None,
        frame: pd.DataFrame | None = None,
        index: int | None = None,
    ) -> bool:
        if signal.final_score < scenario.entry_rules.min_final_score:
            return False
        if signal.risk_score > scenario.entry_rules.max_risk_score:
            return False
        if (
            scenario.entry_rules.max_distance_to_support_pct is not None
            and signal.distance_to_support_pct is not None
            and abs(signal.distance_to_support_pct)
            > scenario.entry_rules.max_distance_to_support_pct
        ):
            return False
        if (
            scenario.entry_rules.max_rsi14 is not None
            and signal.rsi14 is not None
            and signal.rsi14 > scenario.entry_rules.max_rsi14
        ):
            return False
        if scenario.entry_rules.require_bullish_trend and not signal.trend_bullish:
            return False
        if signal.recommendation not in scenario.entry_rules.allowed_recommendations:
            return False
        return self._market_regime_entry_allowed(
            signal,
            scenario,
            asset=asset,
            frame=frame,
            index=index,
        )

    def _market_regime_entry_allowed(
        self,
        signal: HistoricalSignal,
        scenario: BacktestScenario,
        *,
        asset: AssetORM | None,
        frame: pd.DataFrame | None,
        index: int | None,
    ) -> bool:
        rules = scenario.regime_filter_rules
        if not rules.enabled:
            return True
        if asset is None or frame is None or index is None:
            signal.rationale["market_regime_filter"] = {
                "allowed": False,
                "reason": "missing_regime_context",
            }
            return False

        regime = self._market_regime_for_point(asset, frame, index)
        multiplier = 1.0
        reason = "allowed"
        allowed = True
        if (
            rules.min_bull_probability is not None
            and regime.bull_probability < rules.min_bull_probability
        ):
            allowed = False
            reason = "bull_probability_below_min"
        if (
            allowed
            and rules.max_bear_probability is not None
            and regime.bear_probability > rules.max_bear_probability
        ):
            allowed = False
            reason = "bear_probability_above_max"
        if (
            allowed
            and rules.reduce_size_if_bubble_probability_gt is not None
            and regime.bubble_probability > rules.reduce_size_if_bubble_probability_gt
        ):
            multiplier = max(0.0, min(1.0, rules.bubble_position_size_multiplier))
            reason = "bubble_size_reduction"

        signal.rationale["market_regime_filter"] = {
            "allowed": allowed,
            "reason": reason,
            "bull_probability": regime.bull_probability,
            "bear_probability": regime.bear_probability,
            "bubble_probability": regime.bubble_probability,
            "dominant_regime": regime.dominant_regime,
            "position_size_multiplier": multiplier if allowed else 0.0,
        }
        return allowed

    def _market_regime_for_point(
        self,
        asset: AssetORM,
        frame: pd.DataFrame,
        index: int,
    ) -> MarketRegime:
        as_of_date = pd.Timestamp(frame.iloc[index]["date"]).date()
        cache_key = (asset.id, as_of_date)
        cached = self._market_regime_cache.get(cache_key)
        if cached is not None:
            return cached
        prepared = self._market_regime_prepared_frames.get(asset.id)
        if prepared is not None:
            regime = self.market_regime_service.compute_regime_from_prepared_frame(
                prepared,
                as_of_date=as_of_date,
            )
            self._market_regime_cache[cache_key] = regime
            return regime
        regime = self.market_regime_service.compute_regime(
            frame.iloc[: index + 1],
            as_of_date=as_of_date,
        )
        self._market_regime_cache[cache_key] = regime
        return regime

    def _position_pct(
        self,
        signal: HistoricalSignal,
        scenario: BacktestScenario,
        *,
        available_cash_pct: float,
    ) -> float:
        if scenario.execution_rules.position_size_mode.value == "suggested_weight_add":
            target = signal.suggested_weight_add_pct / 100
        else:
            target = scenario.execution_rules.fixed_position_pct
        target *= self._regime_position_size_multiplier(signal, scenario)
        return round(min(max(0.0, target), available_cash_pct), 4)

    @staticmethod
    def _regime_position_size_multiplier(
        signal: HistoricalSignal,
        scenario: BacktestScenario,
    ) -> float:
        if not scenario.regime_filter_rules.enabled:
            return 1.0
        regime_payload = signal.rationale.get("market_regime_filter", {})
        return float(regime_payload.get("position_size_multiplier", 1.0) or 0.0)

    def _exposure_from_open_trades(
        self,
        open_trades: list[SimulatedTrade],
    ) -> PortfolioExposureModel:
        by_asset: dict[str, float] = defaultdict(float)
        by_sector: dict[str, float] = defaultdict(float)
        by_asset_type: dict[str, float] = defaultdict(float)
        total = 0.0
        for trade in open_trades:
            by_asset[trade.symbol] += trade.position_pct
            by_sector[trade.sector] += trade.position_pct
            by_asset_type[trade.asset_type] += trade.position_pct
            total += trade.position_pct
        return PortfolioExposureModel(
            total_invested_weight=round(total, 4),
            by_asset=dict(by_asset),
            by_sector=dict(by_sector),
            by_asset_type=dict(by_asset_type),
        )

    def _violates_portfolio_constraints(
        self,
        signal: HistoricalSignal,
        exposure: PortfolioExposureModel,
        position_pct: float,
        scenario: BacktestScenario,
    ) -> bool:
        if not scenario.respect_sector_limits and not scenario.respect_asset_type_limits:
            return False
        if scenario.respect_sector_limits:
            sector_weight = exposure.by_sector.get(signal.sector, 0.0) + position_pct
            if sector_weight > self.rebalance_service.rules["limits"]["max_sector_weight"]:
                return True
        if scenario.respect_asset_type_limits:
            type_weight = exposure.by_asset_type.get(signal.asset_type, 0.0) + position_pct
            max_type = self.rebalance_service.rules["limits"]["max_asset_type_weight"].get(
                signal.asset_type,
                1.0,
            )
            if type_weight > max_type:
                return True
        return False

    def _position_alert_exit_type(
        self,
        *,
        asset: AssetORM,
        row: pd.Series,
        signal: HistoricalSignal,
        entry_price: float,
        position_pct: float,
        scenario: BacktestScenario,
    ) -> str | None:
        allowed_types = set(scenario.exit_rules.position_alert_exit_types)
        if not allowed_types:
            return None

        target_weight = max(position_pct, signal.suggested_weight_add_pct / 100)
        position = PositionContext(
            quantity=1.0,
            avg_cost=entry_price,
            current_weight=position_pct,
            target_weight=target_weight,
        )
        row_payload = {
            "last_price": float(row["close"]),
            "rsi14": signal.rsi14,
            "sma50": signal.sma50,
            "support_low": signal.support_low,
            "final_opportunity_score": signal.final_score,
            "risk_score": signal.risk_score,
            "recommendation": signal.recommendation,
        }
        alerts = self.position_management_alerts_service.detect_alerts(
            asset=asset,
            row=row_payload,
            position=position,
        )
        by_type = {alert.event_type: alert for alert in alerts}
        for event_type in [
            "exit_candidate",
            "stop_loss_warning",
            "reduce_risk",
            "take_profit",
            "trim_position",
            "rebalance_sell",
            "overbought_warning",
        ]:
            if event_type in allowed_types and event_type in by_type:
                return event_type
        return None

    @staticmethod
    def _build_warnings(total_trades: int, scenario: BacktestScenario) -> list[str]:
        warnings: list[str] = []
        if total_trades < scenario.evaluation_rules.min_trades_warning_threshold:
            warnings.append(
                "Numero de trades bajo; interpreta los resultados con cautela por riesgo "
                "de sobreajuste."
            )
        if scenario.mode == BacktestMode.PORTFOLIO:
            warnings.append(
                "La simulacion de cartera es basica: no modela mark-to-market "
                "intradiario ni correlaciones."
            )
        return warnings

    @staticmethod
    def _apply_slippage(price: float, slippage_bps: float, *, side: str) -> float:
        multiplier = 1 + (slippage_bps / 10000)
        return price * multiplier if side == "buy" else price / multiplier

    def _exit_payload(
        self,
        *,
        frame: pd.DataFrame,
        index: int,
        entry_index: int,
        exit_price: float,
        exit_reason: str,
        mae_pct: float,
        mfe_pct: float,
        max_drawdown_pct: float,
        slippage_bps: float,
    ) -> dict[str, Any]:
        exit_row = frame.iloc[index]
        adjusted_exit_price = self._apply_slippage(exit_price, slippage_bps, side="sell")
        return {
            "exit_date": pd.Timestamp(exit_row["date"]).date(),
            "exit_price": adjusted_exit_price,
            "holding_days": index - entry_index,
            "exit_reason": exit_reason,
            "mae_pct": abs(mae_pct),
            "mfe_pct": mfe_pct,
            "max_drawdown_pct": abs(max_drawdown_pct),
        }

    @staticmethod
    def _score_band(score: float) -> str:
        if score >= 75:
            return "75+"
        if score >= 65:
            return "65-74"
        if score >= 55:
            return "55-64"
        return "<55"

    @staticmethod
    def _risk_band(risk_score: float) -> str:
        if risk_score <= 33:
            return "low"
        if risk_score <= 66:
            return "medium"
        return "high"

    @staticmethod
    def _optional_float(value: object) -> float | None:
        if value is None or pd.isna(value):
            return None
        return float(value)
