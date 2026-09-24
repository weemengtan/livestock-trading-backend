import dataclasses
import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import CurrentUser, require_role
from core.activation_time import activation_date_of, describe_activation
from core.db import get_db
from models.enums import Role
from repositories import users as users_repo
from schemas.dnbp_models import (
    ActivationDisplay,
    CreateDnbpModelRequest,
    DnbpModelResponse,
    DnbpModelSpeciesResponse,
    ModelImpactLine,
    ModelImpactResponse,
    RescheduleDnbpModelRequest,
)
from services import dnbp_model_service
from services.dnbp_model_service import ModelView, SpeciesParams

router = APIRouter(prefix="/dnbp-models", tags=["dnbp-models"])

# Same audience as the rest of Reference Data (§9.8): Bobby and Bing.
_trading_console = require_role(Role.OWNER, Role.ACCOUNTANT)


async def _email(db: AsyncSession, user_id: uuid.UUID | None) -> str | None:
    if user_id is None:
        return None
    user = await users_repo.get_by_id(db, user_id)
    return user.email if user is not None else None


async def _to_response(db: AsyncSession, view: ModelView) -> DnbpModelResponse:
    m = view.model
    return DnbpModelResponse(
        id=m.id,
        name=m.name,
        note=m.note,
        model_type=m.model_type,
        cif_buffer_per_kg=m.cif_buffer_per_kg,
        species=[
            DnbpModelSpeciesResponse(
                species=s.species, dnbp_factor=s.dnbp_factor, standard_weight=s.standard_weight
            )
            for s in view.species
        ],
        status=view.status.value,
        can_still_change=view.can_still_change,
        activation_at=m.activation_at,
        activation_date=activation_date_of(m.activation_at),
        activation_display=ActivationDisplay(**describe_activation(m.activation_at)),
        created_by_email=await _email(db, m.created_by),
        created_at=m.created_at,
        impact_previewed_at=m.impact_previewed_at,
        approved_by_email=await _email(db, m.approved_by),
        approved_at=m.approved_at,
        cancelled_at=m.cancelled_at,
    )


@router.get("", response_model=list[DnbpModelResponse])
async def list_models(current: CurrentUser = Depends(_trading_console), db: AsyncSession = Depends(get_db)):
    return [await _to_response(db, view) for view in await dnbp_model_service.list_models(db)]


@router.get("/{model_id}", response_model=DnbpModelResponse)
async def get_model(
    model_id: uuid.UUID, current: CurrentUser = Depends(_trading_console), db: AsyncSession = Depends(get_db)
):
    return await _to_response(db, await dnbp_model_service.get_model(db, model_id))


@router.post("", response_model=DnbpModelResponse, status_code=201)
async def create_model(
    body: CreateDnbpModelRequest, current: CurrentUser = Depends(_trading_console), db: AsyncSession = Depends(get_db)
):
    view = await dnbp_model_service.create_model(
        db,
        actor_id=current.user_id,
        name=body.name,
        note=body.note,
        model_type=body.model_type,
        cif_buffer_per_kg=body.cif_buffer_per_kg,
        species=[SpeciesParams(s.species, s.dnbp_factor, s.standard_weight) for s in body.species],
        activation_date=body.activation_date,
    )
    response = await _to_response(db, view)
    await db.commit()
    return response


@router.post("/{model_id}/impact", response_model=ModelImpactResponse)
async def preview_impact(
    model_id: uuid.UUID, current: CurrentUser = Depends(_trading_console), db: AsyncSession = Depends(get_db)
):
    impact = await dnbp_model_service.preview_impact(db, model_id, org_id=current.org_id, actor_id=current.user_id)
    await db.commit()
    return ModelImpactResponse(
        baseline_model=impact.baseline_model_name,
        lines=[ModelImpactLine(**dataclasses.asdict(line)) for line in impact.preview.lines],
        aggregate_exposure_delta_aud=impact.preview.aggregate_exposure_delta_aud,
        lines_affected=impact.preview.lines_affected,
        lines_unpriced=impact.preview.lines_unpriced,
    )


@router.post("/{model_id}/approve", response_model=DnbpModelResponse)
async def approve_model(
    model_id: uuid.UUID, current: CurrentUser = Depends(_trading_console), db: AsyncSession = Depends(get_db)
):
    view = await dnbp_model_service.approve(db, model_id, actor_id=current.user_id)
    response = await _to_response(db, view)
    await db.commit()
    return response


@router.post("/{model_id}/reschedule", response_model=DnbpModelResponse)
async def reschedule_model(
    model_id: uuid.UUID,
    body: RescheduleDnbpModelRequest,
    current: CurrentUser = Depends(_trading_console),
    db: AsyncSession = Depends(get_db),
):
    view = await dnbp_model_service.reschedule(
        db, model_id, actor_id=current.user_id, activation_date=body.activation_date
    )
    response = await _to_response(db, view)
    await db.commit()
    return response


@router.post("/{model_id}/cancel", response_model=DnbpModelResponse)
async def cancel_model(
    model_id: uuid.UUID, current: CurrentUser = Depends(_trading_console), db: AsyncSession = Depends(get_db)
):
    view = await dnbp_model_service.cancel(db, model_id, actor_id=current.user_id)
    response = await _to_response(db, view)
    await db.commit()
    return response
