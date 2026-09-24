"""DNBP models: create, preview, approve, reschedule, cancel — and the guard
that keeps a snapshot calculated under one model from being published under
another.

Fetch-then-compute split, same discipline as reference_data_service.py: this
module fetches and persists; which model is live and what status each one has
is domain.engine.model_schedule, and the before/after price math is
domain.engine.impact.compute_impact — neither is reimplemented here.

Lifecycle: create (DRAFT) -> impact preview -> approve (SCHEDULED, by someone
other than the creator when four-eyes is on) -> goes live by itself when its
Singapore-midnight instant passes. Nothing "activates" it; see
core/reference_data.get_live_model_id.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from core.activation_time import activation_instant_for, singapore_today
from core.config import settings
from core.errors import (
    ActivationDateNotInFuture,
    ActivationDateTaken,
    AppError,
    CalculatedUnderOlderModel,
    Conflict,
    ImpactPreviewRequired,
    ModelAlreadyLive,
    ModelFourEyesRequired,
    NoActiveReferenceDataError,
    NotFound,
    PublishedUnderOlderModel,
)
from core.reference_data import get_active_everhealth_config, get_live_model_id, get_model_config
from domain.engine.dnbp import available_model_types
from domain.engine.impact import ImpactLineInput, ImpactPreview, compute_impact
from domain.engine.model_schedule import ModelStatus, can_still_change, live_model_id, status_of
from models.dnbp_model import DnbpModel, DnbpModelSpecies
from repositories import dnbp_models as dnbp_models_repo
from repositories import order_lines as order_lines_repo
from repositories import order_snapshots as order_snapshots_repo
from repositories import order_workings as order_workings_repo
from repositories import registries as registries_repo
from services import audit_service


@dataclass(frozen=True, slots=True)
class SpeciesParams:
    species: str
    dnbp_factor: Decimal | None
    standard_weight: Decimal | None


@dataclass(frozen=True, slots=True)
class ModelView:
    model: DnbpModel
    species: list[DnbpModelSpecies]
    status: ModelStatus
    can_still_change: bool


def _invalid(message: str) -> AppError:
    return AppError("INVALID_DNBP_MODEL", message, 422)


def _future_instant(activation_date: date, now: datetime) -> datetime:
    instant = activation_instant_for(activation_date)
    if instant <= now:
        raise ActivationDateNotInFuture((singapore_today(now) + timedelta(days=1)).isoformat())
    return instant


# --- reads ------------------------------------------------------------------


async def list_models(db: AsyncSession) -> list[ModelView]:
    now = datetime.now(UTC)
    models = await dnbp_models_repo.list_models(db)
    live_id = live_model_id([dnbp_models_repo.to_scheduled(m) for m in models], now)
    species = await dnbp_models_repo.species_for(db, [m.id for m in models])
    return [_view(m, species[m.id], live_id, now) for m in models]


async def get_model(db: AsyncSession, model_id: uuid.UUID) -> ModelView:
    models = await dnbp_models_repo.list_models(db)
    target = next((m for m in models if m.id == model_id), None)
    if target is None:
        raise NotFound("DNBP model")
    now = datetime.now(UTC)
    live_id = live_model_id([dnbp_models_repo.to_scheduled(m) for m in models], now)
    species = (await dnbp_models_repo.species_for(db, [target.id]))[target.id]
    return _view(target, species, live_id, now)


def _view(model: DnbpModel, species: list[DnbpModelSpecies], live_id: uuid.UUID | None, now: datetime) -> ModelView:
    scheduled = dnbp_models_repo.to_scheduled(model)
    return ModelView(
        model=model,
        species=species,
        status=status_of(scheduled, live_id=live_id, now=now),
        can_still_change=can_still_change(scheduled, now=now),
    )


# --- create -----------------------------------------------------------------


async def create_model(
    db: AsyncSession,
    *,
    actor_id: uuid.UUID,
    name: str,
    note: str | None,
    model_type: str | None,
    cif_buffer_per_kg: Decimal,
    species: list[SpeciesParams],
    activation_date: date,
) -> ModelView:
    now = datetime.now(UTC)
    clean_name = name.strip()
    if not clean_name:
        raise _invalid("A model needs a name.")
    if await dnbp_models_repo.get_by_name(db, clean_name) is not None:
        raise AppError("DNBP_MODEL_NAME_TAKEN", f"A model named '{clean_name}' already exists.", 409)

    # Default: the model currently live, so "clone and tweak" never silently
    # changes the formula.
    resolved_type = model_type or (await get_active_everhealth_config(db)).model_type
    if resolved_type not in available_model_types():
        raise AppError(
            "UNKNOWN_MODEL_TYPE",
            f"'{resolved_type}' is not an implemented DNBP model. Available: {', '.join(available_model_types())}.",
            422,
        )
    if cif_buffer_per_kg < 0:
        raise _invalid("The CIF buffer cannot be negative.")

    rows: list[DnbpModelSpecies] = []
    seen: set[str] = set()
    for entry in species:
        code = entry.species.strip().upper()
        if not code:
            raise _invalid("Every species row needs a species.")
        if code in seen:
            raise _invalid(f"{code} appears more than once.")
        seen.add(code)
        if entry.dnbp_factor is None and entry.standard_weight is None:
            raise _invalid(f"{code} needs a DNBP factor, a standard weight, or both.")
        if entry.dnbp_factor is not None and entry.dnbp_factor <= 0:
            raise _invalid(f"{code}: the DNBP factor must be greater than 0.")
        if entry.standard_weight is not None and entry.standard_weight <= 0:
            raise _invalid(f"{code}: the standard weight must be greater than 0.")
        registered = await registries_repo.get_species(db, code)
        if registered is None or not registered.is_active:
            raise _invalid(f"{code} is not an active species — add it under Species & Product Types first.")
        rows.append(
            DnbpModelSpecies(species=code, dnbp_factor=entry.dnbp_factor, standard_weight=entry.standard_weight)
        )
    if not any(r.dnbp_factor is not None for r in rows):
        raise _invalid("A model needs at least one species with a DNBP factor.")

    model = DnbpModel(
        name=clean_name,
        note=note,
        model_type=resolved_type,
        cif_buffer_per_kg=cif_buffer_per_kg,
        activation_at=_future_instant(activation_date, now),
        created_by=actor_id,
    )
    await dnbp_models_repo.create(db, model, rows)
    await audit_service.write(
        db,
        actor_id=actor_id,
        action="dnbp_model.created",
        entity="dnbp_model",
        entity_id=model.id,
        after=_snapshot_of(model, rows),
    )
    return await get_model(db, model.id)


def _snapshot_of(model: DnbpModel, rows: list[DnbpModelSpecies]) -> dict:
    return {
        "name": model.name,
        "model_type": model.model_type,
        "cif_buffer_per_kg": str(model.cif_buffer_per_kg),
        "activation_at": model.activation_at.isoformat(),
        "species": [
            {
                "species": r.species,
                "dnbp_factor": str(r.dnbp_factor) if r.dnbp_factor is not None else None,
                "standard_weight": str(r.standard_weight) if r.standard_weight is not None else None,
            }
            for r in rows
        ],
    }


# --- impact preview ---------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ModelImpact:
    preview: ImpactPreview
    baseline_model_name: str


async def preview_impact(
    db: AsyncSession, model_id: uuid.UUID, *, org_id: uuid.UUID, actor_id: uuid.UUID
) -> ModelImpact:
    """Prices the latest snapshot under this model versus the model that
    would be live just before it switches on. Marks the model previewed —
    the gate approval checks."""
    model = await _lock_changeable(db, model_id)
    now = datetime.now(UTC)

    try:
        baseline = await get_active_everhealth_config(db, at=model.activation_at - timedelta(seconds=1))
    except NoActiveReferenceDataError:
        baseline = await get_active_everhealth_config(db, at=now)
    new_config = await get_model_config(db, model.id)

    latest_snapshot = await order_snapshots_repo.get_latest_for_org(db, org_id)
    lines: list[ImpactLineInput] = []
    if latest_snapshot is not None:
        lines = [
            ImpactLineInput(
                order_line_id=str(line.id),
                contract_no=line.contract_no,
                species=line.species,
                avg_price_aud=line.avg_price_aud,
                qty_kg=line.qty_kg,
            )
            for line in await order_lines_repo.list_by_snapshot(db, latest_snapshot.id)
            if line.species is not None
        ]

    preview = compute_impact(lines, old_config=baseline, new_config=new_config)

    model.impact_previewed_at = now
    await db.flush()
    await audit_service.write(
        db,
        actor_id=actor_id,
        action="dnbp_model.impact_previewed",
        entity="dnbp_model",
        entity_id=model.id,
        after={
            "baseline_model": baseline.ref_data_version,
            "lines_affected": preview.lines_affected,
            "aggregate_exposure_delta_aud": str(preview.aggregate_exposure_delta_aud),
        },
    )
    return ModelImpact(preview=preview, baseline_model_name=baseline.ref_data_version)


# --- approve / reschedule / cancel -----------------------------------------


async def _lock_changeable(db: AsyncSession, model_id: uuid.UUID) -> DnbpModel:
    """Loads the row under a lock and refuses if the model has gone live or
    been cancelled — evaluated against the clock *under the lock*, so a
    change racing a switch-over cannot slip past."""
    model = await dnbp_models_repo.get_by_id(db, model_id, for_update=True)
    if model is None:
        raise NotFound("DNBP model")
    if model.cancelled_at is not None:
        raise Conflict("MODEL_CANCELLED", "This model has been cancelled.")
    if not can_still_change(dnbp_models_repo.to_scheduled(model), now=datetime.now(UTC)):
        raise ModelAlreadyLive()
    return model


async def approve(db: AsyncSession, model_id: uuid.UUID, *, actor_id: uuid.UUID) -> ModelView:
    model = await _lock_changeable(db, model_id)
    if model.approved_at is not None:
        raise Conflict("MODEL_ALREADY_APPROVED", "This model is already approved and scheduled.")
    if model.impact_previewed_at is None:
        raise ImpactPreviewRequired()
    if settings.reference_data_four_eyes_required and model.created_by == actor_id:
        raise ModelFourEyesRequired()

    now = datetime.now(UTC)
    if model.activation_at <= now:
        raise ActivationDateNotInFuture((singapore_today(now) + timedelta(days=1)).isoformat())
    await _refuse_if_date_taken(db, model)

    model.approved_by = actor_id
    model.approved_at = now
    try:
        await db.flush()
    except IntegrityError as exc:  # a concurrent approval took the same instant
        raise Conflict("ACTIVATION_DATE_TAKEN", "Another model was just scheduled for that same moment.") from exc

    await audit_service.write(
        db,
        actor_id=actor_id,
        action="dnbp_model.approved",
        entity="dnbp_model",
        entity_id=model.id,
        after={"activation_at": model.activation_at.isoformat(), "created_by": str(model.created_by)},
    )
    return await get_model(db, model.id)


async def _refuse_if_date_taken(db: AsyncSession, model: DnbpModel) -> None:
    other = await dnbp_models_repo.other_approved_at(db, model.activation_at, excluding=model.id)
    if other is not None:
        raise ActivationDateTaken(other.name)


async def reschedule(
    db: AsyncSession, model_id: uuid.UUID, *, actor_id: uuid.UUID, activation_date: date
) -> ModelView:
    """Moves the switch date. An approved model drops back to DRAFT — its
    old schedule is withdrawn and the new date needs a fresh preview and
    approval, because the model it replaces (and so the impact) can differ."""
    model = await _lock_changeable(db, model_id)
    now = datetime.now(UTC)
    new_instant = _future_instant(activation_date, now)

    before = {
        "activation_at": model.activation_at.isoformat(),
        "was_approved": model.approved_at is not None,
    }
    model.activation_at = new_instant
    model.approved_by = None
    model.approved_at = None
    model.impact_previewed_at = None
    await db.flush()

    await audit_service.write(
        db,
        actor_id=actor_id,
        action="dnbp_model.rescheduled",
        entity="dnbp_model",
        entity_id=model.id,
        before=before,
        after={"activation_at": new_instant.isoformat()},
    )
    return await get_model(db, model.id)


async def cancel(db: AsyncSession, model_id: uuid.UUID, *, actor_id: uuid.UUID) -> ModelView:
    model = await _lock_changeable(db, model_id)
    model.cancelled_by = actor_id
    model.cancelled_at = datetime.now(UTC)
    await db.flush()
    await audit_service.write(
        db,
        actor_id=actor_id,
        action="dnbp_model.cancelled",
        entity="dnbp_model",
        entity_id=model.id,
        before={"activation_at": model.activation_at.isoformat(), "was_approved": model.approved_at is not None},
    )
    return await get_model(db, model.id)


# --- stale-calculation guard ------------------------------------------------


@dataclass(frozen=True, slots=True)
class ModelStamp:
    model_id: uuid.UUID | None
    name: str
    line_count: int


@dataclass(frozen=True, slots=True)
class SnapshotModelStatus:
    live_model_id: uuid.UUID
    live_model_name: str
    calculated_under: list[ModelStamp]
    stale: bool


async def snapshot_model_status(db: AsyncSession, snapshot_id: uuid.UUID) -> SnapshotModelStatus:
    """Which model(s) a snapshot's workings were computed under, versus the
    model live right now. Stale when any line was computed under something
    else — including a legacy row with no stamp, which cannot be proven
    current. A snapshot with no workings yet is not stale: there is nothing
    to be out of date."""
    live_id = await get_live_model_id(db)
    live_config = await get_active_everhealth_config(db)
    stamps = await order_workings_repo.model_stamps_for_snapshot(db, snapshot_id)
    calculated_under = [ModelStamp(model_id=mid, name=name, line_count=count) for mid, name, count in stamps]
    stale = any(stamp.model_id != live_id for stamp in calculated_under)
    return SnapshotModelStatus(
        live_model_id=live_id,
        live_model_name=live_config.ref_data_version,
        calculated_under=calculated_under,
        stale=stale,
    )


async def assert_published_snapshot_on_live_model(db: AsyncSession, snapshot_id: uuid.UUID) -> None:
    status = await snapshot_model_status(db, snapshot_id)
    if status.stale:
        raise PublishedUnderOlderModel(
            status.live_model_name,
            sorted({stamp.name for stamp in status.calculated_under if stamp.model_id != status.live_model_id}),
        )


async def assert_not_stale(db: AsyncSession, snapshot_id: uuid.UUID) -> None:
    status = await snapshot_model_status(db, snapshot_id)
    if status.stale:
        raise CalculatedUnderOlderModel(
            status.live_model_name,
            sorted({stamp.name for stamp in status.calculated_under if stamp.model_id != status.live_model_id}),
        )
