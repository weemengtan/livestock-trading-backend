"""Upload -> parse preview -> diff -> commit (§7.3, §9.2).

A parsed-but-uncommitted upload lives only in Redis, keyed by a preview_id,
for `settings.upload_preview_ttl_seconds` — nothing touches Postgres until
POST /snapshots confirms it. This is what makes "drop a file, see a
preview, confirm" cheap to get wrong and retry: an abandoned upload leaves
no trace.

The uploaded workbook itself is never persisted (not in Redis, not in
object storage): only the extracted Active Orders rows, the file's SHA-256
fingerprint and the audit trail are kept. The preview cache holds the
parsed rows and that fingerprint, never the file bytes.
"""

import hashlib
import json
import uuid
from datetime import UTC, date, datetime
from decimal import Decimal

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.errors import IngestionRejected, PreviewExpired
from domain.ingestion import diff as diff_module
from domain.ingestion.errors import IngestionContractError
from domain.ingestion.layout import DetectedLayout
from domain.ingestion.types import BenchmarkMethod, ValueSource
from domain.ingestion.workbook import ParsedOrderLine, ParsedSnapshot, parse
from models.enums import Incoterm, SnapshotStatus
from models.order_line import OrderLine
from models.order_line_removal import OrderLineRemoval
from models.order_snapshot import OrderSnapshot
from repositories import order_line_removals as order_line_removals_repo
from repositories import order_lines as order_lines_repo
from repositories import order_snapshots as order_snapshots_repo
from repositories import users as users_repo
from services import audit_service
from services.ingestion_contract_service import get_active_contract

_PREVIEW_KEY_PREFIX = "snapshot-preview:"


# --- Decimal-safe (de)serialisation for the Redis preview cache ------------


def _decimal_to_str(value: Decimal | None) -> str | None:
    return str(value) if value is not None else None


def _str_to_decimal(value: str | None) -> Decimal | None:
    return Decimal(value) if value is not None else None


def _serialize_line(line: ParsedOrderLine) -> dict:
    return {
        "line_no": line.line_no,
        "source_sheet": line.source_sheet,
        "source_row": line.source_row,
        "contract_no": line.contract_no,
        "customer_name": line.customer_name,
        "species": line.species,
        "loadout_date": line.loadout_date.isoformat() if line.loadout_date else None,
        "qty_kg": _decimal_to_str(line.qty_kg),
        "avg_price_aud": _decimal_to_str(line.avg_price_aud),
        "amount_aud": _decimal_to_str(line.amount_aud),
        "product_type": line.product_type,
        "incoterm": line.incoterm,
        "nrv_per_kg": _decimal_to_str(line.nrv_per_kg),
        "expected_livestock_cost_per_kg": _decimal_to_str(line.expected_livestock_cost_per_kg),
        "pack_cost_ph": _decimal_to_str(line.pack_cost_ph),
        "offal_return_ph": _decimal_to_str(line.offal_return_ph),
        "skin_return_ph": _decimal_to_str(line.skin_return_ph),
        "avg_weight_kg": _decimal_to_str(line.avg_weight_kg),
        "mom_ph": _decimal_to_str(line.mom_ph),
        "deposit_received": _decimal_to_str(line.deposit_received),
        "comments": line.comments,
        "dnbp_benchmark": _decimal_to_str(line.dnbp_benchmark),
        "benchmark_method": line.benchmark_method.value if line.benchmark_method else None,
        "estimated_heads": _decimal_to_str(line.estimated_heads),
        "total_livestock_cost": _decimal_to_str(line.total_livestock_cost),
        "value_sources": {k: v.value for k, v in line.value_sources.items()},
    }


