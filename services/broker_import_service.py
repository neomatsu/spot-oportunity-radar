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
from services.currency_service import CurrencyService
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


class KrakenCsvParser:
    SOURCE = "kraken"
    REQUIRED_COLUMNS = {
        "txid",
        "pair",
        "time",
        "type",
        "ordertype",
        "price",
        "cost",
        "fee",
        "vol",
        "margin",
    }
    ASSET_ALIASES = {"XBT": "BTC", "XXBT": "BTC", "XETH": "ETH"}
    CURRENCY_ALIASES = {
        "ZUSD": "USD",
        "ZEUR": "EUR",
        "USDC": "USDC",
        "USDT": "USDT",
    }

    def parse(self, content: bytes | str) -> list[BrokerTransaction]:
        text = content.decode("utf-8-sig") if isinstance(content, bytes) else content
        reader = csv.DictReader(StringIO(text))
        missing = self.REQUIRED_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise ValueError(
                "El CSV de Kraken no contiene estas columnas: "
                + ", ".join(sorted(missing))
            )

        transactions: list[BrokerTransaction] = []
        for row_number, row in enumerate(reader, start=2):
            transaction_type = str(row.get("type") or "").strip().upper()
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
        transaction_id = str(row.get("txid") or "").strip()
        pair = str(row.get("pair") or "").strip().upper()
        if not transaction_id:
            raise ValueError("txid vacío")
        base_asset, quote_currency = self._split_pair(pair)
        occurred_at = datetime.fromisoformat(str(row.get("time") or "").strip())
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=UTC)
        else:
            occurred_at = occurred_at.astimezone(UTC)

        quantity = abs(TradeRepublicCsvParser._number(row.get("vol")))
        price = abs(TradeRepublicCsvParser._number(row.get("price")))
        gross_amount = abs(TradeRepublicCsvParser._number(row.get("cost")))
        fees = abs(TradeRepublicCsvParser._number(row.get("fee")))
        margin = abs(TradeRepublicCsvParser._number(row.get("margin")))
        if margin > 1e-12:
            raise ValueError("las operaciones con margen no están soportadas")
        if quantity <= 0 or price <= 0 or gross_amount <= 0:
            raise ValueError("vol, price y cost deben ser positivos")

        order_type = str(row.get("ordertype") or "").strip().lower()
        return BrokerTransaction(
            external_source=self.SOURCE,
            external_transaction_id=transaction_id,
            external_asset_id=base_asset,
            external_name=pair,
            occurred_at=occurred_at,
            transaction_date=occurred_at.date(),
            transaction_type=transaction_type,
            quantity=quantity,
            price=price,
            gross_amount=gross_amount,
            fees=fees,
            taxes=0.0,
            currency=quote_currency,
            description=f"Kraken {transaction_type} {pair} ({order_type or 'spot'})",
            raw_payload={key: value for key, value in row.items()},
        )

    def _split_pair(self, pair: str) -> tuple[str, str]:
        if "/" not in pair:
            raise ValueError(f"par no reconocido: {pair or 'vacío'}")
        base, quote = (part.strip().upper() for part in pair.split("/", maxsplit=1))
        base = self.ASSET_ALIASES.get(base, base)
        quote = self.CURRENCY_ALIASES.get(quote, quote)
        if not base or not quote:
            raise ValueError(f"par no reconocido: {pair}")
        return base, quote


