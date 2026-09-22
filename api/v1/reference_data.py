import dataclasses
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import CurrentUser, require_role
from core.db import get_db
from core.reference_data import get_operational_constants, get_saleyard_calendar
from domain.engine.dnbp import available_model_types
from models.audit_log import AuditLog
from models.enums import Role
from repositories import users as users_repo
from schemas.reference_data import (
    ActiveConfigResponse,
    CreateProductTypeRequest,
    CreateSpeciesRequest,
    CreateVersionRequest,
    ImpactLineResponse,
    ImpactPreviewResponse,
    ProductTypeResponse,
    ReferenceDataAuditEntryResponse,
    ReferenceDataEntryResponse,
    ReferenceDataVersionDetailResponse,
    ReferenceDataVersionResponse,
    SaleyardCalendarRow,
    SpeciesResponse,
)
from services import reference_data_service, registry_service

router = APIRouter(prefix="/reference-data", tags=["reference-data"])

# §9.8 — the DNBP model belongs to both Bobby and Bing (§2.1); neither is
# gated ahead of the other here, same as publications.
_trading_console = require_role(Role.OWNER, Role.ACCOUNTANT)


@router.get("/active", response_model=ActiveConfigResponse)
async def get_active(current: CurrentUser = Depends(_trading_console), db: AsyncSession = Depends(get_db)):
    config = await reference_data_service.get_active_config(db)
    constants = await get_operational_constants(db)
    calendar = await get_saleyard_calendar(db)
    return ActiveConfigResponse(
        ref_data_version=config.ref_data_version,
        ref_data_version_id=config.version_id,
        model_type=config.model_type,
        available_model_types=list(available_model_types()),
        cif_buffer_per_kg=config.cif_buffer_per_kg,
        dnbp_factor_by_species=config.dnbp_factor_by_species,
        standard_weight_by_species=config.standard_weight_by_species,
        bid_check_close_threshold_pct=constants.bid_check_close_threshold_pct,
        buyer_weight_band_tolerance_pct=constants.buyer_weight_band_tolerance_pct,
        stale_instruction_hours=constants.stale_instruction_hours,
        dnbp_outlier_threshold_pct=constants.dnbp_outlier_threshold_pct,
        dnbp_outlier_lookback_days=constants.dnbp_outlier_lookback_days,
        analytics_trailing_days_for_rate=constants.analytics_trailing_days_for_rate,
        delivery_escalation_minutes=constants.delivery_escalation_minutes,
        entry_bounds_max_head_count=constants.entry_bounds_max_head_count,
        entry_bounds_max_price_per_head=constants.entry_bounds_max_price_per_head,
        entry_bounds_weight_lower_multiple=constants.entry_bounds_weight_lower_multiple,
        entry_bounds_weight_upper_multiple=constants.entry_bounds_weight_upper_multiple,
        entry_bounds_fallback_weight_min_kg=constants.entry_bounds_fallback_weight_min_kg,
        entry_bounds_fallback_weight_max_kg=constants.entry_bounds_fallback_weight_max_kg,
        benchmark_compare_highlight_threshold_pct=constants.benchmark_compare_highlight_threshold_pct,
        saleyard_calendar=[
            SaleyardCalendarRow(
                saleyard=row.saleyard, day=row.day, prepayment_aud=row.prepayment_aud, note=row.note
            )
            for row in calendar
        ],
    )


@router.get("/versions", response_model=list[ReferenceDataVersionResponse])
async def list_versions(current: CurrentUser = Depends(_trading_console), db: AsyncSession = Depends(get_db)):
    return await reference_data_service.list_versions(db)


@router.get("/versions/{version_id}", response_model=ReferenceDataVersionDetailResponse)
async def get_version(
    version_id: uuid.UUID, current: CurrentUser = Depends(_trading_console), db: AsyncSession = Depends(get_db)
):
    version, entries = await reference_data_service.get_version_with_entries(db, version_id)
    return ReferenceDataVersionDetailResponse(
        **ReferenceDataVersionResponse.model_validate(version).model_dump(),
        entries=[ReferenceDataEntryResponse.model_validate(e) for e in entries],
    )


