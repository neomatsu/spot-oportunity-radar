from __future__ import annotations

from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from math import isfinite

import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from data.database import AssetORM, FxRateDailyORM, PriceBarDailyORM
from data.repositories.portfolio_repo import PortfolioRepository
from services.currency_service import AssetCurrencyResolver


@dataclass(frozen=True)
class PortfolioHistory:
    frame: pd.DataFrame
    excluded_symbols: tuple[str, ...] = ()


class PortfolioHistoryService:
    """Reconstruct end-of-day holdings using only information available by each date.

    Read cached prices/FX in bulk; rendering never triggers historical downloads.
    Cost is the remaining weighted-average acquisition cost, including buy expenses.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def history(self, *, as_of: date | None = None) -> PortfolioHistory:
        end = as_of or date.today()
        repo = PortfolioRepository(self.session)
        all_transactions = repo.list_transactions()
        transactions = [tx for tx in all_transactions if tx.transaction_date <= end]
        assets = {asset.id: asset for asset in self.session.scalars(select(AssetORM))}
        transaction_ids = {tx.asset_id for tx in all_transactions}
        excluded = tuple(
            assets[pos.asset_id].symbol
            for pos in repo.list_positions()
            if pos.quantity > 0 and pos.asset_id not in transaction_ids
        )
        columns = [
            "date",
            "invested_cost",
            "market_value",
            "missing_symbols",
            "oldest_price_date",
            "review_symbols",
        ]
        if not transactions:
            return PortfolioHistory(pd.DataFrame(columns=columns), excluded)

        rates: dict[str, list] = defaultdict(list)
        rate_dates: dict[str, list] = defaultdict(list)
        for rate in self.session.scalars(
            select(FxRateDailyORM)
            .where(FxRateDailyORM.target_currency == "EUR", FxRateDailyORM.date <= end)
            .order_by(FxRateDailyORM.date)
        ):
            rates[rate.source_currency].append(rate.rate)
            rate_dates[rate.source_currency].append(rate.date)

        def in_eur(value: float, currency: str | None, day: date) -> float | None:
            raw = currency or ""
            pence = raw == "GBp" or raw.upper() == "GBX"
            source = "GBP" if pence else raw.upper()
            if source == "EUR":
                return value if isfinite(value) else None
            idx = bisect_right(rate_dates[source], day) - 1
            if idx < 0:
                return None
            result = value * rates[source][idx] * (0.01 if pence else 1)
            return result if isfinite(result) else None

        resolver = AssetCurrencyResolver()
        currencies = {aid: resolver.resolve(assets[aid]) for aid in transaction_ids}
        prices: dict[int, list] = defaultdict(list)
        price_dates: dict[int, list] = defaultdict(list)
        for bar in self.session.scalars(
            select(PriceBarDailyORM)
            .where(PriceBarDailyORM.asset_id.in_(transaction_ids), PriceBarDailyORM.date <= end)
            .order_by(PriceBarDailyORM.date)
        ):
            prices[bar.asset_id].append(
                in_eur(bar.close, bar.quote_currency or currencies[bar.asset_id], bar.date)
            )
            price_dates[bar.asset_id].append(bar.date)

        quantities: dict[int, float] = defaultdict(float)
        costs: dict[int, float | None] = defaultdict(float)
        by_date: dict[date, list] = defaultdict(list)
        for tx in transactions:
            by_date[tx.transaction_date].append(tx)

        rows = []
        for timestamp in pd.date_range(transactions[0].transaction_date, end, freq="D"):
            day = timestamp.date()
            for tx in by_date[day]:
                aid = tx.asset_id
                if tx.transaction_type == "BUY":
                    amount = in_eur(
                        tx.gross_amount + tx.fees + tx.taxes, tx.transaction_currency, day
                    )
                    quantities[aid] += tx.quantity
                    costs[aid] = (
                        costs[aid] + amount
                        if costs[aid] is not None and amount is not None
                        else None
                    )
                elif tx.transaction_type == "SELL" and quantities[aid] > 0:
                    sold = min(tx.quantity, quantities[aid])
                    if costs[aid] is not None:
                        costs[aid] *= 1 - sold / quantities[aid]
                    quantities[aid] -= sold
                    if quantities[aid] <= 1e-9:
                        quantities[aid], costs[aid] = 0.0, 0.0

            value = 0.0
            missing = []
            review = []
            used_dates = []
            for aid, quantity in quantities.items():
                if quantity <= 1e-9:
                    continue
                idx = bisect_right(price_dates[aid], day) - 1
                price = prices[aid][idx] if idx >= 0 else None
                if price is None:
                    missing.append(assets[aid].symbol)
                else:
                    value += quantity * price
                    used_dates.append(price_dates[aid][idx])
                    cost = costs[aid]
                    if cost is not None and cost > 0:
                        ratio = quantity * price / cost
                        if ratio >= 4 or ratio <= 0.25:
                            review.append(assets[aid].symbol)
            rows.append(
                {
                    "date": timestamp,
                    "invested_cost": (
                        sum(costs.values())
                        if all(cost is not None for cost in costs.values())
                        else None
                    ),
                    "market_value": value if not missing else None,
                    "missing_symbols": ", ".join(sorted(missing)),
                    "oldest_price_date": min(used_dates) if used_dates else None,
                    "review_symbols": ", ".join(sorted(review)),
                }
            )
        return PortfolioHistory(pd.DataFrame(rows, columns=columns), excluded)
