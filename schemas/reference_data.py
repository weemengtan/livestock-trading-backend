import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field, model_validator


class ReferenceDataEntryInput(BaseModel):
    """`table_key` is a raw string, not the enum, so an abattoir-owned
    table name produces a precise 403 (services.reference_data_service.
    resolve_table_key) rather than a generic 422 the enum type would give
    for free — §19 wants that 403 to be testable directly."""

    table_key: str
    key1: str | None = None
    key2: str | None = None
    value: Decimal
    text_value: str | None = None


class ReferenceDataKeyRef(BaseModel):
    """Identifies one keyed entry to remove from the new version."""

    table_key: str
    key1: str | None = None
    key2: str | None = None


class CreateVersionRequest(BaseModel):
    effective_from: datetime
    note: str | None = None
    model_type: str | None = None  # omitted = keep the active version's model
    entries: list[ReferenceDataEntryInput] = Field(default_factory=list)
    removals: list[ReferenceDataKeyRef] = Field(default_factory=list)

    @model_validator(mode="after")
    def _needs_a_change(self) -> "CreateVersionRequest":
        if not self.entries and not self.removals and self.model_type is None:
            raise ValueError("A version needs at least one entry, removal or model change.")
        return self


class ReferenceDataEntryResponse(BaseModel):
    id: uuid.UUID
    table_key: str
    key1: str | None
    key2: str | None
    value: Decimal
    text_value: str | None

    model_config = {"from_attributes": True}


class ReferenceDataVersionResponse(BaseModel):
    id: uuid.UUID
    effective_from: datetime
    created_by: uuid.UUID | None
    note: str | None
    model_type: str
    is_active: bool
    activated_at: datetime | None
    activated_by: uuid.UUID | None
    impact_previewed_at: datetime | None
    created_at: datetime

    model_config = {"from_attributes": True}


class ReferenceDataVersionDetailResponse(ReferenceDataVersionResponse):
    entries: list[ReferenceDataEntryResponse]


class SaleyardCalendarRow(BaseModel):
    saleyard: str
    day: str
    prepayment_aud: Decimal
    note: str | None


class ActiveConfigResponse(BaseModel):
    """§9.8 `GET /reference-data/active` — both halves, labelled by owner
    (§11.7's framing: which panel is editable, which is read-only)."""

    ref_data_version: str
    ref_data_version_id: str | None
    model_type: str
    available_model_types: list[str]
    cif_buffer_per_kg: Decimal
    dnbp_factor_by_species: dict[str, Decimal]
    standard_weight_by_species: dict[str, Decimal]
    bid_check_close_threshold_pct: Decimal
    buyer_weight_band_tolerance_pct: Decimal
    stale_instruction_hours: int
    saleyard_calendar: list["SaleyardCalendarRow"]
    owner: str = "EVERHEALTH"


class ImpactLineResponse(BaseModel):
    order_line_id: str
    contract_no: str | None
    species: str
    old_dnbp: Decimal | None
    new_dnbp: Decimal | None
    delta_per_kg: Decimal | None
    exposure_delta_aud: Decimal | None


class ImpactPreviewResponse(BaseModel):
    lines: list[ImpactLineResponse]
    aggregate_exposure_delta_aud: Decimal
    lines_affected: int
    lines_unpriced: int


class SpeciesResponse(BaseModel):
    code: str
    display_name: str
    is_active: bool
    has_dnbp_factor: bool
    has_standard_weight: bool


class CreateSpeciesRequest(BaseModel):
    code: str
    display_name: str


class ProductTypeResponse(BaseModel):
    code: str
    display_name: str
    is_active: bool

    model_config = {"from_attributes": True}


class CreateProductTypeRequest(BaseModel):
    code: str
    display_name: str