@router.get("/versions/{version_id}/audit", response_model=list[ReferenceDataAuditEntryResponse])
async def get_version_audit(
    version_id: uuid.UUID, current: CurrentUser = Depends(_trading_console), db: AsyncSession = Depends(get_db)
):
    """Every audit_log entry for this version — created / impact_previewed
    / activated, newest first. Same query shape as GET /users/{id}/audit
    and GET /order-lines/{id}/audit, plus a server-side actor_id -> email
    resolution neither of those needs to do (see
    ReferenceDataAuditEntryResponse's docstring for why).

    `activate_version`'s audit write also embeds a second user reference
    inside its `after` payload — the version's original creator, as a bare
    `created_by` UUID string (services/reference_data_service.py) — which
    would otherwise show up in the UI as raw, unreadable UUID text next to
    the already-resolved actor_email above it. Resolved the same way,
    in-place on the returned before/after dicts, rather than teaching the
    frontend to guess which JSON keys happen to be user references."""
    await reference_data_service.get_version_with_entries(db, version_id)  # 404s if the version doesn't exist

    result = await db.execute(
        select(AuditLog)
        .where(AuditLog.entity == "reference_data_version", AuditLog.entity_id == version_id)
        .order_by(AuditLog.at.desc())
    )
    entries = list(result.scalars().all())

    def _as_uuid(value: object) -> uuid.UUID | None:
        if not isinstance(value, str):
            return None
        try:
            return uuid.UUID(value)
        except ValueError:
            return None

    user_ids = {e.actor_id for e in entries if e.actor_id is not None}
    for e in entries:
        for payload in (e.before, e.after):
            referenced = _as_uuid((payload or {}).get("created_by"))
            if referenced is not None:
                user_ids.add(referenced)

    emails: dict[uuid.UUID, str] = {}
    for user_id in user_ids:
        user = await users_repo.get_by_id(db, user_id)
        if user is not None:
            emails[user_id] = user.email

    def _resolve_refs(payload: dict | None) -> dict | None:
        if payload is None:
            return None
        referenced = _as_uuid(payload.get("created_by"))
        if referenced is None or referenced not in emails:
            return payload
        return {**payload, "created_by": emails[referenced]}

    return [
        ReferenceDataAuditEntryResponse(
            id=e.id,
            action=e.action,
            at=e.at,
            actor_email=emails.get(e.actor_id) if e.actor_id is not None else None,
            before=_resolve_refs(e.before),
            after=_resolve_refs(e.after),
        )
        for e in entries
    ]


@router.post("/versions", response_model=ReferenceDataVersionResponse, status_code=201)
async def create_version(
    body: CreateVersionRequest,
    current: CurrentUser = Depends(_trading_console),
    db: AsyncSession = Depends(get_db),
):
    version = await reference_data_service.create_version(
        db,
        actor_id=current.user_id,
        effective_from=body.effective_from,
        note=body.note,
        raw_entries=[(e.table_key, e.key1, e.key2, e.value, e.text_value) for e in body.entries],
        model_type=body.model_type,
        removals=[(r.table_key, r.key1, r.key2) for r in body.removals],
    )
    await db.commit()
    return version


@router.post("/versions/{version_id}/impact", response_model=ImpactPreviewResponse)
async def preview_impact(
    version_id: uuid.UUID, current: CurrentUser = Depends(_trading_console), db: AsyncSession = Depends(get_db)
):
    preview = await reference_data_service.preview_impact(
        db, version_id, org_id=current.org_id, actor_id=current.user_id
    )
    await db.commit()
    return ImpactPreviewResponse(
        lines=[ImpactLineResponse(**dataclasses.asdict(line)) for line in preview.lines],
        aggregate_exposure_delta_aud=preview.aggregate_exposure_delta_aud,
        lines_affected=preview.lines_affected,
        lines_unpriced=preview.lines_unpriced,
    )


@router.post("/versions/{version_id}/activate", response_model=ReferenceDataVersionResponse)
async def activate_version(
    version_id: uuid.UUID, current: CurrentUser = Depends(_trading_console), db: AsyncSession = Depends(get_db)
):
    version = await reference_data_service.activate_version(db, version_id, actor_id=current.user_id)
    await db.commit()
    return version


@router.get("/species", response_model=list[SpeciesResponse])
async def list_species(current: CurrentUser = Depends(_trading_console), db: AsyncSession = Depends(get_db)):
    return await registry_service.list_species_with_pricing_status(db)


@router.post("/species", response_model=SpeciesResponse, status_code=201)
async def create_species(
    body: CreateSpeciesRequest, current: CurrentUser = Depends(_trading_console), db: AsyncSession = Depends(get_db)
):
    await registry_service.create_species(db, code=body.code, display_name=body.display_name, actor_id=current.user_id)
    await db.commit()
    rows = await registry_service.list_species_with_pricing_status(db)
    return next(row for row in rows if row["code"] == body.code.strip().upper())


@router.get("/product-types", response_model=list[ProductTypeResponse])
async def list_product_types(current: CurrentUser = Depends(_trading_console), db: AsyncSession = Depends(get_db)):
    return await registry_service.list_product_types(db)


@router.post("/product-types", response_model=ProductTypeResponse, status_code=201)
async def create_product_type(
    body: CreateProductTypeRequest,
    current: CurrentUser = Depends(_trading_console),
    db: AsyncSession = Depends(get_db),
):
    row = await registry_service.create_product_type(
        db, code=body.code, display_name=body.display_name, actor_id=current.user_id
    )
    await db.commit()
    return row