def _deserialize_line(data: dict) -> ParsedOrderLine:
    return ParsedOrderLine(
        line_no=data["line_no"],
        source_sheet=data["source_sheet"],
        source_row=data["source_row"],
        contract_no=data["contract_no"],
        customer_name=data["customer_name"],
        species=data["species"],
        loadout_date=date.fromisoformat(data["loadout_date"]) if data["loadout_date"] else None,
        qty_kg=_str_to_decimal(data["qty_kg"]),
        avg_price_aud=_str_to_decimal(data["avg_price_aud"]),
        amount_aud=_str_to_decimal(data["amount_aud"]),
        product_type=data["product_type"],
        incoterm=data["incoterm"],
        nrv_per_kg=_str_to_decimal(data["nrv_per_kg"]),
        expected_livestock_cost_per_kg=_str_to_decimal(data["expected_livestock_cost_per_kg"]),
        pack_cost_ph=_str_to_decimal(data["pack_cost_ph"]),
        offal_return_ph=_str_to_decimal(data["offal_return_ph"]),
        skin_return_ph=_str_to_decimal(data["skin_return_ph"]),
        avg_weight_kg=_str_to_decimal(data["avg_weight_kg"]),
        mom_ph=_str_to_decimal(data["mom_ph"]),
        deposit_received=_str_to_decimal(data["deposit_received"]),
        comments=data["comments"],
        dnbp_benchmark=_str_to_decimal(data["dnbp_benchmark"]),
        benchmark_method=BenchmarkMethod(data["benchmark_method"]) if data["benchmark_method"] else None,
        estimated_heads=_str_to_decimal(data["estimated_heads"]),
        total_livestock_cost=_str_to_decimal(data["total_livestock_cost"]),
        value_sources={k: ValueSource(v) for k, v in data["value_sources"].items()},
    )


def _serialize_parsed_snapshot(parsed: ParsedSnapshot) -> dict:
    return {
        "source_filename": parsed.source_filename,
        "parser_version": parsed.parser_version,
        "detected_layout": parsed.detected_layout.as_jsonable(),
        "lines": [_serialize_line(line) for line in parsed.lines],
    }


def _deserialize_parsed_snapshot(data: dict) -> ParsedSnapshot:
    return ParsedSnapshot(
        source_filename=data["source_filename"],
        parser_version=data["parser_version"],
        detected_layout=DetectedLayout(**data["detected_layout"]),
        lines=[_deserialize_line(line) for line in data["lines"]],
    )


def parsed_line_from_db(order_line: OrderLine) -> ParsedOrderLine:
    """Reconstructs a diff-comparable ParsedOrderLine from a committed DB
    row — used only to diff a new upload against the previous snapshot
    (§7.3); parsing metadata (source_sheet/source_row/value_sources) is
    irrelevant to that comparison."""
    return ParsedOrderLine(
        line_no=order_line.line_no,
        source_sheet="",
        source_row=0,
        contract_no=order_line.contract_no,
        customer_name=order_line.customer_name,
        species=order_line.species,
        loadout_date=order_line.loadout_date,
        qty_kg=order_line.qty_kg,
        avg_price_aud=order_line.avg_price_aud,
        amount_aud=order_line.amount_aud,
        product_type=order_line.product_type,
        incoterm=order_line.incoterm.value if order_line.incoterm else None,
        nrv_per_kg=order_line.nrv_per_kg,
        expected_livestock_cost_per_kg=order_line.expected_livestock_cost_per_kg,
        pack_cost_ph=order_line.pack_cost_ph,
        offal_return_ph=order_line.offal_return_ph,
        skin_return_ph=order_line.skin_return_ph,
        avg_weight_kg=order_line.avg_weight_kg,
        mom_ph=order_line.mom_ph,
        deposit_received=order_line.deposit_received,
        comments=order_line.comments,
        dnbp_benchmark=order_line.dnbp_benchmark,
        benchmark_method=order_line.benchmark_method,
        estimated_heads=order_line.estimated_heads,
        total_livestock_cost=order_line.total_livestock_cost,
    )


