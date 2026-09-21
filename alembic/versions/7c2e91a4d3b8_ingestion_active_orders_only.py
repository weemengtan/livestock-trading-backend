"""ingestion: active orders only — drop lookup-tab tables, drift and stored file key

The upload now reads only the Active Orders block of the `Profitability
Analysis` tab. The abattoir lookup-tab tables (and the drift records derived
from comparing them between snapshots) no longer exist, and the uploaded
workbook is no longer stored, so its object-storage key has nothing to point
at. `source_sha256` (the file's fingerprint) is kept.

Revision ID: 7c2e91a4d3b8
Revises: 5aa63ef7b94b
Create Date: 2026-09-21 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '7c2e91a4d3b8'
down_revision: Union[str, None] = '5aa63ef7b94b'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_index(op.f('ix_reference_data_drift_snapshot_id'), table_name='reference_data_drift')
    op.drop_table('reference_data_drift')
    op.drop_column('order_snapshots', 'abattoir_reference_tables')
    op.drop_column('order_snapshots', 'object_storage_key')


def downgrade() -> None:
    # Structure only: data in the dropped columns/table is not recoverable.
    op.add_column('order_snapshots', sa.Column('object_storage_key', sa.String(), nullable=False, server_default=''))
    op.alter_column('order_snapshots', 'object_storage_key', server_default=None)
    op.add_column(
        'order_snapshots',
        sa.Column('abattoir_reference_tables', postgresql.JSONB(astext_type=sa.Text()), nullable=False, server_default='{}'),
    )
    op.alter_column('order_snapshots', 'abattoir_reference_tables', server_default=None)
    op.create_table('reference_data_drift',
    sa.Column('snapshot_id', sa.UUID(), nullable=False),
    sa.Column('table_key', sa.String(), nullable=False),
    sa.Column('key1', sa.String(), nullable=False),
    sa.Column('old_value', sa.Numeric(precision=18, scale=10), nullable=True),
    sa.Column('new_value', sa.Numeric(precision=18, scale=10), nullable=True),
    sa.Column('detected_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('acknowledged_by', sa.UUID(), nullable=True),
    sa.Column('acknowledged_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.ForeignKeyConstraint(['acknowledged_by'], ['users.id'], ),
    sa.ForeignKeyConstraint(['snapshot_id'], ['order_snapshots.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_index(op.f('ix_reference_data_drift_snapshot_id'), 'reference_data_drift', ['snapshot_id'], unique=False)
