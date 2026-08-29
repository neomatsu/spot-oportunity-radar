from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from io import BytesIO
from typing import Any
from uuid import uuid4

import pandas as pd
from sqlalchemy.orm import Session

from data.database import AssetORM
from data.repositories.assets_repo import AssetsRepository
from data.repositories.planned_entries_repo import PlannedEntriesRepository
from services.planned_entry_service import PlannedEntryService


@dataclass(frozen=True, slots=True)
class PlannedEntryImportRow:
    row_number: int
    symbol: str
    target_price: float | None
    suggested_weight_pct: float | None
    tolerance_pct: float | None
    rearm_distance_pct: float | None
    suggested_capital: float | None
    expires_at: date | None
    notes: str | None
    currency: str | None
    external_reference: str | None
    errors: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class PlannedEntryImportPreview:
    row: PlannedEntryImportRow
    asset: AssetORM | None
    status: str
    external_reference: str
    messages: tuple[str, ...] = ()


@dataclass(slots=True)
class PlannedEntryImportSummary:
    imported: int = 0
    duplicates: int = 0
    unmapped: int = 0
    invalid: int = 0
    batch_id: str | None = None
    errors: list[str] = field(default_factory=list)


class PlannedEntryExcelParser:
    REQUIRED_COLUMNS = {"symbol", "target_price", "suggested_weight_pct"}
    COLUMN_ALIASES = {
        "symbol": "symbol",
        "simbolo": "symbol",
        "ticker": "symbol",
        "codigo_activo": "symbol",
        "precio_objetivo": "target_price",
        "target_price": "target_price",
        "pct_de_capital_sugerido": "suggested_weight_pct",
        "capital_sugerido_pct": "suggested_weight_pct",
        "porcentaje_de_capital_sugerido": "suggested_weight_pct",
        "suggested_weight_pct": "suggested_weight_pct",
        "avisar_a_distancia_pct": "tolerance_pct",
        "tolerancia_pct": "tolerance_pct",
        "tolerance_pct": "tolerance_pct",
        "rearmar_al_alejarse_pct": "rearm_distance_pct",
        "rearme_pct": "rearm_distance_pct",
        "rearm_distance_pct": "rearm_distance_pct",
        "capital_sugerido": "suggested_capital",
        "suggested_capital": "suggested_capital",
        "fecha_expiracion": "expires_at",
        "expira_el": "expires_at",
        "expires_at": "expires_at",
        "notas": "notes",
        "notes": "notes",
        "divisa": "currency",
        "currency": "currency",
        "id_externo": "external_reference",
        "external_reference": "external_reference",
    }

    def parse(self, content: bytes) -> list[PlannedEntryImportRow]:
        try:
            frame = pd.read_excel(BytesIO(content), sheet_name=0, dtype=object)
        except Exception as exc:
            raise ValueError(f"No se pudo leer el Excel: {exc}") from exc
        normalized_columns = {
            column: self.COLUMN_ALIASES.get(self._normalize_header(column))
            for column in frame.columns
        }
        usable = {original: alias for original, alias in normalized_columns.items() if alias}
        frame = frame.rename(columns=usable)
        missing = self.REQUIRED_COLUMNS.difference(frame.columns)
        if missing:
            labels = {
                "symbol": "Codigo activo",
                "target_price": "Precio objetivo",
                "suggested_weight_pct": "% de capital sugerido",
            }
            raise ValueError(
                "Faltan columnas obligatorias: "
                + ", ".join(labels[column] for column in sorted(missing))
            )

        rows: list[PlannedEntryImportRow] = []
        for index, raw in frame.iterrows():
            if raw.isna().all():
                continue
            rows.append(self._parse_row(index + 2, raw))
        return rows

    def _parse_row(self, row_number: int, raw: pd.Series) -> PlannedEntryImportRow:
        errors: list[str] = []
        symbol = self._text(raw.get("symbol")).upper()
        if not symbol:
            errors.append("Codigo de activo vacio")
        target_price = self._number(raw.get("target_price"), "Precio objetivo", errors)
        weight = self._number(raw.get("suggested_weight_pct"), "% de capital sugerido", errors)
        tolerance = self._optional_number(raw.get("tolerance_pct"), "Tolerancia", errors)
        rearm = self._optional_number(raw.get("rearm_distance_pct"), "Rearme", errors)
        capital = self._optional_number(raw.get("suggested_capital"), "Capital sugerido", errors)
        expires_at = self._optional_date(raw.get("expires_at"), errors)
        if target_price is not None and target_price <= 0:
            errors.append("Precio objetivo debe ser mayor que cero")
        if weight is not None and not 0 < weight <= 100:
            errors.append("% de capital sugerido debe estar entre 0 y 100")
        if tolerance is not None and tolerance < 0:
            errors.append("Tolerancia no puede ser negativa")
        if rearm is not None and rearm <= (tolerance if tolerance is not None else 1.0):
            errors.append("Rearme debe ser mayor que tolerancia")
        if capital is not None and capital < 0:
            errors.append("Capital sugerido no puede ser negativo")
        if expires_at is not None and expires_at < date.today():
            errors.append("Fecha de expiracion no puede estar en el pasado")
        return PlannedEntryImportRow(
            row_number=row_number,
            symbol=symbol,
            target_price=target_price,
            suggested_weight_pct=weight,
            tolerance_pct=tolerance,
            rearm_distance_pct=rearm,
            suggested_capital=capital,
            expires_at=expires_at,
            notes=self._text(raw.get("notes")) or None,
            currency=self._text(raw.get("currency")).upper() or None,
            external_reference=self._text(raw.get("external_reference")) or None,
            errors=tuple(errors),
        )

    @classmethod
    def template(cls) -> bytes:
        columns = [
            "Codigo activo",
            "Precio objetivo",
            "% de capital sugerido",
            "Avisar a distancia (%)",
            "Rearmar al alejarse (%)",
            "Capital sugerido",
            "Fecha expiracion",
            "Notas",
            "Divisa",
            "ID externo",
        ]
        instructions = pd.DataFrame(
            [
                ("Codigo activo", "Obligatorio", "Ticker configurado, por ejemplo MSFT"),
                ("Precio objetivo", "Obligatorio", "Precio en la divisa nativa del activo"),
                ("% de capital sugerido", "Obligatorio", "Numero entre 0 y 100"),
                ("Avisar a distancia (%)", "Opcional", "Default configurado: 1"),
                ("Rearmar al alejarse (%)", "Opcional", "Default configurado: 3"),
                ("Fecha expiracion", "Opcional", "Formato fecha de Excel o YYYY-MM-DD"),
                ("ID externo", "Opcional", "Identificador estable para evitar duplicados"),
            ],
            columns=["Campo", "Requerido", "Descripcion"],
        )
        buffer = BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
            pd.DataFrame(columns=columns).to_excel(writer, sheet_name="Planes", index=False)
            instructions.to_excel(writer, sheet_name="Instrucciones", index=False)
            worksheet = writer.book["Planes"]
            worksheet.freeze_panes = "A2"
            for column_cells in worksheet.columns:
                worksheet.column_dimensions[column_cells[0].column_letter].width = 24
            instruction_sheet = writer.book["Instrucciones"]
            instruction_sheet.freeze_panes = "A2"
            instruction_sheet.column_dimensions["A"].width = 28
            instruction_sheet.column_dimensions["B"].width = 14
            instruction_sheet.column_dimensions["C"].width = 58
        return buffer.getvalue()

    @staticmethod
    def _normalize_header(value: Any) -> str:
        text = unicodedata.normalize("NFKD", str(value))
        text = "".join(character for character in text if not unicodedata.combining(character))
        text = text.strip().lower().replace("%", " pct ")
        return re.sub(r"[^a-z0-9]+", "_", text).strip("_")

    @staticmethod
    def _text(value: Any) -> str:
        if value is None or pd.isna(value):
            return ""
        return str(value).strip()

    @classmethod
    def _number(cls, value: Any, label: str, errors: list[str]) -> float | None:
        number = cls._optional_number(value, label, errors)
        if number is None:
            errors.append(f"{label} es obligatorio")
        return number

    @classmethod
    def _optional_number(cls, value: Any, label: str, errors: list[str]) -> float | None:
        text = cls._text(value)
        if not text:
            return None
        try:
            return float(text.replace("%", "").replace(" ", "").replace(",", "."))
        except ValueError:
            errors.append(f"{label} no es numerico")
            return None

    @classmethod
    def _optional_date(cls, value: Any, errors: list[str]) -> date | None:
        if value is None or pd.isna(value) or not cls._text(value):
            return None
        parsed = pd.to_datetime(value, errors="coerce")
        if pd.isna(parsed):
            errors.append("Fecha de expiracion no valida")
            return None
        return parsed.date()


