import uuid

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from api.deps import CurrentUser, require_role
from core.db import get_db
from models.enums import Role
from schemas.ingestion_contracts import CreateIngestionContractRequest, IngestionContractResponse
from services import ingestion_contract_service

router = APIRouter(prefix="/ingestion-contracts", tags=["ingestion-contracts"])

# What the app will accept from an upload is a business-critical rule, so
# changing it is OWNER-only (reading it is open to the trading console).
_owner = require_role(Role.OWNER)
_trading_console = require_role(Role.OWNER, Role.ACCOUNTANT)


@router.get("", response_model=list[IngestionContractResponse])
async def list_contracts(current: CurrentUser = Depends(_trading_console), db: AsyncSession = Depends(get_db)):
    return await ingestion_contract_service.list_contracts(db)


@router.post("", response_model=IngestionContractResponse, status_code=201)
async def create_contract(
    body: CreateIngestionContractRequest,
    current: CurrentUser = Depends(_owner),
    db: AsyncSession = Depends(get_db),
):
    record = await ingestion_contract_service.create_contract(db, actor_id=current.user_id, **body.model_dump())
    await db.commit()
    return record


@router.post("/{contract_id}/activate", response_model=IngestionContractResponse)
async def activate_contract(
    contract_id: uuid.UUID, current: CurrentUser = Depends(_owner), db: AsyncSession = Depends(get_db)
):
    record = await ingestion_contract_service.activate_contract(db, contract_id, actor_id=current.user_id)
    await db.commit()
    return record
