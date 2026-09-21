"""Loads the active reference configuration (§6) — always from Postgres.

Everything here comes from the currently-active `reference_data_versions`
row and its `reference_data_entries`: the DNBP parameters (§6.4, §6.5,
§6.7 cif buffer), the operational tunables (§6.7) and the saleyard calendar
(§6.8). Versioned, effective-dated, audited, editable through the Reference
Data screen (§11.7). No caching and no file reads: activating a new version
takes effect on the very next request, and every process sees the same
values.

The first reference-data version of a new database is created by an operator
(scripts/seed_reference_data.py --file <your bootstrap JSON>, or the Reference
Data screen); nothing at runtime reads any file.
"""

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import NoActiveReferenceDataError, ReferenceDataIncomplete
from domain.engine.config import EverhealthConfig
from models.enums import ReferenceDataTableKey
from models.reference_data import ReferenceDataEntry, ReferenceDataVersion
from repositories import reference_data as reference_data_repo

WEEKDAYS = ("MONDAY", "TUESDAY", "WEDNESDAY", "THURSDAY", "FRIDAY", "SATURDAY", "SUNDAY")


@dataclass(frozen=True, slots=True)
class OperationalConstants:
    """§6.7's operational tunables: Bid Check's PASS/CLOSE threshold
    (§12.3), the buyer's displayed weight band tolerance around a species'
    standard weight (§12.2, D10), and the "instruction is stale" banner
    threshold (§3, §12.2). Versioned and audited like everything else here,
    but none are source-of-truth inputs (§6.5) — they tune supporting UX,
    not `AC`."""

    bid_check_close_threshold_pct: Decimal
    buyer_weight_band_tolerance_pct: Decimal
    stale_instruction_hours: int


@dataclass(frozen=True, slots=True)
class SaleyardCalendarEntry:
    saleyard: str
    day: str  # MONDAY..SUNDAY
    prepayment_aud: Decimal
    note: str | None


async def _load_active(db: AsyncSession) -> tuple[ReferenceDataVersion, list[ReferenceDataEntry]]:
    version = await reference_data_repo.get_active_version(db)
    if version is None:
        raise NoActiveReferenceDataError()
    return version, await reference_data_repo.list_entries(db, version.id)


def _scalar(entries: list[ReferenceDataEntry], key: ReferenceDataTableKey) -> Decimal:
    for entry in entries:
        if entry.table_key == key:
            return entry.value
    raise ReferenceDataIncomplete(key.value)


async def get_active_version_id(db: AsyncSession) -> uuid.UUID:
    version = await reference_data_repo.get_active_version(db)
    if version is None:
        raise NoActiveReferenceDataError()
    return version.id


async def get_active_everhealth_config(db: AsyncSession) -> EverhealthConfig:
    version, entries = await _load_active(db)

    dnbp_factor_by_species: dict[str, Decimal] = {}
    standard_weight_by_species: dict[str, Decimal] = {}
    for entry in entries:
        if entry.table_key == ReferenceDataTableKey.DNBP_FACTOR:
            dnbp_factor_by_species[entry.key1] = entry.value
        elif entry.table_key == ReferenceDataTableKey.STANDARD_WEIGHT:
            standard_weight_by_species[entry.key1] = entry.value

    try:
        cif_buffer_per_kg = _scalar(entries, ReferenceDataTableKey.CIF_BUFFER_PER_KG)
    except ReferenceDataIncomplete as exc:
        raise NoActiveReferenceDataError() from exc

    return EverhealthConfig(
        cif_buffer_per_kg=cif_buffer_per_kg,
        dnbp_factor_by_species=dnbp_factor_by_species,
        standard_weight_by_species=standard_weight_by_species,
        ref_data_version=version.effective_from.date().isoformat(),
        version_id=str(version.id),
        model_type=version.model_type,
    )


async def get_operational_constants(db: AsyncSession) -> OperationalConstants:
    _version, entries = await _load_active(db)
    return OperationalConstants(
        bid_check_close_threshold_pct=_scalar(entries, ReferenceDataTableKey.BID_CHECK_CLOSE_THRESHOLD_PCT),
        buyer_weight_band_tolerance_pct=_scalar(entries, ReferenceDataTableKey.BUYER_WEIGHT_BAND_TOLERANCE_PCT),
        stale_instruction_hours=int(_scalar(entries, ReferenceDataTableKey.STALE_INSTRUCTION_HOURS)),
    )


async def get_saleyard_calendar(db: AsyncSession) -> tuple[SaleyardCalendarEntry, ...]:
    """§6.8, §13.1's prepayment block, in weekday order (then saleyard name)
    so the Buy Instruction export always lists the full schedule in the same
    order regardless of which saleyards actually traded that week."""
    _version, entries = await _load_active(db)
    rows = [
        SaleyardCalendarEntry(
            saleyard=entry.key1,
            day=entry.key2,
            prepayment_aud=entry.value,
            note=entry.text_value,
        )
        for entry in entries
        if entry.table_key == ReferenceDataTableKey.SALEYARD_CALENDAR
    ]
    rows.sort(key=lambda row: (WEEKDAYS.index(row.day), row.saleyard))
    return tuple(rows)


def resolve_saleyard_for_date(trade_date, calendar: tuple[SaleyardCalendarEntry, ...]) -> SaleyardCalendarEntry | None:
    """§6.8's day-of-week -> saleyard mapping, used by the buyer PWA's
    default saleyard (§12.4) and the Instruction screen's prepayment note
    (§12.5). Returns None on a day with no scheduled saleyard (e.g.
    Wednesday) rather than guessing."""
    day_name = trade_date.strftime("%A").upper()
    for entry in calendar:
        if entry.day == day_name:
            return entry
    return None
