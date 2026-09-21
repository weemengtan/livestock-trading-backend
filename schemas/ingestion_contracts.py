import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class CreateIngestionContractRequest(BaseModel):
    version: str
    required_sheet_name: str
    active_title_tokens: list[str] = Field(min_length=1)
    section_end_tokens: list[str] = Field(min_length=1)
    title_scan_rows: int = 6
    required_columns: dict[str, str]
    header_synonyms: dict[str, list[str]]
    header_scan_rows: int = 20
    min_header_matches: int = 6
    note: str | None = None


class IngestionFieldsResponse(BaseModel):
    """The closed set of fields the parser can read, for the contract editor."""

    fields: list[str]


class IngestionContractResponse(BaseModel):
    id: uuid.UUID
    version: str
    required_sheet_name: str
    active_title_tokens: list[str]
    section_end_tokens: list[str]
    title_scan_rows: int
    required_columns: dict[str, str]
    header_synonyms: dict[str, list[str]]
    header_scan_rows: int
    min_header_matches: int
    note: str | None
    is_active: bool
    created_by: uuid.UUID | None
    activated_by: uuid.UUID | None
    activated_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}
