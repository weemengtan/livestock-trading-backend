import uuid
from datetime import date

from sqlalchemy import Date, Enum, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.base import TimestampedBase
from models.enums import SnapshotStatus


class OrderSnapshot(TimestampedBase):
    """§7.3, §8. One immutable row per upload. `detected_layout` is the
    parser's own record of what it found and how (domain/ingestion/
    layout.py's `DetectedLayout.as_jsonable`, including the ingestion
    contract version) — kept here, not recomputed later. The uploaded
    workbook itself is not stored; `source_sha256` fingerprints it."""

    __tablename__ = "order_snapshots"

    org_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("organisations.id"))
    uploaded_by: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"))
    as_of_date: Mapped[date | None] = mapped_column(Date, default=None)

    source_filename: Mapped[str] = mapped_column(String)
    source_sha256: Mapped[str] = mapped_column(String, index=True)

    detected_layout: Mapped[dict] = mapped_column(JSONB)
    parser_version: Mapped[str] = mapped_column(String)

    status: Mapped[SnapshotStatus] = mapped_column(
        Enum(SnapshotStatus, name="snapshot_status"), default=SnapshotStatus.PARSED
    )
