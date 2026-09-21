"""Orchestrates headers.py / layout.py / cells.py / benchmark.py into a
`ParsedSnapshot` — the one entry point the API/service layer calls.
Everything below this module is pure dataclasses in, pure dataclasses out;
this module is the sole place that touches `openpyxl` and raw file bytes.

Scope is fixed by the `IngestionContract`: only the Active Orders block of
the one visible sheet it names is ever read. The workbook is opened whole
(openpyxl cannot open a single sheet), the required sheet is gated, and
every other sheet is removed from memory before anything is parsed — so no
later code can read a hidden tab by accident.
"""

import io
from dataclasses import dataclass, field
from decimal import Decimal
from zipfile import BadZipFile

import openpyxl
from openpyxl.utils.exceptions import InvalidFileException

from domain.ingestion import cells
from domain.ingestion.benchmark import classify_benchmark_header
from domain.ingestion.contract import IngestionContract
from domain.ingestion.errors import IngestionContractError, IngestionErrorCode
from domain.ingestion.headers import find_header_row, normalise
from domain.ingestion.layout import DetectedLayout, locate_active_section
from domain.ingestion.types import BenchmarkMethod, ValueSource

PARSER_VERSION = "ingestion-v2"

# §5.1 / §7.2 pt 4: exactly these columns arrive already computed by the
# abattoir's own formulas, so only these are checked for a hand-set literal.
_FORMULA_DRIVEN_FIELDS = frozenset(
    {
        "nrv_per_kg",
        "pack_cost_ph",
        "offal_return_ph",
        "skin_return_ph",
        "mom_ph",
        "dnbp_benchmark",
        "estimated_heads",
        "total_livestock_cost",
    }
)

_DECIMAL_FIELDS = frozenset(
    {
        "qty_kg",
        "avg_price_aud",
        "amount_aud",
        "nrv_per_kg",
        "expected_livestock_cost_per_kg",
        "pack_cost_ph",
        "offal_return_ph",
        "skin_return_ph",
        "avg_weight_kg",
        "mom_ph",
        "deposit_received",
        "dnbp_benchmark",
        "estimated_heads",
        "total_livestock_cost",
    }
)
_ENUM_FIELDS = frozenset({"species", "product_type", "incoterm"})
_TEXT_FIELDS = frozenset({"contract_no", "customer_name", "comments"})
_DATE_FIELDS = frozenset({"loadout_date"})


@dataclass(frozen=True, slots=True)
class ParsedOrderLine:
    line_no: int
    source_sheet: str
    source_row: int
    contract_no: str | None = None
    customer_name: str | None = None
    species: str | None = None
    loadout_date: object = None  # datetime.date | None
    qty_kg: Decimal | None = None
    avg_price_aud: Decimal | None = None
    amount_aud: Decimal | None = None
    product_type: str | None = None
    incoterm: str | None = None
    nrv_per_kg: Decimal | None = None
    expected_livestock_cost_per_kg: Decimal | None = None
    pack_cost_ph: Decimal | None = None
    offal_return_ph: Decimal | None = None
    skin_return_ph: Decimal | None = None
    avg_weight_kg: Decimal | None = None
    mom_ph: Decimal | None = None
    deposit_received: Decimal | None = None
    comments: str | None = None
    dnbp_benchmark: Decimal | None = None
    benchmark_method: BenchmarkMethod | None = None
    estimated_heads: Decimal | None = None
    total_livestock_cost: Decimal | None = None
    value_sources: dict[str, ValueSource] = field(default_factory=dict)

    def identity_key(self) -> tuple:
        """Best-effort stable identity for diffing across snapshots (§7.3).
        contract_no alone is not unique (§5.1) — one contract can split
        across species/product-type/incoterm rows in the real data."""
        return (self.contract_no, self.species, self.product_type, self.incoterm)


@dataclass(frozen=True, slots=True)
class ParsedSnapshot:
    source_filename: str
    parser_version: str
    detected_layout: DetectedLayout
    lines: list[ParsedOrderLine]


