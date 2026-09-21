"""audit log: append-only at the database, tamper-evident hash chain

Adds a total-order sequence, the request correlation id, and the hash-chain
columns. A trigger rejects UPDATE, DELETE and TRUNCATE on audit_log for
everyone, including the table owner; the only sanctioned bypass is the
owner explicitly disabling the truncate trigger inside a transaction (the
dev-only wipe script does this and refuses to run outside ENVIRONMENT=dev).
The production application role is additionally denied those privileges —
see scripts/create_app_role.sql. Existing rows keep NULL hashes ("legacy,
unchained"); the chain begins with the first row written after this.

Revision ID: c8e2f4a6b1d3
Revises: b3a7d5e91c20
Create Date: 2026-09-21 16:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'c8e2f4a6b1d3'
down_revision: Union[str, None] = 'b3a7d5e91c20'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("ALTER TABLE audit_log ADD COLUMN seq BIGINT GENERATED ALWAYS AS IDENTITY")
    op.create_index('ix_audit_log_seq', 'audit_log', ['seq'], unique=True)
    op.add_column('audit_log', sa.Column('correlation_id', sa.String(), nullable=True))
    op.add_column('audit_log', sa.Column('prev_hash', sa.String(), nullable=True))
    op.add_column('audit_log', sa.Column('row_hash', sa.String(), nullable=True))

    op.execute(
        """
        CREATE FUNCTION audit_log_reject_change() RETURNS trigger AS $$
        BEGIN
            RAISE EXCEPTION 'audit_log is append-only: % is not permitted', TG_OP
                USING ERRCODE = 'restrict_violation';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        "CREATE TRIGGER audit_log_no_update_delete BEFORE UPDATE OR DELETE ON audit_log "
        "FOR EACH ROW EXECUTE FUNCTION audit_log_reject_change()"
    )
    op.execute(
        "CREATE TRIGGER audit_log_no_truncate BEFORE TRUNCATE ON audit_log "
        "FOR EACH STATEMENT EXECUTE FUNCTION audit_log_reject_change()"
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_truncate ON audit_log")
    op.execute("DROP TRIGGER IF EXISTS audit_log_no_update_delete ON audit_log")
    op.execute("DROP FUNCTION IF EXISTS audit_log_reject_change()")
    op.drop_column('audit_log', 'row_hash')
    op.drop_column('audit_log', 'prev_hash')
    op.drop_column('audit_log', 'correlation_id')
    op.drop_index('ix_audit_log_seq', table_name='audit_log')
    op.drop_column('audit_log', 'seq')