def _order_line_model(snapshot_id: uuid.UUID, line: ParsedOrderLine) -> OrderLine:
    incoterm = None
    if line.incoterm:
        try:
            incoterm = Incoterm(line.incoterm)
        except ValueError:
            incoterm = None  # genuinely closed per PRD; an unrecognised value is dropped, not stored as garbage
    return OrderLine(
        snapshot_id=snapshot_id,
        line_no=line.line_no,
        contract_no=line.contract_no,
        customer_name=line.customer_name,
        species=line.species,
        loadout_date=line.loadout_date,
        qty_kg=line.qty_kg,
        avg_price_aud=line.avg_price_aud,
        amount_aud=line.amount_aud,
        product_type=line.product_type,
        incoterm=incoterm,
        nrv_per_kg=line.nrv_per_kg,
        expected_livestock_cost_per_kg=line.expected_livestock_cost_per_kg,
        pack_cost_ph=line.pack_cost_ph,
        offal_return_ph=line.offal_return_ph,
        skin_return_ph=line.skin_return_ph,
        avg_weight_kg=line.avg_weight_kg,
        mom_ph=line.mom_ph,
        deposit_received=line.deposit_received,
        comments=line.comments,
        dnbp_benchmark=line.dnbp_benchmark,
        benchmark_method=line.benchmark_method,
        estimated_heads=line.estimated_heads,
        total_livestock_cost=line.total_livestock_cost,
        value_sources={k: v.value for k, v in line.value_sources.items()},
    )


def diff_to_jsonable(snapshot_diff: diff_module.SnapshotDiff) -> dict:
    def _line(line: ParsedOrderLine) -> dict:
        return {"contract_no": line.contract_no, "species": line.species}

    def _field_change(change: diff_module.FieldChange) -> dict:
        def _value(v: object) -> object:
            if isinstance(v, Decimal):
                return str(v)
            if isinstance(v, (date, datetime)):
                return v.isoformat()
            return v

        return {"field": change.field, "previous": _value(change.previous), "current": _value(change.current)}

    return {
        "new_lines": [_line(line) for line in snapshot_diff.new_lines],
        "changed_lines": [
            {"identity_key": list(c.identity_key), "changes": [_field_change(fc) for fc in c.changes]}
            for c in snapshot_diff.changed_lines
        ],
        "removed_lines": [list(key) for key in snapshot_diff.removed_lines],
        "summary": snapshot_diff.summary(),
    }


async def diff_snapshots(db: AsyncSession, snapshot_a_id: uuid.UUID, snapshot_b_id: uuid.UUID) -> dict:
    """GET /snapshots/{id}/diff/{other_id} (§9.2) — structural diff between
    two already-committed snapshots, reusing the same comparison the
    upload preview runs against the previous snapshot."""
    lines_a = [parsed_line_from_db(line) for line in await order_lines_repo.list_by_snapshot(db, snapshot_a_id)]
    lines_b = [parsed_line_from_db(line) for line in await order_lines_repo.list_by_snapshot(db, snapshot_b_id)]
    return diff_to_jsonable(diff_module.compare(lines_a, lines_b))


async def create_upload_preview(
    db: AsyncSession, redis: Redis, *, org_id: uuid.UUID, file_bytes: bytes, filename: str
) -> dict:
    previous_snapshot = await order_snapshots_repo.get_latest_for_org(db, org_id)

    contract = await get_active_contract(db)
    try:
        parsed = parse(file_bytes, filename=filename, contract=contract)
    except IngestionContractError as exc:
        raise IngestionRejected(exc) from exc

    previous_lines: list[ParsedOrderLine] | None = None
    if previous_snapshot is not None:
        db_lines = await order_lines_repo.list_by_snapshot(db, previous_snapshot.id)
        previous_lines = [parsed_line_from_db(line) for line in db_lines]
    snapshot_diff = diff_module.compare(previous_lines, parsed.lines)

    # Never blocks the upload (§7.3: identical resubmission is a valid,
    # expected case, e.g. a quiet day) — just tells the human this looks
    # like something already sitting in the current snapshot, since §11.2
    # lets both Owner and Accountant upload and either could've beaten the
    # other to it.
    source_sha256 = hashlib.sha256(file_bytes).hexdigest()
    duplicate_of_current = None
    if previous_snapshot is not None and source_sha256 == previous_snapshot.source_sha256:
        uploader = await users_repo.get_by_id(db, previous_snapshot.uploaded_by)
        duplicate_of_current = {
            "snapshot_id": previous_snapshot.id,
            "uploaded_by_email": uploader.email if uploader is not None else "unknown",
            "uploaded_at": previous_snapshot.created_at,
        }

    preview_id = str(uuid.uuid4())
    cache_payload = {
        "org_id": str(org_id),
        "filename": filename,
        "source_sha256": source_sha256,
        "parsed": _serialize_parsed_snapshot(parsed),
    }
    await redis.set(
        f"{_PREVIEW_KEY_PREFIX}{preview_id}",
        json.dumps(cache_payload),
        ex=settings.upload_preview_ttl_seconds,
    )

    return {
        "preview_id": preview_id,
        "detected_layout": parsed.detected_layout.as_jsonable(),
        "active_count": len(parsed.lines),
        "diff": diff_to_jsonable(snapshot_diff),
        "duplicate_of_current": duplicate_of_current,
    }


