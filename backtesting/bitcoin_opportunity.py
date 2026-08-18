from __future__ import annotations

from dataclasses import dataclass
from itertools import combinations, combinations_with_replacement

import pandas as pd


@dataclass(frozen=True)
class BitcoinOpportunityBacktestConfig:
    initial_capital: float = 100_000.0
    buy_sizing_basis: str = "initial_capital"
    buy_thresholds: tuple[float, float, float] = (70.0, 75.0, 80.0)
    buy_capital_pcts: tuple[float, float, float] = (0.10, 0.15, 0.25)
    sell_thresholds: tuple[float, float, float] = (20.0, 25.0, 30.0)
    sell_position_pcts: tuple[float, float, float] = (0.50, 0.30, 0.20)
    buy_reset_threshold: float = 60.0
    sell_reset_threshold: float = 40.0
    commission_bps: float = 8.0
    slippage_bps: float = 5.0
    minimum_trade_value: float = 50.0

    def __post_init__(self) -> None:
        if self.initial_capital <= 0:
            raise ValueError("initial_capital must be positive")
        if self.buy_sizing_basis not in {"initial_capital", "available_cash"}:
            raise ValueError(
                "buy_sizing_basis must be 'initial_capital' or 'available_cash'"
            )
        if tuple(sorted(self.buy_thresholds)) != self.buy_thresholds:
            raise ValueError("buy_thresholds must be ascending")
        if tuple(sorted(self.sell_thresholds)) != self.sell_thresholds:
            raise ValueError("sell_thresholds must be ascending")
        if self.buy_reset_threshold >= self.buy_thresholds[0]:
            raise ValueError("buy_reset_threshold must be below the first buy threshold")
        if self.sell_reset_threshold <= self.sell_thresholds[-1]:
            raise ValueError("sell_reset_threshold must be above the last sell threshold")
        for value in (*self.buy_capital_pcts, *self.sell_position_pcts):
            if not 0 <= value <= 1:
                raise ValueError("trade percentages must be between 0 and 1")


@dataclass(frozen=True)
class BitcoinOpportunityBacktestResult:
    config: BitcoinOpportunityBacktestConfig
    equity_curve: pd.DataFrame
    events: pd.DataFrame
    initial_capital: float
    final_equity: float
    total_return_pct: float
    benchmark_return_pct: float
    max_drawdown_pct: float
    realized_pnl: float
    unrealized_pnl: float
    final_cash: float
    final_quantity: float
    buy_count: int
    sell_count: int

    @property
    def excess_return_pct(self) -> float:
        return self.total_return_pct - self.benchmark_return_pct

    @property
    def strategy_score(self) -> float:
        return self.total_return_pct - 0.5 * abs(self.max_drawdown_pct)


