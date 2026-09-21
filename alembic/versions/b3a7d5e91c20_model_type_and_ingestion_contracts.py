"""dnbp model type on versions; ingestion contracts as versioned config

Reference-data versions now name which whitelisted DNBP formula they use
(model_type, default FACTOR_AFTER_BUFFER — the one implemented today). The
ingestion contract (required tab name, section markers, required columns)
moves from a code constant into a versioned, audited table; version 1 is
seeded active with the values the parser has been using.

Revision ID: b3a7d5e91c20
Revises: 9d4f1c7a2e55
Create Date: 2026-09-21 14:00:00.000000

"""
import uuid
from datetime import datetime, timezone
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = 'b3a7d5e91c20'
down_revision: Union[str, None] = '9d4f1c7a2e55'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'reference_data_versions',
        sa.Column('model_type', sa.String(), nullable=False, server_default='FACTOR_AFTER_BUFFER'),
    )
    op.create_table(
        'ingestion_contracts',
        sa.Column('version', sa.String(), nullable=False),
        sa.Column('required_sheet_name', sa.String(), nullable=False),
        sa.Column('active_title_tokens', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('section_end_tokens', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('title_scan_rows', sa.Integer(), nullable=False),
        sa.Column('required_columns', postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column('note', sa.String(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=False),
        sa.Column('created_by', sa.UUID(), nullable=True),
        sa.Column('activated_by', sa.UUID(), nullable=True),
        sa.Column('activated_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['created_by'], ['users.id']),
        sa.ForeignKeyConstraint(['activated_by'], ['users.id']),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('version'),
    )
    op.create_index(
        'uq_ingestion_contracts_single_active', 'ingestion_contracts', ['is_active'],
        unique=True, postgresql_where=sa.text('is_active'),
    )

    contracts = sa.table(
        'ingestion_contracts',
        sa.column('id', sa.UUID()), sa.column('version', sa.String()),
        sa.column('required_sheet_name', sa.String()),
        sa.column('active_title_tokens', postgresql.JSONB()), sa.column('section_end_tokens', postgresql.JSONB()),
        sa.column('title_scan_rows', sa.Integer()), sa.column('required_columns', postgresql.JSONB()),
        sa.column('note', sa.String()), sa.column('is_active', sa.Boolean()),
        sa.column('activated_at', sa.DateTime(timezone=True)),
    )
    op.bulk_insert(contracts, [{
        'id': uuid.uuid4(),
        'version': 'profitability-analysis-active-v1',
        'required_sheet_name': 'Profitability Analysis',
        'active_title_tokens': ['active', 'orders'],
        'section_end_tokens': ['loaded', 'orders'],
        'title_scan_rows': 6,
        'required_columns': {
            'contract_no': 'Contract No.', 'species': 'Type',
            'qty_kg': 'Sum of Total QTY', 'avg_price_aud': 'Average of Price AUD',
        },
        'note': 'Initial contract: Active Orders block of the Profitability Analysis tab only.',
        'is_active': True,
        'activated_at': datetime.now(timezone.utc),
    }])


def downgrade() -> None:
    op.drop_index('uq_ingestion_contracts_single_active', table_name='ingestion_contracts')
    op.drop_table('ingestion_contracts')
    op.drop_column('reference_data_versions', 'model_type')
