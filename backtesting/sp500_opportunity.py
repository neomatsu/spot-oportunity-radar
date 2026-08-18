from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date

import numpy as np
import pandas as pd

from backtesting.bitcoin_opportunity import (
    BitcoinOpportunityBacktestConfig,
    BitcoinOpportunityBacktester,
)


@dataclass(frozen=True, slots=True)
class SP500OpportunityBacktestConfig:
    initial_capital: float = 100_000.0
    buy_sizing_basis: str = "initial_capital"
    buy_thresholds: tuple[float, float, float] = (65.0, 75.0, 85.0)
    buy_capital_pcts: tuple[float, float, float] = (0.10, 0.20, 0.30)
    sell_thresholds: tuple[float, float, float] = (25.0, 35.0, 45.0)
    sell_position_pcts: tuple[float, float, float] = (0.40, 0.25, 0.15)
    buy_reset_threshold: float = 55.0
    sell_reset_threshold: float = 55.0
    commission_bps: float = 8.0
    slippage_bps: float = 5.0
    minimum_trade_value: float = 50.0

    def __post_init__(self) -> None:
        self.to_engine_config()

    def to_engine_config(self) -> BitcoinOpportunityBacktestConfig:
        return BitcoinOpportunityBacktestConfig(
            initial_capital=self.initial_capital,
            buy_sizing_basis=self.buy_sizing_basis,
            buy_thresholds=self.buy_thresholds,
            buy_capital_pcts=self.buy_capital_pcts,
            sell_thresholds=self.sell_thresholds,
            sell_position_pcts=self.sell_position_pcts,
            buy_reset_threshold=self.buy_reset_threshold,
            sell_reset_threshold=self.sell_reset_threshold,
            commission_bps=self.commission_bps,
            slippage_bps=self.slippage_bps,
            minimum_trade_value=self.minimum_trade_value,
        )


@dataclass(frozen=True, slots=True)
class SP500OpportunityBacktestResult:
    config: SP500OpportunityBacktestConfig
    equity_curve: pd.DataFrame
    events: pd.DataFrame
    initial_capital: float
    final_equity: float
    total_return_pct: float
    benchmark_return_pct: float
    max_drawdown_pct: float
    cagr_pct: float
    sharpe: float
    sortino: float
    calmar: float
    realized_pnl: float
    unrealized_pnl: float
    final_cash: float
    final_quantity: float
    buy_count: int
    sell_count: int
    average_exposure_pct: float
    time_fully_in_cash_pct: float
    average_cash_pct: float
    turnover_pct: float

    @property
    def excess_return_pct(self) -> float:
        return self.total_return_pct - self.benchmark_return_pct

    @property
    def robustness_score(self) -> float:
        return self.cagr_pct - 0.35 * abs(self.max_drawdown_pct) + 2.0 * self.sharpe


