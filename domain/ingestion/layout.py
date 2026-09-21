"""Locate the Active Orders block on the one sheet the contract names
(PRD §7.2 point 3, §5.3).

Detected by scanning for marker text, never by assuming a fixed row range,
and never by searching other sheets: the caller hands this module exactly
one worksheet. The ORDERS LOADED marker is used only as the row where the
Active block ends; nothing below it is ever located or parsed.

Marker matching is a token-set comparison, never a fixed phrase, because
real files write "LOADED ORDERS" in one place and "ORDERS LOADED" in
another (a manual-process artefact confirmed with the business).
"""

from dataclasses import dataclass

from domain.ingestion.contract import IngestionContract
from domain.ingestion.errors import IngestionContractError, IngestionErrorCode
from domain.ingestion.headers import HeaderRow, find_header_row, tokenize


@dataclass(frozen=True, slots=True)
class DetectedLayout:
    contract_version: str
    sheet_name: str
    header_row: int
    data_row_start: int
    data_row_end: int  # exclusive: the ORDERS LOADED marker row, or the end of the sheet

    def as_jsonable(self) -> dict:
        """Shape persisted verbatim into order_snapshots.detected_layout
        (§8) — plain JSON-safe types only."""
        return {
            "contract_version": self.contract_version,
            "sheet_name": self.sheet_name,
            "header_row": self.header_row,
            "data_row_start": self.data_row_start,
            "data_row_end": self.data_row_end,
        }


def _title_cell_matches(sheet, tokens: frozenset[str], max_rows: int) -> bool:
    last_row = min(max_rows, sheet.max_row)
    for row_idx in range(1, last_row + 1):
        for col_idx in range(1, sheet.max_column + 1):
            if tokens <= tokenize(sheet.cell(row=row_idx, column=col_idx).value):
                return True
    return False


def _find_marker_row(sheet, tokens: frozenset[str], id_column: int | None, start_row: int) -> int | None:
    columns = [id_column] if id_column is not None else list(range(1, sheet.max_column + 1))
    for row_idx in range(start_row, sheet.max_row + 1):
        for col_idx in columns:
            if tokens <= tokenize(sheet.cell(row=row_idx, column=col_idx).value):
                return row_idx
    return None


def _require_columns(header: HeaderRow, contract: IngestionContract, sheet_name: str) -> None:
    found_fields = set(header.column_map.values())
    missing = [label for field_name, label in contract.required_columns.items() if field_name not in found_fields]
    if missing:
        raise IngestionContractError(
            IngestionErrorCode.REQUIRED_COLUMNS_MISSING,
            f"The Active Orders table on '{sheet_name}' is missing required column(s): {', '.join(missing)}.",
            {"missing_columns": missing},
        )


def locate_active_section(sheet, contract: IngestionContract) -> DetectedLayout:
    """`sheet` is an openpyxl worksheet opened with data_only=True (marker
    and header text is never a formula). Raises `IngestionContractError`
    rather than guessing when the block cannot be found."""
    sheet_name = sheet.title

    if not _title_cell_matches(sheet, contract.active_title_tokens, contract.title_scan_rows):
        raise IngestionContractError(
            IngestionErrorCode.ACTIVE_SECTION_NOT_FOUND,
            f"Could not find the 'ACTIVE ORDERS' section on '{sheet_name}'. "
            "Please check the file is the daily Active Purchase Orders workbook.",
        )

    header = find_header_row(sheet)
    if header is None:
        raise IngestionContractError(
            IngestionErrorCode.ACTIVE_SECTION_NOT_FOUND,
            f"Could not find the Active Orders column headings on '{sheet_name}'.",
        )
    _require_columns(header, contract, sheet_name)

    id_column = next((col for col, field_name in header.column_map.items() if field_name == "contract_no"), None)
    end_marker_row = _find_marker_row(sheet, contract.section_end_tokens, id_column, header.row_index + 1)

    return DetectedLayout(
        contract_version=contract.version,
        sheet_name=sheet_name,
        header_row=header.row_index,
        data_row_start=header.row_index + 1,
        data_row_end=end_marker_row if end_marker_row is not None else sheet.max_row + 1,
    )
