from __future__ import annotations

from collections import defaultdict
from datetime import date
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from core.models import PortfolioExposureModel
from data.database import AssetORM
from data.repositories.portfolio_repo import PortfolioRepository
from data.repositories.prices_repo import PricesRepository
from services.currency_service import CurrencyConversion, CurrencyService


class PortfolioService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.repo = PortfolioRepository(session)
        self.prices_repo = PricesRepository(session)
        self.currency_service = CurrencyService(session, base_currency="EUR")

    def get_exposures(self) -> PortfolioExposureModel:
        positions = self.repo.list_positions()
        assets = {asset.id: asset for asset in self.session.scalars(select(AssetORM)).all()}

        by_asset: dict[str, float] = {}
        by_sector: dict[str, float] = defaultdict(float)
        by_asset_type: dict[str, float] = defaultdict(float)
        total = 0.0

        for position in positions:
            asset = assets.get(position.asset_id)
            if asset is None:
                continue
            by_asset[asset.symbol] = position.current_weight
            by_sector[asset.sector] += position.current_weight
            by_asset_type[asset.asset_type] += position.current_weight
            total += position.current_weight

        return PortfolioExposureModel(
            total_invested_weight=total,
            by_asset=by_asset,
            by_sector=dict(by_sector),
            by_asset_type=dict(by_asset_type),
        )

    def get_total_capital(self, default: float = 0.0) -> float:
        return self.repo.get_total_capital(default=default)

    def set_total_capital(self, total_capital: float) -> None:
        self.repo.set_total_capital(total_capital)
        self.recalculate_positions()

    def price_for_date(
        self,
        asset_id: int,
        transaction_date: date,
    ) -> tuple[float | None, str, date | None]:
        prices = self.prices_repo.get_asset_prices(asset_id)
        if prices.empty:
            return None, "missing", None
        prices = prices.copy()
        prices["date"] = prices["date"].apply(
            lambda value: value.date() if hasattr(value, "date") else value
        )
        asset = self.session.get(AssetORM, asset_id)
        exact = prices[prices["date"] == transaction_date]
        if not exact.empty:
            row = exact.iloc[-1]
            return self._price_row_in_eur(asset, row, "exact_close")
        previous = prices[prices["date"] <= transaction_date]
        if not previous.empty:
            row = previous.iloc[-1]
            return self._price_row_in_eur(asset, row, "previous_close")
        next_row = prices[prices["date"] > transaction_date]
        if not next_row.empty:
            row = next_row.iloc[0]
            return self._price_row_in_eur(asset, row, "next_close")
        return None, "missing", None

    def add_transaction_and_recalculate(
        self,
        *,
        asset_id: int,
        transaction_type: str,
        transaction_date: date,
        quantity: float | None,
        gross_amount: float | None,
        price: float,
        fees: float = 0.0,
        taxes: float = 0.0,
        price_source: str = "manual",
        notes: str | None = None,
        external_source: str | None = None,
        external_transaction_id: str | None = None,
        external_payload_json: dict | None = None,
        transaction_currency: str = "EUR",
    ) -> None:
        transaction_type = transaction_type.upper()
        if transaction_type not in {"BUY", "SELL"}:
            raise ValueError("transaction_type must be BUY or SELL")
        if price <= 0:
            raise ValueError("price must be positive")
        if quantity is None or quantity <= 0:
            if gross_amount is None or gross_amount <= 0:
                raise ValueError("quantity or gross_amount must be positive")
            quantity = gross_amount / price
        if gross_amount is None or gross_amount <= 0:
            gross_amount = quantity * price
        if transaction_type == "SELL":
            available_quantity = self._available_quantity(asset_id)
            if quantity > available_quantity + 1e-9:
                raise ValueError("cannot sell more units than currently held")

        self.repo.add_transaction(
            asset_id=asset_id,
            transaction_type=transaction_type,
            transaction_date=transaction_date,
            quantity=quantity,
            price=price,
            gross_amount=gross_amount,
            fees=fees,
            taxes=taxes,
            price_source=price_source,
            notes=notes,
            external_source=external_source,
            external_transaction_id=external_transaction_id,
            external_payload_json=external_payload_json,
            transaction_currency=transaction_currency,
        )
        self.recalculate_positions()

    def delete_transaction_and_recalculate(self, transaction_id: int) -> bool:
        deleted = self.repo.delete_transaction(transaction_id)
        if deleted:
            self.recalculate_positions()
        return deleted

    def recalculate_positions(self) -> None:
        transactions = self.repo.list_transactions()
        total_capital = self.get_total_capital(default=0.0)
        existing_positions = self.repo.list_positions()
        target_weights = {
            position.asset_id: position.target_weight for position in existing_positions
        }
        state: dict[int, dict[str, float]] = defaultdict(
            lambda: {"quantity": 0.0, "cost_basis": 0.0}
        )

        for tx in transactions:
            bucket = state[tx.asset_id]
            if tx.transaction_type == "BUY":
                bucket["quantity"] += tx.quantity
                bucket["cost_basis"] += tx.gross_amount + tx.fees + tx.taxes
                continue

            if tx.transaction_type == "SELL":
                if bucket["quantity"] <= 0:
                    continue
                sell_qty = min(tx.quantity, bucket["quantity"])
                avg_cost = bucket["cost_basis"] / bucket["quantity"]
                bucket["quantity"] -= sell_qty
                bucket["cost_basis"] -= avg_cost * sell_qty
                if bucket["quantity"] <= 1e-9:
                    bucket["quantity"] = 0.0
                    bucket["cost_basis"] = 0.0

        transaction_asset_ids = {tx.asset_id for tx in transactions}
        for position in existing_positions:
            if position.asset_id in transaction_asset_ids or position.quantity <= 0:
                continue
            state[position.asset_id] = {
                "quantity": float(position.quantity),
                "cost_basis": float(position.quantity) * float(position.avg_cost),
            }

        aggregates: dict[int, dict[str, float]] = {}
        for asset_id, values in state.items():
            quantity = values["quantity"]
            if quantity <= 0:
                continue
            valuation = self._latest_valuation(asset_id)
            current_price_eur = valuation[1]
            current_value = quantity * current_price_eur if current_price_eur is not None else 0.0
            aggregates[asset_id] = {
                "quantity": quantity,
                "avg_cost": values["cost_basis"] / quantity,
                "current_weight": (
                    current_value / total_capital
                    if total_capital > 0 and current_value > 0
                    else 0.0
                ),
            }
        self.repo.replace_positions_from_aggregates(
            aggregates,
            target_weights=target_weights,
        )

    def portfolio_rows(self) -> list[dict[str, Any]]:
        assets = {asset.id: asset for asset in self.session.scalars(select(AssetORM)).all()}
        rows = []
        for position in self.repo.list_positions():
            asset = assets.get(position.asset_id)
            if asset is None:
                continue
            native_price, current_price, quote_currency, conversion, price_date = (
                self._latest_valuation(asset.id)
            )
            current_value = current_price * position.quantity if current_price is not None else None
            cost_basis = position.avg_cost * position.quantity
            ratio = (
                current_price / position.avg_cost
                if current_price and position.avg_cost
                else None
            )
            warning = None
            if current_price is None:
                warning = f"Sin cambio {quote_currency or 'desconocido'}/EUR"
            elif ratio is not None and (ratio >= 4.0 or ratio <= 0.25):
                warning = "Revisar mapeo del activo o escala del precio"
            rows.append(
                {
                    "symbol": asset.symbol,
                    "asset_type": asset.asset_type,
                    "sector": asset.sector,
                    "quantity": position.quantity,
                    "avg_cost": position.avg_cost,
                    "current_price": current_price,
                    "current_price_native": native_price,
                    "quote_currency": quote_currency,
                    "fx_rate_to_eur": conversion.rate,
                    "fx_rate_date": conversion.rate_date,
                    "current_price_eur": current_price,
                    "price_date": price_date,
                    "cost_basis": cost_basis,
                    "current_value": current_value,
                    "current_weight": position.current_weight,
                    "target_weight": position.target_weight,
                    "pnl": current_value - cost_basis if current_value is not None else None,
                    "pnl_pct": (
                        ((current_price / position.avg_cost) - 1) * 100
                        if current_price and position.avg_cost > 0
                        else None
                    ),
                    "valuation_warning": warning,
                }
            )
        return rows

    def transaction_rows(self) -> list[dict[str, Any]]:
        assets = {asset.id: asset for asset in self.session.scalars(select(AssetORM)).all()}
        rows = []
        for tx in self.repo.list_transactions():
            asset = assets.get(tx.asset_id)
            rows.append(
                {
                    "id": tx.id,
                    "symbol": asset.symbol if asset else str(tx.asset_id),
                    "type": tx.transaction_type,
                    "date": tx.transaction_date,
                    "quantity": tx.quantity,
                    "price": tx.price,
                    "gross_amount": tx.gross_amount,
                    "fees": tx.fees,
                    "taxes": tx.taxes,
                    "currency": tx.transaction_currency,
                    "price_source": tx.price_source,
                    "notes": tx.notes,
                    "external_source": tx.external_source,
                    "external_transaction_id": tx.external_transaction_id,
                }
            )
        return rows

    def available_quantity(self, asset_id: int) -> float:
        return self._available_quantity(asset_id)

    def _available_quantity(self, asset_id: int) -> float:
        quantity = 0.0
        transactions = self.repo.list_transactions(asset_id=asset_id)
        for tx in transactions:
            if tx.transaction_type == "BUY":
                quantity += tx.quantity
            elif tx.transaction_type == "SELL":
                quantity -= tx.quantity
        if not transactions:
            position = self.repo.get_by_asset_id(asset_id)
            if position is not None:
                quantity = float(position.quantity)
        return max(0.0, quantity)

    def _latest_price(self, asset_id: int) -> float | None:
        return self._latest_valuation(asset_id)[1]

    def _latest_valuation(
        self,
        asset_id: int,
    ) -> tuple[float | None, float | None, str | None, CurrencyConversion, date | None]:
        prices = self.prices_repo.get_asset_prices(asset_id, limit=1)
        asset = self.session.get(AssetORM, asset_id)
        quote_currency = self.currency_service.asset_currency(asset) if asset else None
        if prices.empty:
            conversion = self.currency_service.conversion(quote_currency)
            return None, None, quote_currency, conversion, None
        row = prices.iloc[-1]
        bar_currency = row.get("quote_currency") or quote_currency
        price_date = row["date"]
        native_price = float(row["close"])
        current_price, conversion = self.currency_service.convert(
            native_price,
            bar_currency,
            as_of=price_date,
        )
        return native_price, current_price, bar_currency, conversion, price_date

    def _price_row_in_eur(
        self,
        asset: AssetORM | None,
        row: Any,
        source: str,
    ) -> tuple[float | None, str, date | None]:
        row_date = row["date"]
        quote_currency = row.get("quote_currency") or (
            self.currency_service.asset_currency(asset) if asset else None
        )
        converted, conversion = self.currency_service.convert(
            float(row["close"]), quote_currency, as_of=row_date
        )
        if converted is None:
            return None, f"{source}:missing_fx_{quote_currency or 'unknown'}", row_date
        if conversion.provider == "identity":
            return converted, source, row_date
        detail = f"{source}:{quote_currency}->EUR"
        if conversion.rate_date:
            detail += f"@{conversion.rate_date.isoformat()}"
        return converted, detail, row_date
