from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from io import StringIO
from typing import Any

from sqlalchemy.orm import Session

from core.config import load_assets_config
from data.repositories.assets_repo import AssetsRepository
from data.repositories.portfolio_repo import PortfolioRepository
from services.portfolio_service import PortfolioService


@dataclass(frozen=True)
class BrokerTransaction:
    external_source: str
    external_transaction_id: str
    external_asset_id: str
    external_name: str
    occurred_at: datetime
    transaction_date: date
    transaction_type: str
    quantity: float
    price: float
    gross_amount: float
    fees: float
    taxes: float
    currency: str
    description: str
    raw_payload: dict[str, Any]


@dataclass
class BrokerImportSummary:
    imported: int = 0
    duplicates: int = 0
    unmapped: int = 0
    invalid: int = 0
    errors: list[str] = field(default_factory=list)


class TradeRepublicCsvParser:
    SOURCE = "trade_republic"
    REQUIRED_COLUMNS = {
        "datetime",
        "date",
        "category",
        "type",
        "name",
        "symbol",
        "shares",
        "price",
        "amount",
        "fee",
        "tax",
        "currency",
        "description",
        "transaction_id",
    }

    def parse(self, content: bytes | str) -> list[BrokerTransaction]:
        text = content.decode("utf-8-sig") if isinstance(content, bytes) else content
        reader = csv.DictReader(StringIO(text))
        missing = self.REQUIRED_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise ValueError(
                "El CSV de Trade Republic no contiene estas columnas: "
                + ", ".join(sorted(missing))
            )

        transactions: list[BrokerTransaction] = []
        for row_number, row in enumerate(reader, start=2):
            if str(row.get("category") or "").upper() != "TRADING":
                continue
            transaction_type = str(row.get("type") or "").upper()
            if transaction_type not in {"BUY", "SELL"}:
                continue
            try:
                transactions.append(self._parse_row(row, transaction_type))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"Fila {row_number}: {exc}") from exc
        return sorted(transactions, key=lambda item: item.occurred_at)

    def _parse_row(
        self,
        row: dict[str, str | None],
        transaction_type: str,
    ) -> BrokerTransaction:
        transaction_id = str(row.get("transaction_id") or "").strip()
        external_asset_id = str(row.get("symbol") or "").strip().upper()
        if not transaction_id:
            raise ValueError("transaction_id vacío")
        if not external_asset_id:
            raise ValueError("symbol/ISIN vacío")
        occurred_at = datetime.fromisoformat(
            str(row.get("datetime") or "").strip().replace("Z", "+00:00")
        )
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=UTC)
        quantity = abs(self._number(row.get("shares")))
        price = abs(self._number(row.get("price")))
        gross_amount = abs(self._number(row.get("amount"), default=quantity * price))
        if quantity <= 0 or price <= 0 or gross_amount <= 0:
            raise ValueError("shares, price y amount deben ser positivos")
        return BrokerTransaction(
            external_source=self.SOURCE,
            external_transaction_id=transaction_id,
            external_asset_id=external_asset_id,
            external_name=str(row.get("name") or "").strip(),
            occurred_at=occurred_at,
            transaction_date=date.fromisoformat(str(row.get("date") or "").strip()),
            transaction_type=transaction_type,
            quantity=quantity,
            price=price,
            gross_amount=gross_amount,
            fees=abs(self._number(row.get("fee"))),
            taxes=abs(self._number(row.get("tax"))),
            currency=str(row.get("currency") or "").strip().upper(),
            description=str(row.get("description") or "").strip(),
            raw_payload={key: value for key, value in row.items()},
        )

    @staticmethod
    def _number(value: str | None, *, default: float = 0.0) -> float:
        normalized = str(value or "").strip().replace(",", ".")
        return float(normalized) if normalized else float(default)


