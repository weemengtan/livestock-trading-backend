"""Buy Log entry sanity bounds (PRD gap found via manual review, no §
number) — pure, no I/O, mirrored line-for-line in TypeScript at
frontend/lib/buyer/entry-bounds.ts, same discipline as bidcheck.py, so the
PWA can reject an implausible entry before it's even queued offline
(§12.7) instead of accepting it locally and only failing on a later sync
the buyer isn't watching.

These are loose "fat-finger" nets, not business rules — grounded in this
org's own `standard_weight_by_species` reference data (real Schedule
Carcase Hot Weight per head, e.g. LAMB 18kg, SHEEP 22kg — see
core/reference_data.py) and realistic Australian saleyard price/pen-size
ranges, but deliberately wide enough that no genuine trade should ever hit
them. They catch magnitude errors (extra zeros, wrong units — e.g. a
300,000kg/$950,000-per-head VEAL entry), not business judgement calls:
DNBP breach scoring (bidcheck.py) already covers "legitimate but
expensive" with its own PASS/CLOSE/BREACH + required reason flow, and is
untouched by this module."""

from decimal import Decimal

MAX_HEAD_COUNT = 2000
MAX_PRICE_PER_HEAD = Decimal("10000")

# When a species has a configured standard_weight_by_species, the
# plausible per-head weight band is that value's 0.2x-5x — generous next
# to the ±15% buyer_weight_band_tolerance_pct already published to buyers
# (that band stays informational-only; this is a much wider outer net).
WEIGHT_LOWER_MULTIPLE = Decimal("0.2")
WEIGHT_UPPER_MULTIPLE = Decimal("5")

# Fallback when no standard weight is configured for the species (open
# registry, §6.9 — a species can be traded before any reference weight
# exists for it): a flat range wide enough to cover any commonly traded
# livestock species' per-head carcass weight.
FALLBACK_WEIGHT_MIN_KG = Decimal("1")
FALLBACK_WEIGHT_MAX_KG = Decimal("500")


def check_entry_bounds(
    *,
    head_count: int,
    price_per_head: Decimal,
    weight_kg: Decimal,
    species: str,
    standard_weight_kg: Decimal | None,
) -> list[str]:
    """Returns every bound the entry falls outside of (usually one, but a
    genuinely bad entry — extra zeros on both price and weight — can fail
    more than one at once; reporting all of them beats making the buyer
    fix-and-resubmit repeatedly)."""
    violations: list[str] = []

    if not (1 <= head_count <= MAX_HEAD_COUNT):
        violations.append(f"No. of Heads must be between 1 and {MAX_HEAD_COUNT}.")

    if not (0 < price_per_head <= MAX_PRICE_PER_HEAD):
        violations.append(f"Price per head must be between $0 and ${MAX_PRICE_PER_HEAD:,.0f}.")

    if standard_weight_kg is not None and standard_weight_kg > 0:
        weight_min = standard_weight_kg * WEIGHT_LOWER_MULTIPLE
        weight_max = standard_weight_kg * WEIGHT_UPPER_MULTIPLE
    else:
        weight_min, weight_max = FALLBACK_WEIGHT_MIN_KG, FALLBACK_WEIGHT_MAX_KG

    if not (weight_min <= weight_kg <= weight_max):
        violations.append(f"Weight for {species} must be between {weight_min:.1f}kg and {weight_max:.1f}kg per head.")

    return violations
