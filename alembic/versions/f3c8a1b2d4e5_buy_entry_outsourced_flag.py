"""buy_entry_outsourced_flag

Revision ID: f3c8a1b2d4e5
Revises: 78f87e837f78
Create Date: 2026-09-23 00:00:00.000000

Outsourced Buy Log: `is_outsourced` flags a buy_entry as purchased by a
3rd-party buyer at another saleyard on this buyer's behalf (their own
saleyard's price couldn't fulfil the species' DNBP). `outsourced_buyer_name`
is an optional free-text identifier for that 3rd party, for reconciliation
only. Neither column changes scoring, deduplication, or head-count
aggregation — every existing query over buy_entries keeps working unchanged.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f3c8a1b2d4e5'
down_revision: Union[str, None] = '78f87e837f78'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # server_default backfills existing rows as False — every buy entry
    # before this feature existed was, definitionally, not outsourced.
    op.add_column(
        'buy_entries',
        sa.Column('is_outsourced', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column('buy_entries', 'is_outsourced', server_default=None)
    op.add_column(
        'buy_entries',
        sa.Column('outsourced_buyer_name', sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('buy_entries', 'outsourced_buyer_name')
    op.drop_column('buy_entries', 'is_outsourced')