async def commit_snapshot(
    db: AsyncSession,
    redis: Redis,
    *,
    org_id: uuid.UUID,
    uploaded_by: uuid.UUID,
    preview_id: str,
) -> OrderSnapshot:
    raw = await redis.get(f"{_PREVIEW_KEY_PREFIX}{preview_id}")
    if raw is None:
        raise PreviewExpired()
    cache_payload = json.loads(raw)
    if cache_payload["org_id"] != str(org_id):
        raise PreviewExpired()

    parsed = _deserialize_parsed_snapshot(cache_payload["parsed"])
    source_sha256 = cache_payload["source_sha256"]

    previous_snapshot = await order_snapshots_repo.get_latest_for_org(db, org_id)

    snapshot_id = uuid.uuid4()

    snapshot = OrderSnapshot(
        id=snapshot_id,
        org_id=org_id,
        uploaded_by=uploaded_by,
        source_filename=cache_payload["filename"],
        source_sha256=source_sha256,
        detected_layout=parsed.detected_layout.as_jsonable(),
        parser_version=parsed.parser_version,
        status=SnapshotStatus.PARSED,
    )
    await order_snapshots_repo.create(db, snapshot)
    await order_lines_repo.create_many(db, [_order_line_model(snapshot_id, line) for line in parsed.lines])

    # §7.3's removed_lines, persisted rather than shown once in the preview
    # and discarded — a contract that silently disappears from the Active
    # block otherwise leaves no record of why.
    if previous_snapshot is not None:
        previous_db_lines = await order_lines_repo.list_by_snapshot(db, previous_snapshot.id)
        previous_db_lines_by_key = {parsed_line_from_db(line).identity_key(): line for line in previous_db_lines}
        previous_lines = [parsed_line_from_db(line) for line in previous_db_lines]
        removed_keys = diff_module.compare(previous_lines, parsed.lines).removed_lines
        if removed_keys:
            now = datetime.now(UTC)
            await order_line_removals_repo.create_many(
                db,
                [
                    OrderLineRemoval(
                        snapshot_id=snapshot_id,
                        contract_no=key[0],
                        species=key[1],
                        product_type=key[2],
                        incoterm=key[3],
                        customer_name=previous_db_lines_by_key[key].customer_name,
                        amount_aud=previous_db_lines_by_key[key].amount_aud,
                        detected_at=now,
                    )
                    for key in removed_keys
                ],
            )

    await audit_service.write(
        db,
        actor_id=uploaded_by,
        action="snapshot.committed",
        entity="order_snapshot",
        entity_id=snapshot_id,
        after={
            "source_filename": snapshot.source_filename,
            "source_sha256": source_sha256,
            "active_count": len(parsed.lines),
            "parser_version": parsed.parser_version,
            "contract_version": parsed.detected_layout.contract_version,
        },
    )

    await redis.delete(f"{_PREVIEW_KEY_PREFIX}{preview_id}")
    return snapshot
