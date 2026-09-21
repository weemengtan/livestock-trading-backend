"""The ingestion contract: the one place that states what an uploaded
workbook must look like for this system to read it.

Business rule: the app reads the Active Orders block of one visible tab and
nothing else — no other tab, and nothing below the ORDERS LOADED marker.
Nothing else in `domain/ingestion` hard-codes a sheet name or marker; it
all comes from an `IngestionContract` handed to `parse()`. At runtime that
is the single active, versioned, audited row in Postgres. A future change
(the abattoir renames the tab, a marker changes) is a new contract version,
not a code change. Each snapshot records the version it was parsed under.
"""

from dataclasses import dataclass

from domain.ingestion.headers import DEFAULT_HEADER_SYNONYMS, build_header_lookup


@dataclass(frozen=True, slots=True)
class IngestionContract:
    version: str
    required_sheet_name: str
    active_title_tokens: frozenset[str]
    section_end_tokens: frozenset[str]  # the ORDERS LOADED marker: a stop boundary only, never parsed
    title_scan_rows: int
    # parsed field name -> label shown to the business user when the column is missing
    required_columns: dict[str, str]
    # parsed field name -> the header spellings accepted for it
    header_synonyms: dict[str, frozenset[str]]
    header_scan_rows: int  # how many rows from the top to look for the header row
    min_header_matches: int  # how many recognised headers make a row "the header row"

    def header_lookup(self) -> dict[str, str]:
        return build_header_lookup(self.header_synonyms)


# Test baseline only: the runtime always loads the active contract from Postgres
# (services/ingestion_contract_service.py); the first row is seeded by an
# Alembic migration with these same values. parse() takes no default.
DEFAULT_CONTRACT = IngestionContract(
    version="profitability-analysis-active-v1",
    required_sheet_name="Profitability Analysis",
    active_title_tokens=frozenset({"active", "orders"}),
    section_end_tokens=frozenset({"loaded", "orders"}),
    title_scan_rows=6,
    required_columns={
        "contract_no": "Contract No.",
        "species": "Type",
        "qty_kg": "Sum of Total QTY",
        "avg_price_aud": "Average of Price AUD",
    },
    header_synonyms=DEFAULT_HEADER_SYNONYMS,
    header_scan_rows=20,
    min_header_matches=6,
)