class PlannedEntryImportService:
    SOURCE = "planned_entry_excel"

    def __init__(self, session: Session) -> None:
        self.session = session
        self.assets_repo = AssetsRepository(session)
        self.entries_repo = PlannedEntriesRepository(session)
        self.entry_service = PlannedEntryService(session)
        self.parser = PlannedEntryExcelParser()

    def parse(self, content: bytes) -> list[PlannedEntryImportRow]:
        return self.parser.parse(content)

    def preview(
        self,
        rows: list[PlannedEntryImportRow],
        *,
        manual_mappings: dict[str, int] | None = None,
    ) -> list[PlannedEntryImportPreview]:
        manual_mappings = manual_mappings or {}
        enabled_assets = {asset.symbol.upper(): asset for asset in self.assets_repo.list_enabled()}
        previews: list[PlannedEntryImportPreview] = []
        seen_references: set[str] = set()
        seen_levels: set[tuple[int, float]] = set()
        for row in rows:
            asset = self.session.get(AssetORM, manual_mappings.get(row.symbol, -1))
            asset = asset or enabled_assets.get(row.symbol)
            external_reference = row.external_reference or self._fingerprint(row)
            messages = list(row.errors)
            status = "Lista para importar"
            if messages:
                status = "Error"
            elif asset is None:
                status = "Sin mapear"
                messages.append("El codigo no corresponde a un activo habilitado")
            elif row.currency and row.currency != (asset.quote_currency or "").upper():
                status = "Error"
                messages.append(
                    f"Divisa {row.currency} no coincide con {asset.quote_currency or 'N/A'}"
                )
            elif external_reference in seen_references:
                status = "Duplicada"
                messages.append("ID externo o huella repetidos dentro del Excel")
            elif (asset.id, round(float(row.target_price), 6)) in seen_levels:
                status = "Duplicada"
                messages.append("Nivel repetido dentro del Excel")
            elif self.entries_repo.get_by_external_reference(self.SOURCE, external_reference):
                status = "Duplicada"
                messages.append("ID externo o huella ya importados")
            elif self.entries_repo.find_matching_level(
                asset_id=asset.id,
                target_price=float(row.target_price),
            ):
                status = "Duplicada"
                messages.append("Ya existe un nivel activo con ese precio")

            if status == "Lista para importar":
                tolerance = (
                    row.tolerance_pct
                    if row.tolerance_pct is not None
                    else float(self.entry_service.config.get("default_tolerance_pct", 1.0))
                )
                rearm = (
                    row.rearm_distance_pct
                    if row.rearm_distance_pct is not None
                    else float(self.entry_service.config.get("default_rearm_distance_pct", 3.0))
                )
                if rearm <= tolerance:
                    status = "Error"
                    messages.append("Rearme debe ser mayor que tolerancia")

            if status == "Lista para importar":
                seen_references.add(external_reference)
            if (
                status == "Lista para importar"
                and asset is not None
                and row.target_price is not None
            ):
                seen_levels.add((asset.id, round(row.target_price, 6)))
            previews.append(
                PlannedEntryImportPreview(
                    row=row,
                    asset=asset,
                    status=status,
                    external_reference=external_reference,
                    messages=tuple(messages),
                )
            )

        totals: dict[int, float] = {}
        for item in previews:
            if (
                item.status == "Lista para importar"
                and item.asset is not None
                and item.row.suggested_weight_pct is not None
            ):
                totals[item.asset.id] = (
                    totals.get(item.asset.id, 0.0) + item.row.suggested_weight_pct
                )
        return [self._with_total_warning(item, totals) for item in previews]

    def import_rows(
        self,
        rows: list[PlannedEntryImportRow],
        *,
        manual_mappings: dict[str, int] | None = None,
    ) -> PlannedEntryImportSummary:
        previews = self.preview(rows, manual_mappings=manual_mappings)
        summary = PlannedEntryImportSummary(batch_id=uuid4().hex)
        for item in previews:
            if item.status == "Duplicada":
                summary.duplicates += 1
                continue
            if item.status == "Sin mapear":
                summary.unmapped += 1
                continue
            if item.status != "Lista para importar" or item.asset is None:
                summary.invalid += 1
                summary.errors.append(f"Fila {item.row.row_number}: {'; '.join(item.messages)}")
                continue
            try:
                self.entry_service.create_level(
                    asset=item.asset,
                    target_price=float(item.row.target_price),
                    suggested_weight_pct=float(item.row.suggested_weight_pct),
                    suggested_capital=item.row.suggested_capital,
                    tolerance_pct=item.row.tolerance_pct,
                    rearm_distance_pct=item.row.rearm_distance_pct,
                    notes=item.row.notes,
                    expires_at=item.row.expires_at,
                    import_source=self.SOURCE,
                    external_reference=item.external_reference,
                    import_batch_id=summary.batch_id,
                )
                summary.imported += 1
            except ValueError as exc:
                summary.invalid += 1
                summary.errors.append(f"Fila {item.row.row_number}: {exc}")
        return summary

    @staticmethod
    def _fingerprint(row: PlannedEntryImportRow) -> str:
        payload = "|".join(
            [
                row.symbol,
                str(row.target_price),
                str(row.suggested_weight_pct),
                str(row.tolerance_pct),
                str(row.rearm_distance_pct),
                str(row.expires_at),
            ]
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    @staticmethod
    def _with_total_warning(
        item: PlannedEntryImportPreview, totals: dict[int, float]
    ) -> PlannedEntryImportPreview:
        if item.asset is None or totals.get(item.asset.id, 0.0) <= 100:
            return item
        return PlannedEntryImportPreview(
            row=item.row,
            asset=item.asset,
            status=item.status,
            external_reference=item.external_reference,
            messages=item.messages
            + (f"Aviso: suma por activo {totals[item.asset.id]:.1f}% supera 100%",),
        )
