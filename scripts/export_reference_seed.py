"""Export the reference data that is LIVE right now as a bootstrap JSON for
scripts/seed_reference_data.py — so a fresh database (e.g. the Railway test
environment) can start from the same pricing configuration as this one.

Only the current state is exported: the live DNBP model (CIF buffer, per-species
factors and standard weights), the active version's operational tunables, and
the saleyard calendar. Version history and scheduled models are not carried
over. The open species / product-type registries are exported empty because
migrations already create them (seeding them again would collide).

The output is business-confidential: it is written owner-read-only and the
default name matches .gitignore's `*.local.json`. Never commit it.

Run with: uv run python -m scripts.export_reference_seed [--out reference_bootstrap.local.json]
"""

import argparse
import asyncio
import json
import os
from decimal import Decimal
from pathlib import Path

from core.activation_time import activation_date_of
from core.db import async_session_factory
from core.reference_data import (
    _load_active,
    _scalar,
    get_live_model_id,
    get_model_config,
    get_saleyard_calendar,
)
from models.enums import ReferenceDataTableKey as Key
from repositories import dnbp_models as dnbp_models_repo

# (bootstrap JSON field, table key, integer?) — mirrors core/reference_seed.seed_entries.
_CONSTANTS = [
    ("bid_check_close_threshold_pct", Key.BID_CHECK_CLOSE_THRESHOLD_PCT, False),
    ("buyer_weight_band_tolerance_pct", Key.BUYER_WEIGHT_BAND_TOLERANCE_PCT, False),
    ("stale_instruction_hours", Key.STALE_INSTRUCTION_HOURS, True),
    ("dnbp_outlier_threshold_pct", Key.DNBP_OUTLIER_THRESHOLD_PCT, False),
    ("dnbp_outlier_lookback_days", Key.DNBP_OUTLIER_LOOKBACK_DAYS, True),
    ("analytics_trailing_days_for_rate", Key.ANALYTICS_TRAILING_DAYS_FOR_RATE, True),
    ("delivery_escalation_minutes", Key.DELIVERY_ESCALATION_MINUTES, True),
    ("entry_bounds_max_head_count", Key.ENTRY_BOUNDS_MAX_HEAD_COUNT, True),
    ("entry_bounds_max_price_per_head", Key.ENTRY_BOUNDS_MAX_PRICE_PER_HEAD, False),
    ("entry_bounds_weight_lower_multiple", Key.ENTRY_BOUNDS_WEIGHT_LOWER_MULTIPLE, False),
    ("entry_bounds_weight_upper_multiple", Key.ENTRY_BOUNDS_WEIGHT_UPPER_MULTIPLE, False),
    ("entry_bounds_fallback_weight_min_kg", Key.ENTRY_BOUNDS_FALLBACK_WEIGHT_MIN_KG, False),
    ("entry_bounds_fallback_weight_max_kg", Key.ENTRY_BOUNDS_FALLBACK_WEIGHT_MAX_KG, False),
    ("benchmark_compare_highlight_threshold_pct", Key.BENCHMARK_COMPARE_HIGHLIGHT_THRESHOLD_PCT, False),
]


def _num(value: Decimal, *, integer: bool = False) -> int | float:
    # The seed loader re-parses via Decimal(str(x)), so a float round-trips
    # exactly for any value that has at most ~15 significant digits.
    return int(value) if integer else float(value)


async def build_seed() -> dict:
    async with async_session_factory() as session:
        model_id = await get_live_model_id(session)
        model = await dnbp_models_repo.get_by_id(session, model_id)
        config = await get_model_config(session, model_id)
        _version, entries = await _load_active(session)
        calendar = await get_saleyard_calendar(session)

    return {
        # The seed creates the model live from this date, so it must not be in
        # the future: use the date the currently-live model took effect.
        "effective_from": activation_date_of(model.activation_at).isoformat(),
        "everhealth": {
            "cif_buffer_per_kg": {"value": _num(config.cif_buffer_per_kg)},
            "dnbp_factor_by_species": {"values": {s: _num(v) for s, v in config.dnbp_factor_by_species.items()}},
            "standard_weight_by_species": {
                "values": {s: _num(v) for s, v in config.standard_weight_by_species.items()}
            },
            "operational_constants": {
                name: _num(_scalar(entries, key), integer=integer) for name, key, integer in _CONSTANTS
            },
            "saleyard_calendar": {
                "values": [
                    {"saleyard": r.saleyard, "day": r.day, "prepayment_aud": _num(r.prepayment_aud), "note": r.note}
                    for r in calendar
                ]
            },
        },
        "open_registries": {"species": {"seed_rows": []}, "product_type": {"seed_rows": []}},
    }


def main(out: Path) -> None:
    seed = asyncio.run(build_seed())
    fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(seed, f, indent=2)
        f.write("\n")
    eh = seed["everhealth"]
    print(f"Wrote {out} (owner-read-only, confidential — do not commit).")
    print(f"  effective_from: {seed['effective_from']}")
    print(
        f"  species factors: {len(eh['dnbp_factor_by_species']['values'])}, "
        f"standard weights: {len(eh['standard_weight_by_species']['values'])}, "
        f"operational constants: {len(eh['operational_constants'])}, "
        f"saleyard calendar rows: {len(eh['saleyard_calendar']['values'])}"
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", type=Path, default=Path("reference_bootstrap.local.json"))
    main(parser.parse_args().out)
