import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Index, Integer, String, text
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.base import TimestampedBase


class IngestionContractRecord(TimestampedBase):
    """What an uploaded workbook must look like (domain/ingestion/contract.py's
    `IngestionContract`), as versioned, audited configuration. Never edited in
    place: a change is a new row, activated by a second person. Exactly one row
    is active (a partial unique index enforces it); every snapshot records the
    `version` label it was parsed under."""

    __tablename__ = "ingestion_contracts"
    __table_args__ = (
        Index("uq_ingestion_contracts_single_active", "is_active", unique=True, postgresql_where=text("is_active")),
    )

    version: Mapped[str] = mapped_column(String, unique=True)
    required_sheet_name: Mapped[str] = mapped_column(String)
    active_title_tokens: Mapped[list] = mapped_column(JSONB)
    section_end_tokens: Mapped[list] = mapped_column(JSONB)
    title_scan_rows: Mapped[int] = mapped_column(Integer)
    required_columns: Mapped[dict] = mapped_column(JSONB)
    note: Mapped[str | None] = mapped_column(String, default=None)

    is_active: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), default=None)
    activated_by: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), default=None)
    activated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
