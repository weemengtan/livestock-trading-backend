"""reference config in postgres: operational constants, saleyard calendar, version identity

Operational constants and the saleyard calendar were read from a seed file
at runtime. They now live in the same versioned, audited reference_data
tables as the DNBP parameters. Every version that already exists is
backfilled once with the three generic operational constants so it remains a
complete, standalone snapshot; the saleyard calendar is business data and is
not seeded here (a version simply has no rows until an operator adds them).

Also: exactly one active version is now enforced by a partial unique index;
a version records who activated it; and workings rows can carry the exact
reference-data version id they were computed under.

Revision ID: 9d4f1c7a2e55
Revises: 7c2e91a4d3b8
Create Date: 2026-09-21 12:00:00.000000

"""
import uuid
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '9d4f1c7a2e55'
down_revision: Union[str, None] = '7c2e91a4d3b8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_NEW_TABLE_KEYS = (
    "BID_CHECK_CLOSE_THRESHOLD_PCT",
    "BUYER_WEIGHT_BAND_TOLERANCE_PCT",
    "STALE_INSTRUCTION_HOURS",
    "SALEYARD_CALENDAR",
)


def _seed_entries() -> list[dict]:
    # Generic operational defaults, applied only to versions that already exist.
    return [
        {"table_key": "BID_CHECK_CLOSE_THRESHOLD_PCT", "key1": None, "key2": None, "value": "5", "text_value": None},
        {"table_key": "BUYER_WEIGHT_BAND_TOLERANCE_PCT", "key1": None, "key2": None, "value": "15", "text_value": None},
        {"table_key": "STALE_INSTRUCTION_HOURS", "key1": None, "key2": None, "value": "24", "text_value": None},
    ]


def upgrade() -> None:
    # A new enum value cannot be used in the transaction that adds it.
    with op.get_context().autocommit_block():
        for key in _NEW_TABLE_KEYS:
            op.execute(f"ALTER TYPE reference_data_table_key ADD VALUE IF NOT EXISTS '{key}'")

    op.add_column('reference_data_entries', sa.Column('text_value', sa.String(), nullable=True))
    op.add_column('reference_data_versions', sa.Column('activated_by', sa.UUID(), nullable=True))
    op.create_foreign_key(
        'reference_data_versions_activated_by_fkey', 'reference_data_versions', 'users', ['activated_by'], ['id']
    )
    op.add_column('order_workings', sa.Column('ref_data_version_id', sa.UUID(), nullable=True))
    op.create_foreign_key(
        'order_workings_ref_data_version_id_fkey', 'order_workings', 'reference_data_versions',
        ['ref_data_version_id'], ['id'],
    )
    op.create_index(op.f('ix_order_workings_ref_data_version_id'), 'order_workings', ['ref_data_version_id'], unique=False)
    op.create_index(
        'uq_reference_data_versions_single_active', 'reference_data_versions', ['is_active'],
        unique=True, postgresql_where=sa.text('is_active'),
    )

    # One-time backfill: every existing version becomes a complete snapshot.
    bind = op.get_bind()
    entries_table = sa.table(
        'reference_data_entries',
        sa.column('id', sa.UUID()), sa.column('version_id', sa.UUID()),
        sa.column('table_key', sa.Enum(name='reference_data_table_key', create_type=False)),
        sa.column('key1', sa.String()), sa.column('key2', sa.String()),
        sa.column('value', sa.Numeric(18, 10)), sa.column('text_value', sa.String()),
    )
    version_ids = [row[0] for row in bind.execute(sa.text("SELECT id FROM reference_data_versions")).fetchall()]
    seed = _seed_entries()
    rows = [{"id": uuid.uuid4(), "version_id": vid, **entry} for vid in version_ids for entry in seed]
    if rows:
        op.bulk_insert(entries_table, rows)


def downgrade() -> None:
    # Removes the data and structure added above. Postgres cannot drop a
    # value from an enum type, so the four new reference_data_table_key
    # values remain (unused).
    op.execute(
        "DELETE FROM reference_data_entries WHERE table_key::text IN "
        "('BID_CHECK_CLOSE_THRESHOLD_PCT','BUYER_WEIGHT_BAND_TOLERANCE_PCT','STALE_INSTRUCTION_HOURS','SALEYARD_CALENDAR')"
    )
    op.drop_index('uq_reference_data_versions_single_active', table_name='reference_data_versions')
    op.drop_index(op.f('ix_order_workings_ref_data_version_id'), table_name='order_workings')
    op.drop_constraint('order_workings_ref_data_version_id_fkey', 'order_workings', type_='foreignkey')
    op.drop_column('order_workings', 'ref_data_version_id')
    op.drop_constraint('reference_data_versions_activated_by_fkey', 'reference_data_versions', type_='foreignkey')
    op.drop_column('reference_data_versions', 'activated_by')
    op.drop_column('reference_data_entries', 'text_value')
