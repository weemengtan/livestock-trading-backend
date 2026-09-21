"""§5.2 — recomputation of the abattoir's own A-V formulas that can be
derived from the same row alone (Q, U, V), for validation only. Checks that
need the abattoir's separate lookup tables (K, M, N, O) or the abattoir's
fixed cost per head (T) are not performed: this system reads only the
Active Orders block of `Profitability Analysis` and takes no reference
data from other tabs or files.

A mismatch raises a warning/correction. A mismatch raises a warning/correction; this module NEVER overwrites
the received value (Constraint 1) and NEVER computes anything the engine
depends on.

This module must have NO import path to dnbp.py (verified by
tests/engine/test_import_graph.py) and, more broadly, does not import
workings.py or config.py either. Validating the abattoir's arithmetic and
computing Everhealth's price are separate concerns that must never share
code (§17.1) — even though both formulas happen to reference a $0.30
figure, they are independent numbers (K's buffer is CIF-only; X's is
universal, §5.3) that could diverge if either changed, and a shared helper
is exactly how a change to a validation rule could silently move the
published price. The only thing this module shares with the rest of the
engine package is `issues.py`, which carries no calculation logic — plain
data types only.
"""

from decimal import Decimal

from domain.engine import issues as codes
from domain.engine.issues import Severity, ValidationIssue

RELATIVE_TOLERANCE = Decimal("0.000001")  # 1e-6, per §5.2


def expected_mom_ph(
    *,
    nrv_per_kg: Decimal,
    livestock_cost_per_kg: Decimal,
    avg_weight_kg: Decimal,
    offal_return_ph: Decimal,
    skin_return_ph: Decimal,
    pack_cost_ph: Decimal,
) -> Decimal:
    """Q ≈ (P > 0) ? (K - L) * P + N + O - M : 0"""
    if avg_weight_kg <= 0:
        return Decimal(0)
    return (nrv_per_kg - livestock_cost_per_kg) * avg_weight_kg + offal_return_ph + skin_return_ph - pack_cost_ph


def expected_estimated_heads(qty_kg: Decimal, avg_weight_kg: Decimal) -> Decimal:
    """U ≈ (P > 0) ? F / P : 0"""
    if avg_weight_kg <= 0:
        return Decimal(0)
    return qty_kg / avg_weight_kg


def expected_total_livestock_cost(livestock_cost_per_kg: Decimal, qty_kg: Decimal) -> Decimal:
    """V ≈ L * F"""
    return livestock_cost_per_kg * qty_kg


def check(column: str, received: Decimal | None, expected: Decimal | None) -> ValidationIssue | None:
    """Relative-tolerance comparison (§5.2, tolerance 1e-6). None on either
    side means "nothing to cross-check" — not a mismatch. Never overwrites
    `received`; only ever reports."""
    if received is None or expected is None:
        return None

    if expected == 0:
        mismatched = received != 0
    else:
        mismatched = abs((received - expected) / expected) > RELATIVE_TOLERANCE

    if not mismatched:
        return None

    return ValidationIssue(
        codes.RECEIVED_VALUE_MISMATCH,
        Severity.CORRECTION,
        f"Received {column} = {received}, expected {expected}",
        column_ref=column,
    )