def _load_required_sheet(file_bytes: bytes, contract: IngestionContract, *, data_only: bool):
    """Open the workbook, enforce that the contract's sheet exists and is
    visible, and return that worksheet with every other sheet removed."""
    try:
        workbook = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=data_only)
    except (BadZipFile, InvalidFileException, KeyError, ValueError) as exc:
        raise IngestionContractError(
            IngestionErrorCode.NOT_A_VALID_XLSX,
            "This file could not be opened as an Excel (.xlsx) workbook. "
            "Please upload the daily Active Purchase Orders file.",
        ) from exc

    target = normalise(contract.required_sheet_name)
    sheet = next((ws for ws in workbook.worksheets if normalise(ws.title) == target), None)

    if sheet is None:
        visible_tabs = [ws.title for ws in workbook.worksheets if ws.sheet_state == "visible"]
        raise IngestionContractError(
            IngestionErrorCode.REQUIRED_SHEET_MISSING,
            f"This file has no '{contract.required_sheet_name}' tab. "
            f"Tabs found: {', '.join(visible_tabs) if visible_tabs else 'none'}. "
            "Please upload the daily Active Purchase Orders file that includes it.",
            {"required_sheet": contract.required_sheet_name, "visible_sheets": visible_tabs},
        )

    if sheet.sheet_state != "visible":
        raise IngestionContractError(
            IngestionErrorCode.REQUIRED_SHEET_HIDDEN,
            f"The '{sheet.title}' tab is hidden in this file. Please unhide it in Excel and upload the file again.",
            {"required_sheet": contract.required_sheet_name},
        )

    for other in [ws for ws in workbook.worksheets if ws is not sheet]:
        workbook.remove(other)
    return sheet


def parse(
    file_bytes: bytes,
    *,
    filename: str,
    contract: IngestionContract,
) -> ParsedSnapshot:
    sheet_values = _load_required_sheet(file_bytes, contract, data_only=True)
    sheet_formulas = _load_required_sheet(file_bytes, contract, data_only=False)

    layout = locate_active_section(sheet_values, contract)
    header = find_header_row(
        sheet_values,
        contract.header_lookup(),
        min_matches=contract.min_header_matches,
        start_row=layout.header_row,
        max_scan_rows=1,
    )
    benchmark_method = _detect_benchmark_method(sheet_values, header.column_map, header.row_index)

    lines: list[ParsedOrderLine] = []
    for row_idx in range(layout.data_row_start, layout.data_row_end):
        parsed_row = _parse_row(
            sheet_values, sheet_formulas, row_idx, header.column_map, benchmark_method=benchmark_method
        )
        if parsed_row is None:
            continue
        lines.append(
            ParsedOrderLine(
                line_no=len(lines) + 1,
                source_sheet=layout.sheet_name,
                source_row=row_idx,
                **parsed_row,
            )
        )

    if not lines:
        raise IngestionContractError(
            IngestionErrorCode.NO_ACTIVE_ROWS,
            f"No Active Orders rows were found on '{layout.sheet_name}'.",
        )

    return ParsedSnapshot(
        source_filename=filename,
        parser_version=PARSER_VERSION,
        detected_layout=layout,
        lines=lines,
    )


def _detect_benchmark_method(sheet_values, column_map: dict[int, str], header_row: int) -> BenchmarkMethod | None:
    benchmark_col = next((col for col, field_name in column_map.items() if field_name == "dnbp_benchmark"), None)
    if benchmark_col is None:
        return None
    header_text = sheet_values.cell(row=header_row, column=benchmark_col).value
    return classify_benchmark_header(header_text)


def _parse_row(
    sheet_values,
    sheet_formulas,
    row_idx: int,
    column_map: dict[int, str],
    *,
    benchmark_method: BenchmarkMethod | None,
) -> dict | None:
    id_col = next((col for col, f in column_map.items() if f == "contract_no"), None)
    qty_col = next((col for col, f in column_map.items() if f == "qty_kg"), None)
    id_value = sheet_values.cell(row=row_idx, column=id_col).value if id_col else None
    qty_value = sheet_values.cell(row=row_idx, column=qty_col).value if qty_col else None
    if cells.is_grand_total_row(id_value, qty_value):
        return None
    if normalise(id_value) == "" and normalise(qty_value) == "":
        return None  # a fully blank row inside the range (spacer row)

    result: dict = {}
    value_sources: dict[str, ValueSource] = {}

    for col_idx, field_name in column_map.items():
        raw_value = sheet_values.cell(row=row_idx, column=col_idx).value
        raw_value = cells.resolve_blank(raw_value)

        if field_name in _DATE_FIELDS:
            result[field_name] = cells.to_date(raw_value)
        elif field_name in _ENUM_FIELDS:
            result[field_name] = cells.normalise_enum_value(raw_value)
        elif field_name in _TEXT_FIELDS:
            result[field_name] = cells.normalise_text(raw_value)
        elif field_name in _DECIMAL_FIELDS:
            result[field_name] = cells.to_decimal(raw_value)
        else:
            result[field_name] = raw_value

        if field_name in _FORMULA_DRIVEN_FIELDS and raw_value is not None:
            formula_value = sheet_formulas.cell(row=row_idx, column=col_idx).value
            value_sources[field_name] = (
                ValueSource.FORMULA if cells.is_formula_cell(formula_value) else ValueSource.HAND_SET
            )

    if "dnbp_benchmark" in result and result["dnbp_benchmark"] is not None:
        result["benchmark_method"] = benchmark_method

    result["value_sources"] = value_sources
    return result