class BrokerImportService:
    """Broker-neutral persistence workflow with source-specific CSV adapters."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.assets_repo = AssetsRepository(session)
        self.portfolio_repo = PortfolioRepository(session)
        self.portfolio_service = PortfolioService(session)
        self.currency_service = CurrencyService(session, base_currency="EUR")
        self.trade_republic_parser = TradeRepublicCsvParser()
        self.kraken_parser = KrakenCsvParser()

    def parse_trade_republic(self, content: bytes | str) -> list[BrokerTransaction]:
        return self.trade_republic_parser.parse(content)

    def parse_kraken(self, content: bytes | str) -> list[BrokerTransaction]:
        return self.kraken_parser.parse(content)

    def resolve_asset_ids(
        self,
        transactions: list[BrokerTransaction],
    ) -> dict[str, int]:
        configured_isins = self._configured_isin_mappings()
        configured_crypto = self._configured_crypto_mappings()
        result: dict[str, int] = {}
        metadata = {item.external_asset_id: item for item in transactions}
        for external_id, item in metadata.items():
            persisted = self.portfolio_repo.get_external_asset_mapping(
                item.external_source, external_id
            )
            if persisted is not None:
                result[external_id] = persisted.asset_id
                continue
            configured = (
                configured_crypto
                if item.external_source == KrakenCsvParser.SOURCE
                else configured_isins
            )
            asset = configured.get(external_id)
            if asset is not None:
                result[external_id] = asset.id
        return result

    def duplicate_transaction_ids(
        self,
        transactions: list[BrokerTransaction],
    ) -> set[str]:
        if not transactions:
            return set()
        sources = {item.external_source for item in transactions}
        if len(sources) != 1:
            raise ValueError("No se pueden mezclar fuentes en un mismo lote")
        return self.portfolio_repo.existing_external_transaction_ids(
            sources.pop(),
            [item.external_transaction_id for item in transactions],
        )

    def import_trade_republic(
        self,
        transactions: list[BrokerTransaction],
        *,
        manual_mappings: dict[str, int] | None = None,
    ) -> BrokerImportSummary:
        return self._import_transactions(
            transactions,
            manual_mappings=manual_mappings,
            convert_to_eur=False,
        )

    def import_kraken(
        self,
        transactions: list[BrokerTransaction],
        *,
        manual_mappings: dict[str, int] | None = None,
    ) -> BrokerImportSummary:
        return self._import_transactions(
            transactions,
            manual_mappings=manual_mappings,
            convert_to_eur=True,
        )

    def _import_transactions(
        self,
        transactions: list[BrokerTransaction],
        *,
        manual_mappings: dict[str, int] | None,
        convert_to_eur: bool,
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
            if not convert_to_eur and item.currency != "EUR":
                summary.invalid += 1
                summary.errors.append(
                    f"{item.external_transaction_id}: moneda {item.currency or 'N/A'} no soportada"
                )
                continue
            converted = self._amounts_in_eur(item) if convert_to_eur else None
            if convert_to_eur and converted is None:
                summary.invalid += 1
                summary.errors.append(
                    f"{item.external_transaction_id}: no hay cambio "
                    f"{item.currency}/EUR para {item.transaction_date}"
                )
                continue
            price, gross_amount, fees, taxes, payload, price_source = (
                converted
                if converted is not None
                else (
                    item.price,
                    item.gross_amount,
                    item.fees,
                    item.taxes,
                    item.raw_payload,
                    item.external_source,
                )
            )
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
                price=price,
                gross_amount=gross_amount,
                fees=fees,
                taxes=taxes,
                transaction_currency="EUR" if convert_to_eur else item.currency,
                price_source=price_source,
                notes=item.description or None,
                external_source=item.external_source,
                external_transaction_id=item.external_transaction_id,
                external_payload_json=payload,
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

    def _amounts_in_eur(
        self,
        item: BrokerTransaction,
    ) -> tuple[float, float, float, float, dict[str, Any], str] | None:
        source_currency = self._fx_currency(item.currency)
        conversion = self.currency_service.conversion(
            source_currency,
            as_of=item.transaction_date,
        )
        if conversion.rate is None:
            return None
        rate = float(conversion.rate)
        payload = dict(item.raw_payload)
        payload["_import_conversion"] = {
            "original_currency": item.currency,
            "fx_source_currency": source_currency,
            "target_currency": "EUR",
            "rate": rate,
            "rate_date": (
                conversion.rate_date.isoformat() if conversion.rate_date else None
            ),
            "provider": conversion.provider,
            "stablecoin_parity_assumption": item.currency in {"USDC", "USDT"},
            "original_price": item.price,
            "original_gross_amount": item.gross_amount,
            "original_fees": item.fees,
        }
        rate_label = (
            f"@{conversion.rate_date.isoformat()}" if conversion.rate_date else ""
        )
        return (
            item.price * rate,
            item.gross_amount * rate,
            item.fees * rate,
            item.taxes * rate,
            payload,
            f"{item.external_source}:{item.currency}->EUR{rate_label}",
        )

    @staticmethod
    def _fx_currency(currency: str) -> str:
        return "USD" if currency.upper() in {"USDC", "USDT"} else currency.upper()

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

    def _configured_crypto_mappings(self) -> dict[str, Any]:
        mappings: dict[str, Any] = {}
        quote_suffixes = ("USDT", "USDC", "USD", "EUR")
        for configured_asset in load_assets_config().assets:
            if configured_asset.asset_type.lower() != "crypto":
                continue
            asset = self.assets_repo.get_by_symbol(configured_asset.symbol)
            if asset is None:
                continue
            symbol = configured_asset.symbol.upper()
            mappings[symbol] = asset
            for suffix in quote_suffixes:
                if symbol.endswith(suffix) and len(symbol) > len(suffix):
                    mappings[symbol[: -len(suffix)]] = asset
                    break
        return mappings
