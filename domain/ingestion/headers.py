"""Header-text column mapping (PRD §7.2 points 1-2).

Never map by column letter or fixed cell address. A sheet's header row is
located by scanning for known header tokens, and each column is mapped to a
field name by normalising its header text and matching it against the
contract's synonym registry — verified against the real workbooks, where the
same field lands on different letters (the benchmark column is `T` in one
file, `S` in the other) and even the header row index differs (7 vs 4).

The accepted spellings live in the ingestion contract (versioned config in
Postgres), not here: a renamed header is a new contract version, not a code
change. `DEFAULT_HEADER_SYNONYMS` below is the baseline the first contract
was seeded with (and the test baseline); `PARSED_FIELDS` is the closed set of
fields the parser knows how to read.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

# field name -> set of normalised header texts that map to it. Extended
# beyond PRD §7.2's sketch with every header actually observed in the
# supplied workbooks. Baseline only — the runtime uses the active contract's.
DEFAULT_HEADER_SYNONYMS: dict[str, frozenset[str]] = {
    "contract_no": frozenset({"row labels", "contract no", "contract no.", "order number"}),
    "customer_name": frozenset({"customer name"}),
    "species": frozenset({"type", "species"}),
    "loadout_date": frozenset({"loadout date"}),
    "qty_kg": frozenset({"sum of total qty", "total qty", "qty"}),
    "avg_price_aud": frozenset({"average of price aud", "avg price aud", "price aud"}),
    "amount_aud": frozenset({"sum of amount aud", "amount aud"}),
    "product_type": frozenset({"product type"}),
    "incoterm": frozenset({"cif or fas ?", "cif or fas", "incoterm"}),
    "nrv_per_kg": frozenset({"nrv per kg", "nrv (per kg)"}),
    "expected_livestock_cost_per_kg": frozenset(
        {
            "livestock cost per kg hscw",
            "expected livestock cost per kg hscw",
        }
    ),
    "pack_cost_ph": frozenset({"pack cost"}),
    "offal_return_ph": frozenset({"offal return ph"}),
    "skin_return_ph": frozenset({"avg skin return", "skin return"}),
    "avg_weight_kg": frozenset({"avg weight", "average weight"}),
    "mom_ph": frozenset({"mom ph"}),
    "deposit_received": frozenset({"deposit received"}),
    "comments": frozenset({"comments"}),
    # Both benchmark spellings map to the same field; benchmark.py decides
    # GAYAN_FIXED_COST vs FINANCIER_MARGIN from the header text separately.
    "dnbp_benchmark": frozenset(
        {
            "do not buy price",
            # normalise() strips punctuation (including "%") from real
            # header text before comparison, so this entry must already be
            # in that stripped form or it can never match.
            "do not buy price using method instructed by the financier",
        }
    ),
    "estimated_heads": frozenset({"estimated number of heads required"}),
    "total_livestock_cost": frozenset({"total livestock cost"}),
}

_PUNCTUATION_RE = re.compile(r"[^\w\s]")
_WHITESPACE_RE = re.compile(r"\s+")


def normalise(text: object) -> str:
    """Lowercase, strip punctuation, collapse whitespace. The one function
    every header/enum comparison in this package goes through."""
    if text is None:
        return ""
    s = str(text).strip().lower()
    s = s.replace("_", " ")  # real headers use "_" as a stray word separator (e.g. "Price_Using")
    s = _PUNCTUATION_RE.sub("", s)
    s = _WHITESPACE_RE.sub(" ", s)
    return s.strip()


def tokenize(text: object) -> frozenset[str]:
    """Word-set of a normalised string, order-independent. Used for the
    marker-row / title-cell tests below — real data writes "LOADED ORDERS"
    in one sheet and "ORDERS LOADED" in another, purely from manual
    inconsistency, so word order must never matter."""
    return frozenset(normalise(text).split())


# The closed set of fields the parser can read (a new one is a code change).
PARSED_FIELDS = frozenset(DEFAULT_HEADER_SYNONYMS)


class AmbiguousHeaderError(ValueError):
    """The same header text is listed for two different fields."""


def build_header_lookup(synonyms: Mapping[str, Iterable[str]]) -> dict[str, str]:
    """normalised header text -> field name. A header that maps to two fields
    would make a column's meaning depend on dict order, so it is rejected."""
    lookup: dict[str, str] = {}
    for field_name, spellings in synonyms.items():
        for spelling in spellings:
            key = normalise(spelling)
            if not key:
                continue
            if key in lookup and lookup[key] != field_name:
                raise AmbiguousHeaderError(
                    f"'{spelling}' is listed for both '{lookup[key]}' and '{field_name}'."
                )
            lookup[key] = field_name
    return lookup


def map_header_cell(text: object, lookup: Mapping[str, str]) -> str | None:
    """Field name for one header cell's text, or None if unrecognised. An
    unrecognised header is not an error — it's a column this system doesn't
    need (or a future column); the row can still be a valid header row via
    the other cells."""
    return lookup.get(normalise(text))


@dataclass(frozen=True, slots=True)
class HeaderRow:
    row_index: int  # 1-based, openpyxl convention
    column_map: dict[int, str]  # 1-based column index -> field name
    match_count: int


def find_header_row(
    sheet,
    lookup: Mapping[str, str],
    *,
    min_matches: int,
    max_scan_rows: int,
    start_row: int = 1,
) -> HeaderRow | None:
    """Scan up to `max_scan_rows` rows of `sheet` starting at `start_row`
    (an openpyxl worksheet opened with data_only=True — header text is never
    a formula) for a row whose cells match >= `min_matches` entries of the
    contract's header lookup. Returns the best-matching row in that window,
    or None if no row qualifies."""
    best: HeaderRow | None = None
    last_row = min(start_row + max_scan_rows - 1, sheet.max_row)
    for row_idx in range(start_row, last_row + 1):
        column_map: dict[int, str] = {}
        seen_fields: set[str] = set()
        for col_idx in range(1, sheet.max_column + 1):
            field = map_header_cell(sheet.cell(row=row_idx, column=col_idx).value, lookup)
            # First occurrence wins. Some files carry a "workings echo" block
            # that re-uses identical header text for several columns
            # ("Average of Price AUD" appears at both G and X, "Pack Cost" at
            # both M and Y, ...) — the received block always comes first, so
            # a later duplicate is the echo, never the source value, and must
            # not overwrite the real mapping.
            if field is not None and field not in seen_fields:
                column_map[col_idx] = field
                seen_fields.add(field)
        if len(column_map) >= min_matches and (best is None or len(column_map) > best.match_count):
            best = HeaderRow(row_index=row_idx, column_map=column_map, match_count=len(column_map))
    return best
