import uuid
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.enums import ReferenceDataTableKey
from models.reference_data import ReferenceDataEntry, ReferenceDataVersion


async def get_active_version(db: AsyncSession) -> ReferenceDataVersion | None:
    result = await db.execute(select(ReferenceDataVersion).where(ReferenceDataVersion.is_active.is_(True)))
    return result.scalar_one_or_none()


async def get_by_id(db: AsyncSession, version_id: uuid.UUID) -> ReferenceDataVersion | None:
    return await db.get(ReferenceDataVersion, version_id)


async def list_versions(db: AsyncSession) -> list[ReferenceDataVersion]:
    result = await db.execute(select(ReferenceDataVersion).order_by(ReferenceDataVersion.created_at.desc()))
    return list(result.scalars().all())


async def list_entries(db: AsyncSession, version_id: uuid.UUID) -> list[ReferenceDataEntry]:
    result = await db.execute(select(ReferenceDataEntry).where(ReferenceDataEntry.version_id == version_id))
    return list(result.scalars().all())


async def create_version(
    db: AsyncSession,
    *,
    effective_from: datetime,
    created_by: uuid.UUID,
    note: str | None,
    model_type: str,
    entries: list[tuple[ReferenceDataTableKey, str | None, str | None, Decimal, str | None]],
) -> ReferenceDataVersion:
    """A version is created whole, with its entries, and never edited
    afterwards (§6.5 non-negotiable: never an in-place edit)."""
    version = ReferenceDataVersion(
        effective_from=effective_from,
        created_by=created_by,
        note=note,
        model_type=model_type,
        is_active=False,
    )
    db.add(version)
    await db.flush()

    for table_key, key1, key2, value, text_value in entries:
        db.add(
            ReferenceDataEntry(
                version_id=version.id, table_key=table_key, key1=key1, key2=key2, value=value, text_value=text_value
            )
        )
    await db.flush()
    return version


async def mark_impact_previewed(db: AsyncSession, version: ReferenceDataVersion) -> None:
    version.impact_previewed_at = datetime.now(UTC)
    await db.flush()


async def activate(db: AsyncSession, version: ReferenceDataVersion, *, activated_by: uuid.UUID) -> None:
    """Flips exactly one version active at a time. Caller (the service
    layer) is responsible for the impact_previewed_at gate and the
    separation-of-duties check — this function only performs the flip once
    permitted. The previous version is deactivated and flushed first: the
    partial unique index on is_active rejects two active rows even
    momentarily, and the unit of work does not guarantee UPDATE order."""
    previous = await get_active_version(db)
    if previous is not None and previous.id != version.id:
        previous.is_active = False
        await db.flush()
    version.is_active = True
    version.activated_at = datetime.now(UTC)
    version.activated_by = activated_by
    await db.flush()
