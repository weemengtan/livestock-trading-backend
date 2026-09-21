"""§6.4/§6.5/§6.7/§9.8/§11.7 — reference-data versioning. Fetch-then-compute
split, same discipline as calculate_service.py: this module fetches lines
from the DB and builds EverhealthConfig objects; the actual before/after
math is domain.engine.impact.compute_impact, a pure function this module
calls but never reimplements.
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.errors import AbattoirOwnedTable, AppError, FourEyesRequired, ImpactPreviewRequired, NotFound
from core.reference_data import WEEKDAYS, get_active_everhealth_config
from domain.engine.config import DEFAULT_MODEL_TYPE, EverhealthConfig
from domain.engine.dnbp import available_model_types
from domain.engine.impact import ImpactLineInput, ImpactPreview, compute_impact
from domain.engine.workings import Lifecycle
from models.enums import ReferenceDataTableKey
from models.reference_data import ReferenceDataVersion
from repositories import order_lines as order_lines_repo
from repositories import order_snapshots as order_snapshots_repo
from repositories import reference_data as reference_data_repo
from services import audit_service

# §6.1-6.3/§6.6 — abattoir-owned tables. Never valid on this route (§9.8).
# Named here (not just absent from ReferenceDataTableKey) so a client
# submitting one of these gets a precise 403 explaining why, rather than a
# generic "invalid table_key" 422.
_ABATTOIR_TABLE_KEYS = {
    "pack_cost_by_product_type",
    "offal_return_ph_by_species",
    "skin_return_ph_by_species",
    "reference_weight_by_species",
    "required_margin_by_species",
    "benchmark_fixed_cost_per_head",
}

# Everhealth-owned (§6.4/§6.5/§6.7) — the only writable table_keys.
_WRITABLE_TABLE_KEYS = {
    "cif_buffer_per_kg": ReferenceDataTableKey.CIF_BUFFER_PER_KG,
    "dnbp_factor_by_species": ReferenceDataTableKey.DNBP_FACTOR,
    "standard_weight_by_species": ReferenceDataTableKey.STANDARD_WEIGHT,
    "bid_check_close_threshold_pct": ReferenceDataTableKey.BID_CHECK_CLOSE_THRESHOLD_PCT,
    "buyer_weight_band_tolerance_pct": ReferenceDataTableKey.BUYER_WEIGHT_BAND_TOLERANCE_PCT,
    "stale_instruction_hours": ReferenceDataTableKey.STALE_INSTRUCTION_HOURS,
    "saleyard_calendar": ReferenceDataTableKey.SALEYARD_CALENDAR,
}

# Only these two can move `AC` — the impact-preview/second-confirmation
# friction (§6.5) applies to them and them only. Standard weight (§6.4) is
# Everhealth-owned and versioned but cannot reach AC (§5.3), so it is
# exempt from that gate.
SOURCE_OF_TRUTH_TABLE_KEYS = {ReferenceDataTableKey.CIF_BUFFER_PER_KG, ReferenceDataTableKey.DNBP_FACTOR}


def resolve_table_key(raw_table_key: str) -> ReferenceDataTableKey:
    if raw_table_key in _ABATTOIR_TABLE_KEYS:
        raise AbattoirOwnedTable(raw_table_key)
    resolved = _WRITABLE_TABLE_KEYS.get(raw_table_key)
    if resolved is None:
        raise AppError("UNKNOWN_TABLE_KEY", f"'{raw_table_key}' is not a recognised reference-data table.", 422)
    return resolved


def _invalid(message: str) -> AppError:
    return AppError("INVALID_REFERENCE_DATA_VALUE", message, 422)


def validate_entry(table_key: ReferenceDataTableKey, key1: str | None, key2: str | None, value: Decimal) -> None:
    """Reject a value that could only be a mistake, before it is versioned.
    Ranges are deliberately about plausibility, not business policy."""
    if table_key in (
        ReferenceDataTableKey.BID_CHECK_CLOSE_THRESHOLD_PCT,
        ReferenceDataTableKey.BUYER_WEIGHT_BAND_TOLERANCE_PCT,
    ):
        if not Decimal(0) < value <= Decimal(100):
            raise _invalid(f"{table_key.value} must be greater than 0 and at most 100 (a percentage).")
    elif table_key == ReferenceDataTableKey.STALE_INSTRUCTION_HOURS:
        if value <= 0 or value != value.to_integral_value():
            raise _invalid("STALE_INSTRUCTION_HOURS must be a positive whole number of hours.")
    elif table_key == ReferenceDataTableKey.SALEYARD_CALENDAR:
        if not key1 or not key1.strip():
            raise _invalid("A saleyard calendar row needs a saleyard name.")
        if key2 not in WEEKDAYS:
            raise _invalid(f"A saleyard calendar row needs a day of the week ({', '.join(WEEKDAYS)}).")
        if value < 0:
            raise _invalid("A saleyard prepayment cannot be negative.")
    elif table_key == ReferenceDataTableKey.CIF_BUFFER_PER_KG:
        if value < 0:
            raise _invalid("The CIF buffer cannot be negative.")
    elif table_key in (ReferenceDataTableKey.DNBP_FACTOR, ReferenceDataTableKey.STANDARD_WEIGHT):
        if not key1 or value <= 0:
            raise _invalid(f"{table_key.value} needs a species and a value greater than 0.")


async def list_versions(db: AsyncSession) -> list[ReferenceDataVersion]:
    return await reference_data_repo.list_versions(db)


async def get_version_with_entries(db: AsyncSession, version_id: uuid.UUID):
    version = await reference_data_repo.get_by_id(db, version_id)
    if version is None:
        raise NotFound("Reference data version")
    entries = await reference_data_repo.list_entries(db, version_id)
    return version, entries


async def get_active_config(db: AsyncSession) -> EverhealthConfig:
    return await get_active_everhealth_config(db)


async def create_version(
    db: AsyncSession,
    *,
    actor_id: uuid.UUID,
    effective_from: datetime,
    note: str | None,
    raw_entries: list[tuple[str, str | None, str | None, Decimal, str | None]],
    model_type: str | None = None,
) -> ReferenceDataVersion:
    """Every version is a COMPLETE, standalone snapshot of the Everhealth
    config — same philosophy as order_snapshots being a full cumulative
    snapshot, not a delta (§7.1). `raw_entries` is the caller's requested
    *change* (e.g. "just SHEEP's factor"); this clones the currently-active
    config first and overlays those changes on top, so the persisted
    version always carries a value for cif_buffer and every species that
    the active config already prices — never leaving a gap that would make
    `get_active_everhealth_config` unable to find e.g. cif_buffer once this
    version activates. Never an in-place edit: the clone is a new row, the
    active version is untouched."""
    merged: dict[tuple[ReferenceDataTableKey, str | None, str | None], tuple[Decimal, str | None]] = {}

    active_version = await reference_data_repo.get_active_version(db)
    resolved_model_type = model_type or (active_version.model_type if active_version else DEFAULT_MODEL_TYPE)
    if resolved_model_type not in available_model_types():
        raise AppError(
            "UNKNOWN_MODEL_TYPE",
            f"'{resolved_model_type}' is not an implemented DNBP model. "
            f"Available: {', '.join(available_model_types())}.",
            422,
        )
    if active_version is not None:
        for entry in await reference_data_repo.list_entries(db, active_version.id):
            merged[(entry.table_key, entry.key1, entry.key2)] = (entry.value, entry.text_value)

    for raw_table_key, key1, key2, value, text_value in raw_entries:
        table_key = resolve_table_key(raw_table_key)
        validate_entry(table_key, key1, key2, value)
        merged[(table_key, key1, key2)] = (value, text_value)

    resolved_entries = [
        (table_key, key1, key2, value, text_value) for (table_key, key1, key2), (value, text_value) in merged.items()
    ]

    version = await reference_data_repo.create_version(
        db,
        effective_from=effective_from,
        created_by=actor_id,
        note=note,
        model_type=resolved_model_type,
        entries=resolved_entries,
    )

    await audit_service.write(
        db,
        actor_id=actor_id,
        action="reference_data_version.created",
        entity="reference_data_version",
        entity_id=version.id,
        after={"effective_from": effective_from.isoformat(), "note": note,
            "model_type": resolved_model_type,
            "entry_count": len(resolved_entries),
        },
    )
    return version


async def preview_impact(
    db: AsyncSession, version_id: uuid.UUID, *, org_id: uuid.UUID, actor_id: uuid.UUID
) -> ImpactPreview:
    version, entries = await get_version_with_entries(db, version_id)

    # create_version already persisted a COMPLETE snapshot (cloned from the
    # active config with the caller's changes overlaid), so this version's
    # own entries are the whole new_config — no further merging needed here.
    cif_buffer: Decimal | None = None
    factors: dict[str, Decimal] = {}
    weights: dict[str, Decimal] = {}
    for entry in entries:
        if entry.table_key == ReferenceDataTableKey.CIF_BUFFER_PER_KG:
            cif_buffer = entry.value
        elif entry.table_key == ReferenceDataTableKey.DNBP_FACTOR:
            factors[entry.key1] = entry.value
        elif entry.table_key == ReferenceDataTableKey.STANDARD_WEIGHT:
            weights[entry.key1] = entry.value

    old_config = await get_active_everhealth_config(db)
    new_config = EverhealthConfig(
        cif_buffer_per_kg=cif_buffer if cif_buffer is not None else old_config.cif_buffer_per_kg,
        dnbp_factor_by_species=factors,
        standard_weight_by_species=weights,
        model_type=version.model_type,
    )

    latest_snapshot = await order_snapshots_repo.get_latest_for_org(db, org_id)
    lines: list[ImpactLineInput] = []
    if latest_snapshot is not None:
        active_lines = await order_lines_repo.list_by_snapshot(db, latest_snapshot.id, lifecycle=Lifecycle.ACTIVE)
        lines = [
            ImpactLineInput(
                order_line_id=str(line.id),
                contract_no=line.contract_no,
                species=line.species,
                avg_price_aud=line.avg_price_aud,
                qty_kg=line.qty_kg,
            )
            for line in active_lines
            if line.species is not None
        ]

    preview = compute_impact(lines, old_config=old_config, new_config=new_config)

    await reference_data_repo.mark_impact_previewed(db, version)
    await audit_service.write(
        db,
        actor_id=actor_id,
        action="reference_data_version.impact_previewed",
        entity="reference_data_version",
        entity_id=version.id,
        after={
            "lines_affected": preview.lines_affected,
            "aggregate_exposure_delta_aud": str(preview.aggregate_exposure_delta_aud),
        },
    )
    return preview


async def activate_version(db: AsyncSession, version_id: uuid.UUID, *, actor_id: uuid.UUID) -> ReferenceDataVersion:
    version = await reference_data_repo.get_by_id(db, version_id)
    if version is None:
        raise NotFound("Reference data version")
    if version.impact_previewed_at is None:
        raise ImpactPreviewRequired()
    if settings.reference_data_four_eyes_required and version.created_by == actor_id:
        raise FourEyesRequired()

    before_active = await reference_data_repo.get_active_version(db)
    await reference_data_repo.activate(db, version, activated_by=actor_id)

    await audit_service.write(
        db,
        actor_id=actor_id,
        action="reference_data_version.activated",
        entity="reference_data_version",
        entity_id=version.id,
        before={"previous_active_version_id": str(before_active.id) if before_active else None},
        after={
            "activated_at": datetime.now(UTC).isoformat(),
            "created_by": str(version.created_by),
            "model_type": version.model_type,
        },
    )
    return version
