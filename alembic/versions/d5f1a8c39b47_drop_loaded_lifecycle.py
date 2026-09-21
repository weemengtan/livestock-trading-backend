"""drop the LOADED lifecycle: every order line is an Active Order

The upload reads only the Active Orders block, so an order line no longer
has a lifecycle. Removes the `lifecycle` column, its index and enum type,
and the trigger that only allowed workings for ACTIVE lines (now vacuous).

Revision ID: d5f1a8c39b47
Revises: c8e2f4a6b1d3
Create Date: 2026-09-21 18:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'd5f1a8c39b47'
down_revision: Union[str, None] = 'c8e2f4a6b1d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS order_workings_active_only ON order_workings")
    op.execute("DROP FUNCTION IF EXISTS forbid_workings_for_loaded_line()")
    op.drop_index(op.f('ix_order_lines_lifecycle'), table_name='order_lines')
    op.drop_column('order_lines', 'lifecycle')
    sa.Enum(name='lifecycle').drop(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    lifecycle = sa.Enum('ACTIVE', 'LOADED', name='lifecycle')
    lifecycle.create(op.get_bind(), checkfirst=True)
    op.add_column('order_lines', sa.Column('lifecycle', lifecycle, nullable=False, server_default='ACTIVE'))
    op.alter_column('order_lines', 'lifecycle', server_default=None)
    op.create_index(op.f('ix_order_lines_lifecycle'), 'order_lines', ['lifecycle'], unique=False)
    op.execute(
        """
        CREATE FUNCTION forbid_workings_for_loaded_line() RETURNS trigger AS $$
        DECLARE
            line_lifecycle lifecycle;
        BEGIN
            SELECT lifecycle INTO line_lifecycle FROM order_lines WHERE id = NEW.order_line_id;
            IF line_lifecycle IS DISTINCT FROM 'ACTIVE' THEN
                RAISE EXCEPTION 'order_workings may only be created for an ACTIVE order_line (got %)', line_lifecycle;
            END IF;
            RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER order_workings_active_only
            BEFORE INSERT OR UPDATE ON order_workings
            FOR EACH ROW EXECUTE FUNCTION forbid_workings_for_loaded_line()
        """
    )
