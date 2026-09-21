"""Bootstrap only: turns a bootstrap JSON file's `everhealth` block into
`reference_data_entries` rows (used by scripts/seed_reference_data.py to
give an empty dev database its first reference-data version). The file is
business-confidential (pricing parameters, saleyard arrangements) and is
never part of this repository — the caller passes its path explicitly.
Nothing at runtime reads any file: the application reads the active version
from Postgres.

Expected shape (values are yours to supply):

    {
      "effective_from": "YYYY-MM-DD",
      "everhealth": {
        "cif_buffer_per_kg": {"value": <number>},
        "dnbp_factor_by_species": {"values": {"<SPECIES>": <number>, ...}},
        "standard_weight_by_species": {"values": {"<SPECIES>": <number>, ...}},
        "operational_constants": {
          "bid_check_close_threshold_pct": <number>,
          "buyer_weight_band_tolerance_pct": <number>,
          "stale_instruction_hours": <integer>
        },
        "saleyard_calendar": {"values": [
          {"saleyard": "<name>", "day": "MONDAY", "prepayment_aud": <number>, "note": "<text or null>"}
        ]}
      },
      "open_registries": {
        "species": {"seed_rows": ["<SPECIES>", ...]},
        "product_type": {"seed_rows": ["<PRODUCT TYPE>", ...]}
      }
    }
"""

import json
from decimal import Decimal
from pathlib import Path

from models.enums import ReferenceDataTableKey

SeedEntry = tuple[ReferenceDataTableKey, str | None, str | None, Decimal, str | None]  # key, key1, key2, value, text


def load_seed(path: Path) -> dict:
    return json.loads(Path(path).read_text())


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
