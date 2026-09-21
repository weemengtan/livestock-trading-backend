"""Why an uploaded workbook was rejected. Domain-level and framework-free
(same boundary discipline as the rest of `domain/`): the service layer
translates `IngestionContractError` into the API error envelope. `message`
is written for the business user who uploaded the file, not for a
developer, and `details` carries what the UI needs to help them fix it."""

import enum
from typing import Any


class IngestionErrorCode(enum.StrEnum):
    NOT_A_VALID_XLSX = "NOT_A_VALID_XLSX"
    REQUIRED_SHEET_MISSING = "REQUIRED_SHEET_MISSING"
    REQUIRED_SHEET_HIDDEN = "REQUIRED_SHEET_HIDDEN"
    ACTIVE_SECTION_NOT_FOUND = "ACTIVE_SECTION_NOT_FOUND"
    REQUIRED_COLUMNS_MISSING = "REQUIRED_COLUMNS_MISSING"
    NO_ACTIVE_ROWS = "NO_ACTIVE_ROWS"


class IngestionContractError(Exception):
    def __init__(self, code: IngestionErrorCode, message: str, details: dict[str, Any] | None = None) -> None:
        self.code = code
        self.message = message
        self.details = details or {}
        super().__init__(message)
