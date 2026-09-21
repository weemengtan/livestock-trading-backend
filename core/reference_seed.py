"""Bootstrap only: turns fixtures/reference-data-seed.json's `everhealth`
block into `reference_data_entries` rows. Used by scripts/seed_reference_data.py
(reseeding an empty dev DB) and the test fixtures. Nothing at runtime reads
the seed file — the application reads the active version from Postgres."""

import json
from decimal import Decimal
from pathlib import Path

from models.enums import ReferenceDataTableKey

SEED_PATH = Path(__file__).resolve().parent.parent / "fixtures" / "reference-data-seed.json"

SeedEntry = tuple[ReferenceDataTableKey, str | None, str | None, Decimal, str | None]  # key, key1, key2, value, text


def load_seed(path: Path = SEED_PATH) -> dict:
    return json.loads(path.read_text())


def seed_entries(seed: dict) -> list[SeedEntry]:
    everhealth = seed["everhealth"]
    constants = everhealth["operational_constants"]
    key = ReferenceDataTableKey
    entries: list[SeedEntry] = [
        (key.CIF_BUFFER_PER_KG, None, None, Decimal(str(everhealth["cif_buffer_per_kg"]["value"])), None),
        (key.BID_CHECK_CLOSE_THRESHOLD_PCT, None, None, Decimal(str(constants["bid_check_close_threshold_pct"])), None),
        (
            key.BUYER_WEIGHT_BAND_TOLERANCE_PCT,
            None,
            None,
            Decimal(str(constants["buyer_weight_band_tolerance_pct"])),
            None,
        ),
        (key.STALE_INSTRUCTION_HOURS, None, None, Decimal(str(constants["stale_instruction_hours"])), None),
    ]
    for species, factor in everhealth["dnbp_factor_by_species"]["values"].items():
        entries.append((key.DNBP_FACTOR, species, None, Decimal(str(factor)), None))
    for species, weight in everhealth["standard_weight_by_species"]["values"].items():
        entries.append((key.STANDARD_WEIGHT, species, None, Decimal(str(weight)), None))
    for row in everhealth["saleyard_calendar"]["values"]:
        entries.append(
            (key.SALEYARD_CALENDAR, row["saleyard"], row["day"], Decimal(str(row["prepayment_aud"])), row.get("note"))
        )
    return entries
