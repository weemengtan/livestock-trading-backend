"""The one write path to audit_log (§8, §14), and its integrity check.

Every entry joins a hash chain: `row_hash = sha256(prev_hash | canonical
row)`, where `prev_hash` is the previous entry's `row_hash`. Editing,
removing or reordering any past entry breaks every hash after it, which
`verify_chain` reports. Writes are serialised with a transaction-scoped
advisory lock so two concurrent transactions cannot both link to the same
predecessor; the entry is written in the caller's transaction, so it commits
or rolls back together with the change it records.

`ip` and `correlation_id` default to the current request's (core/
request_context.py, core/correlation.py) so callers need not pass them.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from core.correlation import correlation_id_var
from core.request_context import client_ip_var
from models.audit_log import AuditLog

GENESIS_HASH = "0" * 64
_CHAIN_LOCK_KEY = 7_450_231_001  # arbitrary constant: the advisory lock that serialises chain appends


def _canonical(entry: AuditLog, prev_hash: str) -> str:
    payload = {
        "id": str(entry.id),
        "at": entry.at.astimezone(UTC).isoformat(),
        "actor_id": str(entry.actor_id) if entry.actor_id else None,
        "action": entry.action,
        "entity": entry.entity,
        "entity_id": str(entry.entity_id) if entry.entity_id else None,
        "before": entry.before,
        "after": entry.after,
        "ip": entry.ip,
        "correlation_id": entry.correlation_id,
        "prev_hash": prev_hash,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)


def compute_row_hash(entry: AuditLog, prev_hash: str) -> str:
    return hashlib.sha256(_canonical(entry, prev_hash).encode()).hexdigest()


async def write(
    db: AsyncSession,
    *,
    actor_id: uuid.UUID | None,
    action: str,
    entity: str,
    entity_id: uuid.UUID | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    ip: str | None = None,
) -> AuditLog:
    correlation_id = correlation_id_var.get()
    entry = AuditLog(
        id=uuid.uuid4(),
        at=datetime.now(UTC),
        actor_id=actor_id,
        action=action,
        entity=entity,
        entity_id=entity_id,
        before=before,
        after=after,
        ip=ip if ip is not None else client_ip_var.get(),
        correlation_id=correlation_id if correlation_id != "-" else None,
    )

    # Held until the caller's transaction ends, so the next writer sees this
    # entry's hash as its predecessor.
    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": _CHAIN_LOCK_KEY})
    previous = (
        await db.execute(
            select(AuditLog.row_hash).where(AuditLog.row_hash.is_not(None)).order_by(AuditLog.seq.desc()).limit(1)
        )
    ).scalar()
    entry.prev_hash = previous or GENESIS_HASH
    entry.row_hash = compute_row_hash(entry, entry.prev_hash)

    db.add(entry)
    await db.flush()
    return entry


def state_of(obj: object, fields: tuple[str, ...]) -> dict[str, Any]:
    """JSON-safe snapshot of an object's fields for `before` / `after`."""
    state: dict[str, Any] = {}
    for name in fields:
        value = getattr(obj, name)
        state[name] = value if value is None or isinstance(value, (bool, int, str)) else str(value)
    return state


@dataclass(frozen=True, slots=True)
class ChainVerification:
    total: int
    unchained_legacy: int  # rows written before the chain existed (no hash) — not verifiable
    verified: int
    ok: bool
    first_bad_seq: int | None
    problem: str | None


async def verify_chain(db: AsyncSession) -> ChainVerification:
    """Recompute every hash in sequence order and check each row links to its
    predecessor. Legacy rows without a hash are counted, not verified."""
    rows = (await db.execute(select(AuditLog).order_by(AuditLog.seq))).scalars().all()
    legacy = 0
    verified = 0
    expected_prev = GENESIS_HASH
    started = False
    for row in rows:
        if row.row_hash is None:
            if started:
                return ChainVerification(
                    len(rows), legacy, verified, False, row.seq, "unhashed row after the chain began"
                )
            legacy += 1
            continue
        started = True
        if row.prev_hash != expected_prev:
            problem = "previous-hash link broken (row removed, reordered or altered)"
            return ChainVerification(len(rows), legacy, verified, False, row.seq, problem)
        if compute_row_hash(row, row.prev_hash) != row.row_hash:
            problem = "row content does not match its hash"
            return ChainVerification(len(rows), legacy, verified, False, row.seq, problem)
        expected_prev = row.row_hash
        verified += 1
    return ChainVerification(len(rows), legacy, verified, True, None, None)
