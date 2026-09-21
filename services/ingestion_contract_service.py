"""The ingestion contract as versioned, audited configuration.

The runtime always parses under the single active contract in Postgres; a
change is a new version, created by one person and activated by another
(same separation of duties as reference data), and every step is audited.
"""

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from core.config import settings
from core.errors import AppError, Conflict, FourEyesRequired, NotFound
from domain.ingestion.contract import IngestionContract
from domain.ingestion.headers import PARSED_FIELDS, AmbiguousHeaderError, build_header_lookup, normalise
from models.ingestion_contract import IngestionContractRecord
from repositories import ingestion_contracts as contracts_repo
from services import audit_service

# The contract must at least name the columns the DNBP depends on.
_MANDATORY_FIELDS = frozenset({"contract_no", "species", "avg_price_aud"})


class NoActiveIngestionContractError(AppError):
    def __init__(self) -> None:
        super().__init__(
            "NO_ACTIVE_INGESTION_CONTRACT",
            "No active ingestion contract exists — an upload cannot be validated until one is activated.",
            status_code=500,
        )


def to_domain(record: IngestionContractRecord) -> IngestionContract:
    return IngestionContract(
        version=record.version,
        required_sheet_name=record.required_sheet_name,
        active_title_tokens=frozenset(record.active_title_tokens),
        section_end_tokens=frozenset(record.section_end_tokens),
        title_scan_rows=record.title_scan_rows,
        required_columns=dict(record.required_columns),
        header_synonyms={field: frozenset(spellings) for field, spellings in record.header_synonyms.items()},
        header_scan_rows=record.header_scan_rows,
        min_header_matches=record.min_header_matches,
    )


async def get_active_contract(db: AsyncSession) -> IngestionContract:
    record = await contracts_repo.get_active(db)
    if record is None:
        raise NoActiveIngestionContractError()
    return to_domain(record)


def _invalid(message: str) -> AppError:
    return AppError("INVALID_INGESTION_CONTRACT", message, 422)


def validate(
    *,
    version: str,
    required_sheet_name: str,
    active_title_tokens: list[str],
    section_end_tokens: list[str],
    title_scan_rows: int,
    required_columns: dict[str, str],
    header_synonyms: dict[str, list[str]],
    header_scan_rows: int,
    min_header_matches: int,
) -> None:
    if not version.strip():
        raise _invalid("A contract needs a version label.")
    if not required_sheet_name.strip():
        raise _invalid("A contract needs a required sheet name.")
    for name, tokens in (("active title", active_title_tokens), ("section end", section_end_tokens)):
        if not tokens or any(not t.strip() for t in tokens):
            raise _invalid(f"The {name} marker needs at least one word.")
    if not 1 <= title_scan_rows <= 50:
        raise _invalid("title_scan_rows must be between 1 and 50.")
    unknown = sorted((set(required_columns) | set(header_synonyms)) - PARSED_FIELDS)
    if unknown:
        raise _invalid(f"Unknown column field(s): {', '.join(unknown)}.")
    missing = sorted(_MANDATORY_FIELDS - set(required_columns))
    if missing:
        raise _invalid(f"The contract must require: {', '.join(missing)}.")
    if any(not label.strip() for label in required_columns.values()):
        raise _invalid("Every required column needs a label to show the user.")

    cleaned = {f: [s for s in spellings if normalise(s)] for f, spellings in header_synonyms.items()}
    without_headers = sorted(f for f in required_columns if not cleaned.get(f))
    if without_headers:
        raise _invalid(f"Every required column needs at least one accepted header: {', '.join(without_headers)}.")
    try:
        build_header_lookup(cleaned)
    except AmbiguousHeaderError as exc:
        raise _invalid(str(exc)) from exc
    mappable = sum(1 for spellings in cleaned.values() if spellings)
    if not 1 <= header_scan_rows <= 100:
        raise _invalid("header_scan_rows must be between 1 and 100.")
    if not 1 <= min_header_matches <= mappable:
        raise _invalid(f"min_header_matches must be between 1 and the {mappable} fields that have accepted headers.")
    if min_header_matches < len(required_columns):
        raise _invalid("min_header_matches cannot be lower than the number of required columns.")


async def create_contract(
    db: AsyncSession,
    *,
    actor_id: uuid.UUID,
    version: str,
    required_sheet_name: str,
    active_title_tokens: list[str],
    section_end_tokens: list[str],
    title_scan_rows: int,
    required_columns: dict[str, str],
    header_synonyms: dict[str, list[str]],
    header_scan_rows: int,
    min_header_matches: int,
    note: str | None,
) -> IngestionContractRecord:
    active_tokens = [t.strip().lower() for t in active_title_tokens]
    end_tokens = [t.strip().lower() for t in section_end_tokens]
    validate(
        version=version,
        required_sheet_name=required_sheet_name,
        active_title_tokens=active_tokens,
        section_end_tokens=end_tokens,
        title_scan_rows=title_scan_rows,
        required_columns=required_columns,
        header_synonyms=header_synonyms,
        header_scan_rows=header_scan_rows,
        min_header_matches=min_header_matches,
    )
    if await contracts_repo.get_by_version(db, version.strip()) is not None:
        raise Conflict("CONTRACT_VERSION_EXISTS", f"A contract with version '{version.strip()}' already exists.")

    record = await contracts_repo.create(
        db,
        IngestionContractRecord(
            version=version.strip(),
            required_sheet_name=required_sheet_name.strip(),
            active_title_tokens=active_tokens,
            section_end_tokens=end_tokens,
            title_scan_rows=title_scan_rows,
            required_columns=required_columns,
            header_synonyms={
                field: [s.strip() for s in spellings if s.strip()] for field, spellings in header_synonyms.items()
            },
            header_scan_rows=header_scan_rows,
            min_header_matches=min_header_matches,
            note=note,
            is_active=False,
            created_by=actor_id,
        ),
    )
    await audit_service.write(
        db,
        actor_id=actor_id,
        action="ingestion_contract.created",
        entity="ingestion_contract",
        entity_id=record.id,
        after={"version": record.version, "required_sheet_name": record.required_sheet_name, "note": note},
    )
    return record


async def activate_contract(
    db: AsyncSession, contract_id: uuid.UUID, *, actor_id: uuid.UUID
) -> IngestionContractRecord:
    record = await contracts_repo.get_by_id(db, contract_id)
    if record is None:
        raise NotFound("Ingestion contract")
    if settings.reference_data_four_eyes_required and record.created_by == actor_id:
        raise FourEyesRequired()

    previous = await contracts_repo.get_active(db)
    await contracts_repo.activate(db, record, activated_by=actor_id)
    await audit_service.write(
        db,
        actor_id=actor_id,
        action="ingestion_contract.activated",
        entity="ingestion_contract",
        entity_id=record.id,
        before={"previous_version": previous.version if previous else None},
        after={"version": record.version, "required_sheet_name": record.required_sheet_name},
    )
    return record


async def list_contracts(db: AsyncSession) -> list[IngestionContractRecord]:
    return await contracts_repo.list_all(db)
