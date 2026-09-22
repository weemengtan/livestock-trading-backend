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


def check_entry_bounds(
    *,
    head_count: int,
    price_per_head: Decimal,
    weight_kg: Decimal,
    species: str,
    standard_weight_kg: Decimal | None,
    max_head_count: int,
    max_price_per_head: Decimal,
    weight_lower_multiple: Decimal,
    weight_upper_multiple: Decimal,
    fallback_weight_min_kg: Decimal,
    fallback_weight_max_kg: Decimal,
) -> list[str]:
    """Returns every bound the entry falls outside of (usually one, but a
    genuinely bad entry — extra zeros on both price and weight — can fail
    more than one at once; reporting all of them beats making the buyer
    fix-and-resubmit repeatedly).

    All six bounds are Everhealth-config-editable (reference_data's
    ENTRY_BOUNDS_* keys) — the caller (services/buy_entry_service.py)
    fetches them and passes them in; this function stays a pure
    comparison, same discipline as the rest of this module."""
    violations: list[str] = []

    if not (1 <= head_count <= max_head_count):
        violations.append(f"No. of Heads must be between 1 and {max_head_count}.")

    if not (0 < price_per_head <= max_price_per_head):
        violations.append(f"Price per head must be between $0 and ${max_price_per_head:,.0f}.")

    if standard_weight_kg is not None and standard_weight_kg > 0:
        weight_min = standard_weight_kg * weight_lower_multiple
        weight_max = standard_weight_kg * weight_upper_multiple
    else:
        weight_min, weight_max = fallback_weight_min_kg, fallback_weight_max_kg

    if not (weight_min <= weight_kg <= weight_max):
        violations.append(f"Weight for {species} must be between {weight_min:.1f}kg and {weight_max:.1f}kg per head.")

    return violations
