from __future__ import annotations

from datetime import date, timedelta
from io import BytesIO

import pandas as pd
import pytest

from data.database import AssetORM
from data.repositories.planned_entries_repo import PlannedEntriesRepository
from services.planned_entry_import_service import (
    PlannedEntryExcelParser,
    PlannedEntryImportService,
)


def _asset(db_session, symbol: str, currency: str = "USD") -> AssetORM:
    asset = AssetORM(
        symbol=symbol,
        name=f"{symbol} asset",
        asset_type="stock",
        sector="Technology",
        region="US",
        enabled=True,
        supports_fundamentals=False,
        quote_currency=currency,
    )
    db_session.add(asset)
    db_session.flush()
    return asset


def _workbook(rows: list[dict]) -> bytes:
    buffer = BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        pd.DataFrame(rows).to_excel(writer, sheet_name="Planes", index=False)
    return buffer.getvalue()


def test_parser_accepts_spanish_columns_and_decimal_commas(db_session) -> None:
    content = _workbook(
        [
            {
                "Código activo": "MSFT",
                "Precio objetivo": "390,50",
                "% de capital sugerido": "7,5%",
                "Avisar a distancia (%)": "1,25",
                "Rearmar al alejarse (%)": 3,
                "Fecha expiración": date.today() + timedelta(days=30),
            }
        ]
    )

    rows = PlannedEntryImportService(db_session).parse(content)

    assert len(rows) == 1
    assert rows[0].symbol == "MSFT"
    assert rows[0].target_price == 390.5
    assert rows[0].suggested_weight_pct == 7.5
    assert rows[0].tolerance_pct == 1.25
    assert rows[0].errors == ()


def test_imports_multiple_assets_and_reuses_current_planned_entry_logic(db_session) -> None:
    _asset(db_session, "MSFT")
    _asset(db_session, "SXR8.DE", "EUR")
    content = _workbook(
        [
            {
                "Codigo activo": "MSFT",
                "Precio objetivo": 390,
                "% de capital sugerido": 5,
                "Divisa": "USD",
                "ID externo": "plan-msft-1",
            },
            {
                "Codigo activo": "SXR8.DE",
                "Precio objetivo": 650,
                "% de capital sugerido": 15,
                "Divisa": "EUR",
                "ID externo": "plan-sxr8-1",
            },
        ]
    )
    service = PlannedEntryImportService(db_session)
    rows = service.parse(content)

    summary = service.import_rows(rows)
    levels = PlannedEntriesRepository(db_session).list_all()

    assert summary.imported == 2
    assert summary.invalid == 0
    assert {level.asset.symbol for level in levels} == {"MSFT", "SXR8.DE"}
    assert all(level.import_source == "planned_entry_excel" for level in levels)
    assert all(level.import_batch_id == summary.batch_id for level in levels)


def test_reimporting_same_workbook_is_deduplicated(db_session) -> None:
    _asset(db_session, "MSFT")
    service = PlannedEntryImportService(db_session)
    rows = service.parse(
        _workbook(
            [
                {
                    "Codigo activo": "MSFT",
                    "Precio objetivo": 390,
                    "% de capital sugerido": 5,
                }
            ]
        )
    )

    first = service.import_rows(rows)
    second = service.import_rows(rows)

    assert first.imported == 1
    assert second.imported == 0
    assert second.duplicates == 1
    assert len(PlannedEntriesRepository(db_session).list_all()) == 1


def test_unknown_symbol_can_be_mapped_manually(db_session) -> None:
    asset = _asset(db_session, "MSFT")
    service = PlannedEntryImportService(db_session)
    rows = service.parse(
        _workbook(
            [
                {
                    "Codigo activo": "MICROSOFT",
                    "Precio objetivo": 390,
                    "% de capital sugerido": 5,
                }
            ]
        )
    )

    without_mapping = service.preview(rows)
    summary = service.import_rows(rows, manual_mappings={"MICROSOFT": asset.id})

    assert without_mapping[0].status == "Sin mapear"
    assert summary.imported == 1
    assert PlannedEntriesRepository(db_session).list_all()[0].asset_id == asset.id


def test_invalid_rows_are_reported_without_blocking_valid_rows(db_session) -> None:
    _asset(db_session, "MSFT")
    service = PlannedEntryImportService(db_session)
    rows = service.parse(
        _workbook(
            [
                {
                    "Codigo activo": "MSFT",
                    "Precio objetivo": 390,
                    "% de capital sugerido": 5,
                },
                {
                    "Codigo activo": "MSFT",
                    "Precio objetivo": -1,
                    "% de capital sugerido": 150,
                },
            ]
        )
    )

    summary = service.import_rows(rows)

    assert summary.imported == 1
    assert summary.invalid == 1
    assert len(summary.errors) == 1


def test_missing_required_excel_columns_are_rejected() -> None:
    content = _workbook([{"Codigo activo": "MSFT"}])

    with pytest.raises(ValueError, match="Faltan columnas obligatorias"):
        PlannedEntryExcelParser().parse(content)


def test_template_contains_required_columns() -> None:
    frame = pd.read_excel(BytesIO(PlannedEntryExcelParser.template()), sheet_name="Planes")

    assert {"Codigo activo", "Precio objetivo", "% de capital sugerido"}.issubset(
        frame.columns
    )