class BitcoinOpportunityBacktester:
    """Long-only staged allocation driven by cached daily opportunity scores."""

    def run(
        self,
        history: pd.DataFrame,
        config: BitcoinOpportunityBacktestConfig,
    ) -> BitcoinOpportunityBacktestResult:
        frame = self._prepare_history(history)
        if len(frame) < 3:
            raise ValueError("At least three valid historical observations are required")

        cash = config.initial_capital
        quantity = 0.0
        cost_basis = 0.0
        realized_pnl = 0.0
        pending: dict[str, object] | None = None
        used_buy_thresholds: set[float] = set()
        used_sell_thresholds: set[float] = set()
        events: list[dict[str, object]] = []
        curve: list[dict[str, float | object]] = []

        for index, row in frame.iterrows():
            price = float(row["bitcoin_price"])
            if pending is not None:
                cash, quantity, cost_basis, pnl, event = self._execute_pending(
                    pending=pending,
                    date=row["date"],
                    market_price=price,
                    cash=cash,
                    quantity=quantity,
                    cost_basis=cost_basis,
                    config=config,
                )
                realized_pnl += pnl
                if event is not None:
                    events.append(event)
                pending = None

            position_value = quantity * price
            equity = cash + position_value
            curve.append(
                {
                    "date": row["date"],
                    "bitcoin_price": price,
                    "overall_score": float(row["overall_score"]),
                    "cash": cash,
                    "position_value": position_value,
                    "equity": equity,
                    "exposure_pct": position_value / equity * 100 if equity > 0 else 0.0,
                }
            )

            if index == 0 or index >= len(frame) - 1:
                continue
            previous_score = float(frame.iloc[index - 1]["overall_score"])
            current_score = float(row["overall_score"])
            if current_score < config.buy_reset_threshold:
                used_buy_thresholds.clear()
            if current_score > config.sell_reset_threshold:
                used_sell_thresholds.clear()
            crossed_buys = [
                (threshold, pct)
                for threshold, pct in zip(
                    config.buy_thresholds, config.buy_capital_pcts, strict=True
                )
                if previous_score < threshold <= current_score
                and threshold not in used_buy_thresholds
                and pct > 0
            ]
            crossed_sells = [
                (threshold, pct)
                for threshold, pct in zip(
                    config.sell_thresholds, config.sell_position_pcts, strict=True
                )
                if previous_score > threshold >= current_score
                and threshold not in used_sell_thresholds
                and pct > 0
            ]
            if crossed_buys:
                used_buy_thresholds.update(item[0] for item in crossed_buys)
                pending = {
                    "action": "BUY",
                    "signal_date": row["date"],
                    "signal_score": current_score,
                    "thresholds": [item[0] for item in crossed_buys],
                    "fraction": min(sum(item[1] for item in crossed_buys), 1.0),
                }
            elif crossed_sells and quantity > 0:
                used_sell_thresholds.update(item[0] for item in crossed_sells)
                pending = {
                    "action": "SELL",
                    "signal_date": row["date"],
                    "signal_score": current_score,
                    "thresholds": [item[0] for item in crossed_sells],
                    "fraction": min(sum(item[1] for item in crossed_sells), 1.0),
                }

        equity_curve = pd.DataFrame(curve)
        equity_curve["peak"] = equity_curve["equity"].cummax()
        equity_curve["drawdown_pct"] = (
            equity_curve["equity"] / equity_curve["peak"] - 1
        ) * 100
        final_price = float(frame.iloc[-1]["bitcoin_price"])
        final_equity = cash + quantity * final_price
        average_cost = cost_basis / quantity if quantity > 0 else 0.0
        unrealized_pnl = quantity * (final_price - average_cost) if quantity > 0 else 0.0
        benchmark_return = (final_price / float(frame.iloc[0]["bitcoin_price"]) - 1) * 100
        event_frame = pd.DataFrame(events)
        return BitcoinOpportunityBacktestResult(
            config=config,
            equity_curve=equity_curve,
            events=event_frame,
            initial_capital=config.initial_capital,
            final_equity=final_equity,
            total_return_pct=(final_equity / config.initial_capital - 1) * 100,
            benchmark_return_pct=benchmark_return,
            max_drawdown_pct=float(equity_curve["drawdown_pct"].min()),
            realized_pnl=realized_pnl,
            unrealized_pnl=unrealized_pnl,
            final_cash=cash,
            final_quantity=quantity,
            buy_count=(
                int((event_frame.get("action") == "BUY").sum())
                if not event_frame.empty
                else 0
            ),
            sell_count=(
                int((event_frame.get("action") == "SELL").sum())
                if not event_frame.empty
                else 0
            ),
        )

    def optimize_percentages(
        self,
        history: pd.DataFrame,
        base_config: BitcoinOpportunityBacktestConfig,
        candidates: list[float],
    ) -> pd.DataFrame:
        values = sorted({float(value) for value in candidates if 0 <= value <= 1})
        if not values:
            raise ValueError("At least one percentage candidate is required")
        buy_profiles = list(combinations_with_replacement(values, 3))
        sell_profiles = [tuple(reversed(profile)) for profile in buy_profiles]
        rows: list[dict[str, float | int]] = []
        for buy_profile in buy_profiles:
            for sell_profile in sell_profiles:
                config = BitcoinOpportunityBacktestConfig(
                    initial_capital=base_config.initial_capital,
                    buy_sizing_basis=base_config.buy_sizing_basis,
                    buy_thresholds=base_config.buy_thresholds,
                    buy_capital_pcts=buy_profile,
                    sell_thresholds=base_config.sell_thresholds,
                    sell_position_pcts=sell_profile,
                    buy_reset_threshold=base_config.buy_reset_threshold,
                    sell_reset_threshold=base_config.sell_reset_threshold,
                    commission_bps=base_config.commission_bps,
                    slippage_bps=base_config.slippage_bps,
                    minimum_trade_value=base_config.minimum_trade_value,
                )
                result = self.run(history, config)
                rows.append(
                    {
                        "buy_1_pct": buy_profile[0] * 100,
                        "buy_2_pct": buy_profile[1] * 100,
                        "buy_3_pct": buy_profile[2] * 100,
                        "sell_1_pct": sell_profile[0] * 100,
                        "sell_2_pct": sell_profile[1] * 100,
                        "sell_3_pct": sell_profile[2] * 100,
                        "total_return_pct": result.total_return_pct,
                        "benchmark_return_pct": result.benchmark_return_pct,
                        "excess_return_pct": result.excess_return_pct,
                        "max_drawdown_pct": result.max_drawdown_pct,
                        "final_equity": result.final_equity,
                        "buys": result.buy_count,
                        "sells": result.sell_count,
                        "strategy_score": result.strategy_score,
                    }
                )
        return pd.DataFrame(rows).sort_values(
            ["strategy_score", "total_return_pct"], ascending=False
        ).reset_index(drop=True)

    def optimize_thresholds(
        self,
        history: pd.DataFrame,
        base_config: BitcoinOpportunityBacktestConfig,
        *,
        buy_candidates: list[float],
        sell_candidates: list[float],
    ) -> pd.DataFrame:
        """Evaluate distinct ordered threshold triplets with fixed sizing rules."""
        buy_values = sorted(
            {
                float(value)
                for value in buy_candidates
                if base_config.buy_reset_threshold < float(value) <= 100
            }
        )
        sell_values = sorted(
            {
                float(value)
                for value in sell_candidates
                if 0 <= float(value) < base_config.sell_reset_threshold
            }
        )
        if len(buy_values) < 3 or len(sell_values) < 3:
            raise ValueError("At least three valid buy and sell thresholds are required")

        rows: list[dict[str, float | int]] = []
        for buy_thresholds in combinations(buy_values, 3):
            for sell_thresholds in combinations(sell_values, 3):
                config = BitcoinOpportunityBacktestConfig(
                    initial_capital=base_config.initial_capital,
                    buy_sizing_basis=base_config.buy_sizing_basis,
                    buy_thresholds=buy_thresholds,
                    buy_capital_pcts=base_config.buy_capital_pcts,
                    sell_thresholds=sell_thresholds,
                    sell_position_pcts=base_config.sell_position_pcts,
                    buy_reset_threshold=base_config.buy_reset_threshold,
                    sell_reset_threshold=base_config.sell_reset_threshold,
                    commission_bps=base_config.commission_bps,
                    slippage_bps=base_config.slippage_bps,
                    minimum_trade_value=base_config.minimum_trade_value,
                )
                result = self.run(history, config)
                rows.append(
                    {
                        "buy_threshold_1": buy_thresholds[0],
                        "buy_threshold_2": buy_thresholds[1],
                        "buy_threshold_3": buy_thresholds[2],
                        "sell_threshold_1": sell_thresholds[0],
                        "sell_threshold_2": sell_thresholds[1],
                        "sell_threshold_3": sell_thresholds[2],
                        "total_return_pct": result.total_return_pct,
                        "benchmark_return_pct": result.benchmark_return_pct,
                        "excess_return_pct": result.excess_return_pct,
                        "max_drawdown_pct": result.max_drawdown_pct,
                        "final_equity": result.final_equity,
                        "buys": result.buy_count,
                        "sells": result.sell_count,
                        "strategy_score": result.strategy_score,
                    }
                )
        return pd.DataFrame(rows).sort_values(
            ["strategy_score", "total_return_pct"], ascending=False
        ).reset_index(drop=True)

    @staticmethod
    def _prepare_history(history: pd.DataFrame) -> pd.DataFrame:
        required = ["date", "overall_score", "bitcoin_price"]
        missing = [column for column in required if column not in history.columns]
        if missing:
            raise ValueError(f"Missing historical columns: {', '.join(missing)}")
        frame = history[required].copy()
        frame["date"] = pd.to_datetime(frame["date"])
        frame["overall_score"] = pd.to_numeric(frame["overall_score"], errors="coerce")
        frame["bitcoin_price"] = pd.to_numeric(frame["bitcoin_price"], errors="coerce")
        return (
            frame.dropna()
            .sort_values("date")
            .drop_duplicates("date", keep="last")
            .reset_index(drop=True)
        )

    @staticmethod
    def _execute_pending(
        *,
        pending: dict[str, object],
        date: object,
        market_price: float,
        cash: float,
        quantity: float,
        cost_basis: float,
        config: BitcoinOpportunityBacktestConfig,
    ) -> tuple[float, float, float, float, dict[str, object] | None]:
        fraction = float(pending["fraction"])
        commission_rate = config.commission_bps / 10_000
        slippage_rate = config.slippage_bps / 10_000
        action = str(pending["action"])
        cash_before = cash
        quantity_before = quantity
        realized_pnl = 0.0

        if action == "BUY":
            execution_price = market_price * (1 + slippage_rate)
            sizing_base = (
                cash
                if config.buy_sizing_basis == "available_cash"
                else config.initial_capital
            )
            requested = sizing_base * fraction
            gross_value = min(requested, cash / (1 + commission_rate))
            if gross_value < config.minimum_trade_value:
                return cash, quantity, cost_basis, 0.0, None
            fee = gross_value * commission_rate
            traded_quantity = gross_value / execution_price
            cash -= gross_value + fee
            if abs(cash) < 1e-8:
                cash = 0.0
            quantity += traded_quantity
            cost_basis += gross_value + fee
        else:
            sizing_base = quantity
            execution_price = market_price * (1 - slippage_rate)
            traded_quantity = quantity * fraction
            gross_value = traded_quantity * execution_price
            if gross_value < config.minimum_trade_value:
                return cash, quantity, cost_basis, 0.0, None
            fee = gross_value * commission_rate
            average_cost = cost_basis / quantity if quantity > 0 else 0.0
            released_cost = average_cost * traded_quantity
            realized_pnl = gross_value - fee - released_cost
            cash += gross_value - fee
            quantity -= traded_quantity
            cost_basis = max(0.0, cost_basis - released_cost)
            if quantity < 1e-12:
                quantity = 0.0
                cost_basis = 0.0

        event = {
            "signal_date": pending["signal_date"],
            "execution_date": date,
            "action": action,
            "trigger_thresholds": ", ".join(
                f"{float(value):g}" for value in pending["thresholds"]  # type: ignore[union-attr]
            ),
            "signal_score": pending["signal_score"],
            "applied_pct": fraction * 100,
            "sizing_basis": (
                config.buy_sizing_basis if action == "BUY" else "current_position"
            ),
            "sizing_base": sizing_base,
            "price": execution_price,
            "quantity": traded_quantity,
            "gross_value": gross_value,
            "fees": fee,
            "cash_before": cash_before,
            "cash_after": cash,
            "quantity_before": quantity_before,
            "quantity_after": quantity,
            "realized_pnl": realized_pnl,
        }
        return cash, quantity, cost_basis, realized_pnl, event
