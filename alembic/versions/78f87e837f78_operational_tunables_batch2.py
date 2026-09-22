"""operational tunables batch 2: DNBP outlier, entry bounds, delivery
escalation, benchmark-compare highlight

A second round of business constants that were hardcoded in Python/TS
source move into the same versioned, audited reference_data_entries table
as the first operational-constants batch (9d4f1c7a2e55). Every version that
already exists — in practice just the currently-active one, since
9d4f1c7a2e55 already made every version a complete snapshot — is backfilled
once with these 11 keys using the values that were previously hardcoded, so
behaviour is unchanged the moment this migration runs. A version with no
rows (fresh/dev DB, no active version yet) is left alone — same discipline
as 9d4f1c7a2e55's backfill.

Revision ID: 78f87e837f78
Revises: e7a3c2b19d64
Create Date: 2026-09-22 00:00:00.000000

"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '78f87e837f78'
down_revision: Union[str, None] = 'e7a3c2b19d64'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_TABLE_KEYS = (
    "DNBP_OUTLIER_THRESHOLD_PCT",
    "DNBP_OUTLIER_LOOKBACK_DAYS",
    "ANALYTICS_TRAILING_DAYS_FOR_RATE",
    "DELIVERY_ESCALATION_MINUTES",
    "ENTRY_BOUNDS_MAX_HEAD_COUNT",
    "ENTRY_BOUNDS_MAX_PRICE_PER_HEAD",
    "ENTRY_BOUNDS_WEIGHT_LOWER_MULTIPLE",
    "ENTRY_BOUNDS_WEIGHT_UPPER_MULTIPLE",
    "ENTRY_BOUNDS_FALLBACK_WEIGHT_MIN_KG",
    "ENTRY_BOUNDS_FALLBACK_WEIGHT_MAX_KG",
    "BENCHMARK_COMPARE_HIGHLIGHT_THRESHOLD_PCT",
)


def _seed_entries() -> list[dict]:
    # The exact values that were previously hardcoded in Python/TS source.
    return [
        {"table_key": "DNBP_OUTLIER_THRESHOLD_PCT", "key1": None, "key2": None, "value": "15", "text_value": None},
        {"table_key": "DNBP_OUTLIER_LOOKBACK_DAYS", "key1": None, "key2": None, "value": "30", "text_value": None},
        {"table_key": "ANALYTICS_TRAILING_DAYS_FOR_RATE", "key1": None, "key2": None, "value": "7", "text_value": None},
        {"table_key": "DELIVERY_ESCALATION_MINUTES", "key1": None, "key2": None, "value": "15", "text_value": None},
        {"table_key": "ENTRY_BOUNDS_MAX_HEAD_COUNT", "key1": None, "key2": None, "value": "2000", "text_value": None},
        {"table_key": "ENTRY_BOUNDS_MAX_PRICE_PER_HEAD", "key1": None, "key2": None, "value": "10000", "text_value": None},
        {"table_key": "ENTRY_BOUNDS_WEIGHT_LOWER_MULTIPLE", "key1": None, "key2": None, "value": "0.2", "text_value": None},
        {"table_key": "ENTRY_BOUNDS_WEIGHT_UPPER_MULTIPLE", "key1": None, "key2": None, "value": "5", "text_value": None},
        {"table_key": "ENTRY_BOUNDS_FALLBACK_WEIGHT_MIN_KG", "key1": None, "key2": None, "value": "1", "text_value": None},
        {"table_key": "ENTRY_BOUNDS_FALLBACK_WEIGHT_MAX_KG", "key1": None, "key2": None, "value": "500", "text_value": None},
        {
            "table_key": "BENCHMARK_COMPARE_HIGHLIGHT_THRESHOLD_PCT",
            "key1": None,
            "key2": None,
            "value": "15",
            "text_value": None,
        },
    ]


def upgrade() -> None:
    # A new enum value cannot be used in the transaction that adds it.
    with op.get_context().autocommit_block():
        for key in _NEW_TABLE_KEYS:
            op.execute(f"ALTER TYPE reference_data_table_key ADD VALUE IF NOT EXISTS '{key}'")

    bind = op.get_bind()
    entries_table = sa.table(
        'reference_data_entries',
        sa.column('id', sa.UUID()),
        sa.column('version_id', sa.UUID()),
        sa.column('table_key', sa.Enum(name='reference_data_table_key', create_type=False)),
        sa.column('key1', sa.String()),
        sa.column('key2', sa.String()),
        sa.column('value', sa.Numeric(18, 10)),
        sa.column('text_value', sa.String()),
    )
    # Only the currently-active version — uq_reference_data_versions_single_active
    # (added by 9d4f1c7a2e55) guarantees at most one row here.
    active_id = bind.execute(sa.text("SELECT id FROM reference_data_versions WHERE is_active")).scalar()
    if active_id is not None:
        rows = [{"id": uuid.uuid4(), "version_id": active_id, **entry} for entry in _seed_entries()]
        op.bulk_insert(entries_table, rows)
    # No active version (fresh/dev DB) -> nothing to backfill; the app
    # already reports NO_ACTIVE_REFERENCE_DATA until an operator seeds one.


def downgrade() -> None:
    # Postgres cannot drop a value from an enum type, so the 11 new
    # reference_data_table_key values remain defined (unused) after this.
    op.execute(
        "DELETE FROM reference_data_entries WHERE table_key::text IN ("
        "'DNBP_OUTLIER_THRESHOLD_PCT','DNBP_OUTLIER_LOOKBACK_DAYS','ANALYTICS_TRAILING_DAYS_FOR_RATE',"
        "'DELIVERY_ESCALATION_MINUTES','ENTRY_BOUNDS_MAX_HEAD_COUNT','ENTRY_BOUNDS_MAX_PRICE_PER_HEAD',"
        "'ENTRY_BOUNDS_WEIGHT_LOWER_MULTIPLE','ENTRY_BOUNDS_WEIGHT_UPPER_MULTIPLE',"
        "'ENTRY_BOUNDS_FALLBACK_WEIGHT_MIN_KG','ENTRY_BOUNDS_FALLBACK_WEIGHT_MAX_KG',"
        "'BENCHMARK_COMPARE_HIGHLIGHT_THRESHOLD_PCT')"
    )
