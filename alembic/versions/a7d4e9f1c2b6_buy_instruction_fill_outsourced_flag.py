"""buy_instruction_fill_outsourced_flag

Revision ID: a7d4e9f1c2b6
Revises: f3c8a1b2d4e5
Create Date: 2026-09-23 00:00:00.000000

Mirrors f3c8a1b2d4e5's buy_entries columns, on buy_instruction_line_fills:
`is_outsourced` flags a manual fill as attributable to a 3rd-party buyer's
purchase on this buyer's behalf; `outsourced_buyer_name` is an optional
free-text identifier for that 3rd party. Bing sets both by hand when adding
the fill — buy_instruction_line_fills has no reference to buy_entries (see
models/buy_instruction.py's BuyInstructionLineFill docstring), so this can't
be derived from a buy_entries row and is entered the same way label/kg_amount
already are. Neither column changes balance derivation or any existing
reconciliation query.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'a7d4e9f1c2b6'
down_revision: Union[str, None] = 'f3c8a1b2d4e5'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # server_default backfills existing fills as False — every fill entered
    # before this feature existed was, definitionally, not flagged outsourced.
    op.add_column(
        'buy_instruction_line_fills',
        sa.Column('is_outsourced', sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.alter_column('buy_instruction_line_fills', 'is_outsourced', server_default=None)
    op.add_column(
        'buy_instruction_line_fills',
        sa.Column('outsourced_buyer_name', sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('buy_instruction_line_fills', 'outsourced_buyer_name')
    op.drop_column('buy_instruction_line_fills', 'is_outsourced')
