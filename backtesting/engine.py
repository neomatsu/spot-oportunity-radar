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

    def run(
        self,
        scenario: BacktestScenario,
        *,
        persist: bool = True,
        run_name: str | None = None,
    ) -> BacktestRunResult:
        assets = self._load_assets(scenario.assets)
        frames = {
            asset.id: self._load_asset_frame(asset, scenario.start_date, scenario.end_date)
            for asset in assets
        }

        if scenario.mode == BacktestMode.PORTFOLIO:
            trades = self._run_portfolio_mode(assets, frames, scenario)
        else:
            trades = self._run_trade_by_trade_mode(assets, frames, scenario)

        metrics = summarize_trades(trades, initial_capital=scenario.initial_capital)
        segmented = segment_trades(trades, initial_capital=scenario.initial_capital)
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
                if signal is None or not self._entry_allowed(signal, scenario):
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
                if signal is None or not self._entry_allowed(signal, scenario):
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

    def _resolve_exit(
        self,
        *,
        asset: AssetORM,
        frame: pd.DataFrame,
        signal: HistoricalSignal,
        signal_index: int,
        entry_index: int,
        entry_price: float,
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

            if scenario.exit_rules.strategy.value in {"signal_loss", "hybrid"}:
                future_signal = self.evaluate_signal_point(asset, frame, index, future_exposure)
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

            horizon_hit = index >= entry_index + scenario.exit_rules.fixed_horizon_days
            if (
                scenario.exit_rules.strategy.value == "fixed_horizon"
                or scenario.exit_rules.strategy.value == "hybrid"
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

    def _persist_result(
        self,
        scenario: BacktestScenario,
        trades: list[SimulatedTrade],
        metrics: dict[str, float],
        segmented: dict[str, dict[str, Any]],
        *,
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
        self.backtest_repo.add_metrics(metric_payloads)
        self.backtest_repo.add_trades(self._trade_payloads(run.id, parameter_set.id, trades))
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

    def _entry_allowed(self, signal: HistoricalSignal, scenario: BacktestScenario) -> bool:
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
        return signal.recommendation in scenario.entry_rules.allowed_recommendations

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
        return round(min(max(0.0, target), available_cash_pct), 4)

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
