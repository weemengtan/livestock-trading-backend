import dataclasses
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import CurrentUser, require_role
from core.db import get_db
from core.reference_data import get_operational_constants, get_saleyard_calendar
from domain.engine.dnbp import available_model_types
from models.enums import Role
from schemas.reference_data import (
    ActiveConfigResponse,
    CreateProductTypeRequest,
    CreateSpeciesRequest,
    CreateVersionRequest,
    ImpactLineResponse,
    ImpactPreviewResponse,
    ProductTypeResponse,
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