class BrokerImportService:
    """Broker-neutral persistence workflow with a Trade Republic adapter."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.assets_repo = AssetsRepository(session)
        self.portfolio_repo = PortfolioRepository(session)
        self.portfolio_service = PortfolioService(session)
        self.trade_republic_parser = TradeRepublicCsvParser()

    def parse_trade_republic(self, content: bytes | str) -> list[BrokerTransaction]:
        return self.trade_republic_parser.parse(content)

    def resolve_asset_ids(
        self,
        transactions: list[BrokerTransaction],
    ) -> dict[str, int]:
        configured = self._configured_isin_mappings()
        result: dict[str, int] = {}
        for external_id in {item.external_asset_id for item in transactions}:
            persisted = self.portfolio_repo.get_external_asset_mapping(
                TradeRepublicCsvParser.SOURCE, external_id
            )
            if persisted is not None:
                result[external_id] = persisted.asset_id
                continue
            asset = configured.get(external_id)
            if asset is not None:
                result[external_id] = asset.id
        return result

    def duplicate_transaction_ids(
        self,
        transactions: list[BrokerTransaction],
    ) -> set[str]:
        return self.portfolio_repo.existing_external_transaction_ids(
            TradeRepublicCsvParser.SOURCE,
            [item.external_transaction_id for item in transactions],
        )

    def import_trade_republic(
        self,
        transactions: list[BrokerTransaction],
        *,
        manual_mappings: dict[str, int] | None = None,
    ) -> BrokerImportSummary:
        manual_mappings = manual_mappings or {}
        mappings = self.resolve_asset_ids(transactions)
        mappings.update(manual_mappings)
        metadata = {item.external_asset_id: item for item in transactions}
        for external_id, asset_id in mappings.items():
            item = metadata[external_id]
            self.portfolio_repo.upsert_external_asset_mapping(
                external_source=item.external_source,
                external_asset_id=external_id,
                external_symbol=external_id,
                external_name=item.external_name,
                asset_id=asset_id,
            )

        summary = BrokerImportSummary()
        existing_ids = self.duplicate_transaction_ids(transactions)
        seen_ids: set[str] = set()
        available_by_asset: dict[int, float] = {}
        for item in transactions:
            if (
                item.external_transaction_id in existing_ids
                or item.external_transaction_id in seen_ids
            ):
                summary.duplicates += 1
                continue
            seen_ids.add(item.external_transaction_id)
            asset_id = mappings.get(item.external_asset_id)
            if asset_id is None:
                summary.unmapped += 1
                continue
            if item.currency != "EUR":
                summary.invalid += 1
                summary.errors.append(
                    f"{item.external_transaction_id}: moneda {item.currency or 'N/A'} no soportada"
                )
                continue
            available = available_by_asset.setdefault(
                asset_id, self.portfolio_service.available_quantity(asset_id)
            )
            if item.transaction_type == "SELL" and item.quantity > available + 1e-8:
                summary.invalid += 1
                summary.errors.append(
                    f"{item.external_transaction_id}: venta de {item.quantity:g} supera "
                    f"las {available:g} unidades disponibles"
                )
                continue

            self.portfolio_repo.add_transaction(
                asset_id=asset_id,
                transaction_type=item.transaction_type,
                transaction_date=item.transaction_date,
                quantity=item.quantity,
                price=item.price,
                gross_amount=item.gross_amount,
                fees=item.fees,
                taxes=item.taxes,
                transaction_currency=item.currency,
                price_source="trade_republic",
                notes=item.description or None,
                external_source=item.external_source,
                external_transaction_id=item.external_transaction_id,
                external_payload_json=item.raw_payload,
            )
            available_by_asset[asset_id] = (
                available + item.quantity
                if item.transaction_type == "BUY"
                else available - item.quantity
            )
            summary.imported += 1

        if summary.imported:
            self.portfolio_service.recalculate_positions()
        return summary

    def _configured_isin_mappings(self) -> dict[str, Any]:
        mappings: dict[str, Any] = {}
        for configured_asset in load_assets_config().assets:
            isin = str(configured_asset.external_ids.get("isin") or "").upper()
            if not isin:
                continue
            asset = self.assets_repo.get_by_symbol(configured_asset.symbol)
            if asset is not None:
                mappings[isin] = asset
        return mappings
