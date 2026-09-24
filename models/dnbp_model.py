import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Index, Numeric, String, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.base import TimestampedBase

MONEY = Numeric(18, 10)


class DnbpModel(TimestampedBase):
    """A DNBP pricing model (§5.3, §6.4, §6.5, §6.7 cif buffer): the formula
    selector, the CIF buffer, and the per-species dnbp factors and standard
    weights — exactly the fields of domain.engine.config.EverhealthConfig.
    Operational tunables and the saleyard calendar are NOT part of a model;
    they stay in reference_data_versions and change independently, so a model
    scheduled weeks ahead can never roll one of those back.

    Immutable once created (same discipline as reference_data_versions): a
    change is a new model. What can change afterwards is its schedule —
    approval, activation instant, cancellation — never its prices.

    Only one model is live at any instant, and "live" is never stored: it is
    resolved at read time as the approved, non-cancelled model with the
    latest `activation_at` that is not in the future (see
    domain/engine/model_schedule.py). No background job flips anything, so a
    switch cannot be missed by a server that was down at the moment.
    """

    __tablename__ = "dnbp_models"
    __table_args__ = (
        # Two approved models switching on at the same instant would make
        # "the latest" ambiguous — refused by the database, not just by the
        # service.
        Index(
            "uq_dnbp_models_approved_activation_at",
            "activation_at",
            unique=True,
            postgresql_where=text("approved_at IS NOT NULL AND cancelled_at IS NULL"),
        ),
    )

    name: Mapped[str] = mapped_column(String, unique=True)
    note: Mapped[str | None] = mapped_column(String, default=None)
    model_type: Mapped[str] = mapped_column(String, default="FACTOR_AFTER_BUFFER", server_default="FACTOR_AFTER_BUFFER")
    cif_buffer_per_kg: Mapped[Decimal] = mapped_column(MONEY)

    # The exact moment this model goes live: 00:00 Singapore time on the date
    # the user chose (core/activation_time.py), stored as an instant.
    activation_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)

    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), default=None)
    # The activation gate (§6.5, §19): approval is refused until the impact
    # preview has been fetched for this model.
    impact_previewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    approved_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), default=None)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    cancelled_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), default=None)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)


class DnbpModelSpecies(TimestampedBase):
    """One species' parameters inside a model. `species` is a bare code, never
    an FK to species_registry (§6.9: open registry). A species may carry a
    dnbp_factor, a standard_weight, or both — they were independent entries
    before this table existed and stay independent."""

    __tablename__ = "dnbp_model_species"
    __table_args__ = (UniqueConstraint("model_id", "species", name="uq_dnbp_model_species_model_species"),)

    model_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("dnbp_models.id"), index=True)
    species: Mapped[str] = mapped_column(String)
    dnbp_factor: Mapped[Decimal | None] = mapped_column(MONEY, default=None)
    standard_weight: Mapped[Decimal | None] = mapped_column(MONEY, default=None)