class SP500OpportunityBacktester:
    """S&P adapter over the audited D+1 staged-allocation engine used by Bitcoin."""

    def __init__(self) -> None:
        self.engine = BitcoinOpportunityBacktester()

    def run(
        self,
        history: pd.DataFrame,
        config: SP500OpportunityBacktestConfig,
    ) -> SP500OpportunityBacktestResult:
        prepared = self._prepare_history(history)
        engine_history = prepared.rename(columns={"sp500_price": "bitcoin_price"})
        base = self.engine.run(engine_history, config.to_engine_config())
        curve = base.equity_curve.rename(columns={"bitcoin_price": "sp500_price"})
        daily_returns = curve["equity"].pct_change().dropna()
        years = max(
            (pd.Timestamp(curve["date"].iloc[-1]) - pd.Timestamp(curve["date"].iloc[0])).days
            / 365.25,
            1 / 365.25,
        )
        cagr = ((base.final_equity / base.initial_capital) ** (1 / years) - 1.0) * 100.0
        sharpe = self._ratio(daily_returns.mean(), daily_returns.std(ddof=0)) * np.sqrt(252)
        downside = daily_returns[daily_returns < 0]
        sortino = self._ratio(daily_returns.mean(), downside.std(ddof=0)) * np.sqrt(252)
        calmar = cagr / abs(base.max_drawdown_pct) if base.max_drawdown_pct < 0 else np.nan
        exposure = pd.to_numeric(curve["exposure_pct"], errors="coerce").fillna(0.0)
        average_cash_pct = (
            pd.to_numeric(curve["cash"], errors="coerce")
            / pd.to_numeric(curve["equity"], errors="coerce").replace(0, np.nan)
        ).mean() * 100.0
        gross_turnover = (
            pd.to_numeric(base.events.get("gross_value"), errors="coerce").sum()
            if not base.events.empty
            else 0.0
        )
        return SP500OpportunityBacktestResult(
            config=config,
            equity_curve=curve,
            events=base.events,
            initial_capital=base.initial_capital,
            final_equity=base.final_equity,
            total_return_pct=base.total_return_pct,
            benchmark_return_pct=base.benchmark_return_pct,
            max_drawdown_pct=base.max_drawdown_pct,
            cagr_pct=float(cagr),
            sharpe=float(sharpe),
            sortino=float(sortino),
            calmar=float(calmar),
            realized_pnl=base.realized_pnl,
            unrealized_pnl=base.unrealized_pnl,
            final_cash=base.final_cash,
            final_quantity=base.final_quantity,
            buy_count=base.buy_count,
            sell_count=base.sell_count,
            average_exposure_pct=float(exposure.mean()),
            time_fully_in_cash_pct=float((exposure < 0.01).mean() * 100.0),
            average_cash_pct=float(average_cash_pct),
            turnover_pct=float(gross_turnover / curve["equity"].mean() * 100.0),
        )

    def sensitivity_analysis(
        self,
        history: pd.DataFrame,
        base_config: SP500OpportunityBacktestConfig,
        *,
        threshold_offsets: list[float],
        sizing_multipliers: list[float] | None = None,
    ) -> pd.DataFrame:
        multipliers = sizing_multipliers or [0.75, 1.0, 1.25]
        rows: list[dict[str, float | int | str]] = []
        for offset in sorted(set(float(value) for value in threshold_offsets)):
            for multiplier in sorted(set(float(value) for value in multipliers)):
                buy_thresholds = tuple(
                    max(base_config.buy_reset_threshold + 1, min(100.0, value + offset))
                    for value in base_config.buy_thresholds
                )
                sell_thresholds = tuple(
                    max(0.0, min(base_config.sell_reset_threshold - 1, value + offset))
                    for value in base_config.sell_thresholds
                )
                if len(set(buy_thresholds)) < 3 or len(set(sell_thresholds)) < 3:
                    continue
                config = SP500OpportunityBacktestConfig(
                    **{
                        **asdict(base_config),
                        "buy_thresholds": buy_thresholds,
                        "sell_thresholds": sell_thresholds,
                        "buy_capital_pcts": tuple(
                            min(1.0, value * multiplier)
                            for value in base_config.buy_capital_pcts
                        ),
                        "sell_position_pcts": tuple(
                            min(1.0, value * multiplier)
                            for value in base_config.sell_position_pcts
                        ),
                    }
                )
                result = self.run(history, config)
                rows.append(self._result_row(result, offset, multiplier))
        return pd.DataFrame(rows).sort_values(
            ["robustness_score", "cagr_pct"], ascending=False
        ).reset_index(drop=True)

    def in_sample_out_of_sample(
        self,
        history: pd.DataFrame,
        configs: list[SP500OpportunityBacktestConfig],
        *,
        split_date: date,
    ) -> pd.DataFrame:
        frame = self._prepare_history(history)
        train = frame[frame["date"].dt.date < split_date]
        test = frame[frame["date"].dt.date >= split_date]
        if len(train) < 3 or len(test) < 3:
            raise ValueError("Training and validation require at least three observations")
        rows: list[dict[str, float | int | str]] = []
        for index, config in enumerate(configs):
            train_result = self.run(train, config)
            test_result = self.run(test, config)
            rows.append(
                {
                    "config_id": index + 1,
                    "train_start": train["date"].min().date(),
                    "train_end": train["date"].max().date(),
                    "test_start": test["date"].min().date(),
                    "test_end": test["date"].max().date(),
                    "train_cagr_pct": train_result.cagr_pct,
                    "train_max_drawdown_pct": train_result.max_drawdown_pct,
                    "train_sharpe": train_result.sharpe,
                    "train_robustness_score": train_result.robustness_score,
                    "test_cagr_pct": test_result.cagr_pct,
                    "test_max_drawdown_pct": test_result.max_drawdown_pct,
                    "test_sharpe": test_result.sharpe,
                    "test_robustness_score": test_result.robustness_score,
                    "robustness_gap": (
                        train_result.robustness_score - test_result.robustness_score
                    ),
                    "config": str(asdict(config)),
                }
            )
        return pd.DataFrame(rows).sort_values(
            "train_robustness_score", ascending=False
        ).reset_index(drop=True)

    def walk_forward(
        self,
        history: pd.DataFrame,
        configs: list[SP500OpportunityBacktestConfig],
        folds: list[tuple[date, date, date, date]],
    ) -> pd.DataFrame:
        frame = self._prepare_history(history)
        rows: list[dict[str, float | int | str]] = []
        for fold_index, (train_start, train_end, test_start, test_end) in enumerate(folds):
            train = frame[
                (frame["date"].dt.date >= train_start)
                & (frame["date"].dt.date <= train_end)
            ]
            test = frame[
                (frame["date"].dt.date >= test_start)
                & (frame["date"].dt.date <= test_end)
            ]
            if len(train) < 3 or len(test) < 3:
                continue
            ranked = sorted(
                ((self.run(train, config), config) for config in configs),
                key=lambda item: item[0].robustness_score,
                reverse=True,
            )
            train_result, selected = ranked[0]
            test_result = self.run(test, selected)
            rows.append(
                {
                    "fold": fold_index + 1,
                    "train_start": train_start,
                    "train_end": train_end,
                    "test_start": test_start,
                    "test_end": test_end,
                    "train_score": train_result.robustness_score,
                    "test_score": test_result.robustness_score,
                    "test_cagr_pct": test_result.cagr_pct,
                    "test_max_drawdown_pct": test_result.max_drawdown_pct,
                    "selected_config": str(asdict(selected)),
                }
            )
        return pd.DataFrame(rows)

    @staticmethod
    def _result_row(
        result: SP500OpportunityBacktestResult,
        offset: float,
        multiplier: float,
    ) -> dict[str, float | int | str]:
        return {
            "threshold_offset": offset,
            "sizing_multiplier": multiplier,
            "buy_thresholds": str(result.config.buy_thresholds),
            "sell_thresholds": str(result.config.sell_thresholds),
            "total_return_pct": result.total_return_pct,
            "benchmark_return_pct": result.benchmark_return_pct,
            "cagr_pct": result.cagr_pct,
            "max_drawdown_pct": result.max_drawdown_pct,
            "sharpe": result.sharpe,
            "sortino": result.sortino,
            "calmar": result.calmar,
            "average_exposure_pct": result.average_exposure_pct,
            "turnover_pct": result.turnover_pct,
            "buys": result.buy_count,
            "sells": result.sell_count,
            "robustness_score": result.robustness_score,
        }

    @staticmethod
    def _prepare_history(history: pd.DataFrame) -> pd.DataFrame:
        required = ["date", "overall_score", "sp500_price"]
        missing = [column for column in required if column not in history.columns]
        if missing:
            raise ValueError(f"Missing historical columns: {missing}")
        frame = history[required].copy()
        frame["date"] = pd.to_datetime(frame["date"])
        frame["overall_score"] = pd.to_numeric(frame["overall_score"], errors="coerce")
        frame["sp500_price"] = pd.to_numeric(frame["sp500_price"], errors="coerce")
        return (
            frame.dropna()
            .sort_values("date")
            .drop_duplicates("date", keep="last")
            .reset_index(drop=True)
        )

    @staticmethod
    def _ratio(numerator: float, denominator: float) -> float:
        if denominator is None or np.isnan(denominator) or denominator <= 0:
            return np.nan
        return float(numerator / denominator)
