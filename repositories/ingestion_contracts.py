import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.ingestion_contract import IngestionContractRecord


async def get_active(db: AsyncSession) -> IngestionContractRecord | None:
    result = await db.execute(select(IngestionContractRecord).where(IngestionContractRecord.is_active.is_(True)))
    return result.scalar_one_or_none()


async def get_by_id(db: AsyncSession, contract_id: uuid.UUID) -> IngestionContractRecord | None:
    return await db.get(IngestionContractRecord, contract_id)


async def get_by_version(db: AsyncSession, version: str) -> IngestionContractRecord | None:
    result = await db.execute(select(IngestionContractRecord).where(IngestionContractRecord.version == version))
    return result.scalar_one_or_none()


async def list_all(db: AsyncSession) -> list[IngestionContractRecord]:
    result = await db.execute(select(IngestionContractRecord).order_by(IngestionContractRecord.created_at.desc()))
    return list(result.scalars().all())


async def create(db: AsyncSession, record: IngestionContractRecord) -> IngestionContractRecord:
    db.add(record)
    await db.flush()
    return record


async def activate(db: AsyncSession, record: IngestionContractRecord, *, activated_by: uuid.UUID) -> None:
    """The previous contract is deactivated and flushed first: the partial
    unique index rejects two active rows even momentarily."""
    previous = await get_active(db)
    if previous is not None and previous.id != record.id:
        previous.is_active = False
        await db.flush()
    record.is_active = True
    record.activated_at = datetime.now(UTC)
    record.activated_by = activated_by
    await db.flush()
