import uuid
from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field


class DnbpModelSpeciesInput(BaseModel):
    species: str
    dnbp_factor: Decimal | None = None
    standard_weight: Decimal | None = None


class CreateDnbpModelRequest(BaseModel):
    name: str
    note: str | None = None
    model_type: str | None = None  # omitted = the formula of the model live now
    cif_buffer_per_kg: Decimal
    species: list[DnbpModelSpeciesInput] = Field(min_length=1)
    # A calendar date in Singapore; the model goes live at 00:00 that day.
    activation_date: date


class RescheduleDnbpModelRequest(BaseModel):
    activation_date: date


class DnbpModelSpeciesResponse(BaseModel):
    species: str
    dnbp_factor: Decimal | None
    standard_weight: Decimal | None


class ActivationDisplay(BaseModel):
    """The switch moment in both zones, "YYYY-MM-DD HH:MM"."""

    singapore: str
    melbourne: str


class DnbpModelResponse(BaseModel):
    id: uuid.UUID
    name: str
    note: str | None
    model_type: str
    cif_buffer_per_kg: Decimal
    species: list[DnbpModelSpeciesResponse]
    status: str  # DRAFT | SCHEDULED | LIVE | RETIRED | CANCELLED
    can_still_change: bool
    activation_at: datetime
    activation_date: date  # the Singapore calendar date
    activation_display: ActivationDisplay
    created_by_email: str | None
    created_at: datetime
    impact_previewed_at: datetime | None
    approved_by_email: str | None
    approved_at: datetime | None
    cancelled_at: datetime | None


class ModelImpactLine(BaseModel):
    order_line_id: str
    contract_no: str | None
    species: str
    old_dnbp: Decimal | None
    new_dnbp: Decimal | None
    delta_per_kg: Decimal | None
    exposure_delta_aud: Decimal | None


class ModelImpactResponse(BaseModel):
    baseline_model: str
    lines: list[ModelImpactLine]
    aggregate_exposure_delta_aud: Decimal
    lines_affected: int
    lines_unpriced: int


class ModelStampResponse(BaseModel):
    model_id: uuid.UUID | None
    name: str
    line_count: int


class SnapshotModelStatusResponse(BaseModel):
    live_model_id: uuid.UUID
    live_model_name: str
    calculated_under: list[ModelStampResponse]
    stale: bool
