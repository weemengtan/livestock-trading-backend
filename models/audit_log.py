import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, DateTime, ForeignKey, Identity, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from core.db import Base


class AuditLog(Base):
    """§8, §14. Generic, append-only, tamper-evident. Every business and
    security event writes through this table via services/audit_service.py.

    Append-only is enforced by the database (a trigger rejects UPDATE,
    DELETE and TRUNCATE; the production app role is also denied those
    privileges). Tamper-evidence: each row carries the hash of the previous
    row (`prev_hash`) and its own (`row_hash`), so any edit, deletion or
    reordering breaks the chain, which `audit_service.verify_chain` detects.
    `seq` is the chain's total order."""

    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    seq: Mapped[int] = mapped_column(BigInteger, Identity(always=True), unique=True)
    actor_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("users.id"), default=None)
    action: Mapped[str] = mapped_column(String, index=True)
    entity: Mapped[str] = mapped_column(String, index=True)
    entity_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), default=None)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB, default=None)
    ip: Mapped[str | None] = mapped_column(String, default=None)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    correlation_id: Mapped[str | None] = mapped_column(String, default=None)
    prev_hash: Mapped[str | None] = mapped_column(String, default=None)
    row_hash: Mapped[str | None] = mapped_column(String, default=None)
