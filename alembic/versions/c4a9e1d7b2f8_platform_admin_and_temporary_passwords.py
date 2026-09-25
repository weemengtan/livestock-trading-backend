"""platform admin role and temporary-password flag

Adds the PLATFORM_ADMIN value to the `role` enum and users.must_change_password
(set by an admin-issued temporary password, cleared when the user changes it).

Revision ID: c4a9e1d7b2f8
Revises: b9e2c4d7a1f3
Create Date: 2026-09-25 14:00:00.000000

"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = 'c4a9e1d7b2f8'
down_revision: Union[str, None] = 'b9e2c4d7a1f3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # A new enum value cannot be used in the same transaction that adds it, and
    # older Postgres refuses ADD VALUE inside a transaction block at all, so it
    # runs in its own autocommit block.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE role ADD VALUE IF NOT EXISTS 'PLATFORM_ADMIN'")
    op.add_column(
        'users',
        sa.Column('must_change_password', sa.Boolean(), server_default=sa.false(), nullable=False),
    )


def downgrade() -> None:
    op.drop_column('users', 'must_change_password')
    # Postgres cannot drop a single enum value; PLATFORM_ADMIN stays in the
    # `role` type after a downgrade (harmless while no row uses it).
