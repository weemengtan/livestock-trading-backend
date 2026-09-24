import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from domain.engine.model_schedule import ScheduledModel
from models.dnbp_model import DnbpModel, DnbpModelSpecies


def to_scheduled(model: DnbpModel) -> ScheduledModel:
    return ScheduledModel(
        id=model.id,
        activation_at=model.activation_at,
        approved=model.approved_at is not None,
        cancelled=model.cancelled_at is not None,
    )


async def list_models(db: AsyncSession) -> list[DnbpModel]:
    result = await db.execute(select(DnbpModel).order_by(DnbpModel.activation_at.desc(), DnbpModel.created_at.desc()))
    return list(result.scalars().all())


async def get_by_id(db: AsyncSession, model_id: uuid.UUID, *, for_update: bool = False) -> DnbpModel | None:
    stmt = select(DnbpModel).where(DnbpModel.id == model_id)
    if for_update:
        stmt = stmt.with_for_update()
    return (await db.execute(stmt)).scalar_one_or_none()


async def get_by_name(db: AsyncSession, name: str) -> DnbpModel | None:
    return (await db.execute(select(DnbpModel).where(DnbpModel.name == name))).scalar_one_or_none()


async def species_for(db: AsyncSession, model_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[DnbpModelSpecies]]:
    grouped: dict[uuid.UUID, list[DnbpModelSpecies]] = {model_id: [] for model_id in model_ids}
    if not model_ids:
        return grouped
    result = await db.execute(
        select(DnbpModelSpecies).where(DnbpModelSpecies.model_id.in_(model_ids)).order_by(DnbpModelSpecies.species)
    )
    for row in result.scalars().all():
        grouped[row.model_id].append(row)
    return grouped


async def create(
    db: AsyncSession, model: DnbpModel, species_rows: list[DnbpModelSpecies]
) -> DnbpModel:
    db.add(model)
    await db.flush()
    for row in species_rows:
        row.model_id = model.id
        db.add(row)
    await db.flush()
    return model


async def other_approved_at(
    db: AsyncSession, activation_at: datetime, *, excluding: uuid.UUID
) -> DnbpModel | None:
    """An approved, non-cancelled model that already switches on at exactly
    this instant (the partial unique index enforces the same rule)."""
    result = await db.execute(
        select(DnbpModel).where(
            DnbpModel.activation_at == activation_at,
            DnbpModel.approved_at.is_not(None),
            DnbpModel.cancelled_at.is_(None),
            DnbpModel.id != excluding,
        )
    )
    return result.scalars().first()
