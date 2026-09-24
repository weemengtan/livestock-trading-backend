"""Loads the active reference configuration (§6) — always from Postgres.

Two independently versioned halves:
  * the DNBP model (§6.4, §6.5, §6.7 cif buffer): whichever `dnbp_models` row
    is live right now — approved, with the latest activation instant that has
    passed (schedulable ahead of time, see domain/engine/model_schedule.py);
  * the operational tunables (§6.7) and saleyard calendar (§6.8): the
    currently-active `reference_data_versions` row and its entries.
Versioned, audited, editable through the Reference Data screen (§11.7). No
caching and no file reads: a change takes effect on the very next request,
and every process sees the same values.

The first reference-data version of a new database is created by an operator
(scripts/seed_reference_data.py --file <your bootstrap JSON>, or the Reference
Data screen); nothing at runtime reads any file.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from core.errors import NoActiveReferenceDataError, ReferenceDataIncomplete
from domain.engine.config import EverhealthConfig
from domain.engine.model_schedule import live_model_id
from models.enums import ReferenceDataTableKey
from models.reference_data import ReferenceDataEntry, ReferenceDataVersion
from repositories import dnbp_models as dnbp_models_repo
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
    dnbp_outlier_threshold_pct: Decimal
    dnbp_outlier_lookback_days: int
    analytics_trailing_days_for_rate: int
    delivery_escalation_minutes: int
    entry_bounds_max_head_count: int
    entry_bounds_max_price_per_head: Decimal
    entry_bounds_weight_lower_multiple: Decimal
    entry_bounds_weight_upper_multiple: Decimal
    entry_bounds_fallback_weight_min_kg: Decimal
    entry_bounds_fallback_weight_max_kg: Decimal
    benchmark_compare_highlight_threshold_pct: Decimal


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


async def get_live_model_id(db: AsyncSession, at: datetime | None = None) -> uuid.UUID:
    """The DNBP model live at `at` (default: now). Resolved from the
    approved schedule and the clock — see domain/engine/model_schedule.py."""
    models = await dnbp_models_repo.list_models(db)
    live_id = live_model_id([dnbp_models_repo.to_scheduled(m) for m in models], at or datetime.now(UTC))
    if live_id is None:
        raise NoActiveReferenceDataError()
    return live_id


async def get_active_everhealth_config(db: AsyncSession, at: datetime | None = None) -> EverhealthConfig:
    """The engine config of the DNBP model live at `at` (default: now).

    Never cached: a scheduled model takes effect on the first request after
    its activation instant, on every process, with no job to run. The
    config's `ref_data_version` label is the model's name and `version_id` its
    id, so every computed workings row names the exact model that produced it.
    """
    model_id = await get_live_model_id(db, at)
    return await get_model_config(db, model_id)


async def get_model_config(db: AsyncSession, model_id: uuid.UUID) -> EverhealthConfig:
    """The engine config for one specific model, live or not (used by the
    impact preview, which prices a model that has not gone live yet)."""
    model = await dnbp_models_repo.get_by_id(db, model_id)
    if model is None:
        raise NoActiveReferenceDataError()
    species_rows = (await dnbp_models_repo.species_for(db, [model.id]))[model.id]
    return EverhealthConfig(
        cif_buffer_per_kg=model.cif_buffer_per_kg,
        dnbp_factor_by_species={r.species: r.dnbp_factor for r in species_rows if r.dnbp_factor is not None},
        standard_weight_by_species={
            r.species: r.standard_weight for r in species_rows if r.standard_weight is not None
        },
        ref_data_version=model.name,
        version_id=str(model.id),
        model_type=model.model_type,
    )


async def get_operational_constants(db: AsyncSession) -> OperationalConstants:
    _version, entries = await _load_active(db)
    return OperationalConstants(
        bid_check_close_threshold_pct=_scalar(entries, ReferenceDataTableKey.BID_CHECK_CLOSE_THRESHOLD_PCT),
        buyer_weight_band_tolerance_pct=_scalar(entries, ReferenceDataTableKey.BUYER_WEIGHT_BAND_TOLERANCE_PCT),
        stale_instruction_hours=int(_scalar(entries, ReferenceDataTableKey.STALE_INSTRUCTION_HOURS)),
        dnbp_outlier_threshold_pct=_scalar(entries, ReferenceDataTableKey.DNBP_OUTLIER_THRESHOLD_PCT),
        dnbp_outlier_lookback_days=int(_scalar(entries, ReferenceDataTableKey.DNBP_OUTLIER_LOOKBACK_DAYS)),
        analytics_trailing_days_for_rate=int(
            _scalar(entries, ReferenceDataTableKey.ANALYTICS_TRAILING_DAYS_FOR_RATE)
        ),
        delivery_escalation_minutes=int(_scalar(entries, ReferenceDataTableKey.DELIVERY_ESCALATION_MINUTES)),
        entry_bounds_max_head_count=int(_scalar(entries, ReferenceDataTableKey.ENTRY_BOUNDS_MAX_HEAD_COUNT)),
        entry_bounds_max_price_per_head=_scalar(entries, ReferenceDataTableKey.ENTRY_BOUNDS_MAX_PRICE_PER_HEAD),
        entry_bounds_weight_lower_multiple=_scalar(
            entries, ReferenceDataTableKey.ENTRY_BOUNDS_WEIGHT_LOWER_MULTIPLE
        ),
        entry_bounds_weight_upper_multiple=_scalar(
            entries, ReferenceDataTableKey.ENTRY_BOUNDS_WEIGHT_UPPER_MULTIPLE
        ),
        entry_bounds_fallback_weight_min_kg=_scalar(
            entries, ReferenceDataTableKey.ENTRY_BOUNDS_FALLBACK_WEIGHT_MIN_KG
        ),
        entry_bounds_fallback_weight_max_kg=_scalar(
            entries, ReferenceDataTableKey.ENTRY_BOUNDS_FALLBACK_WEIGHT_MAX_KG
        ),
        benchmark_compare_highlight_threshold_pct=_scalar(
            entries, ReferenceDataTableKey.BENCHMARK_COMPARE_HIGHLIGHT_THRESHOLD_PCT
        ),
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
