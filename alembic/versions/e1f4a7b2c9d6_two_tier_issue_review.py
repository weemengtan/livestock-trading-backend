"""two-tier issue review

Revision ID: e1f4a7b2c9d6
Revises: c4a9e1d7b2f8
Create Date: 2026-10-08 10:00:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'e1f4a7b2c9d6'
down_revision: Union[str, None] = 'c4a9e1d7b2f8'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('validation_issues', sa.Column('approval_reason_code', sa.String(), nullable=True))
    op.add_column('validation_issues', sa.Column('approval_remark', sa.Text(), nullable=True))
    op.add_column('validation_issues', sa.Column('approval_expires_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('validation_issues', sa.Column('recommendation_decision', sa.String(), nullable=True))
    op.add_column('validation_issues', sa.Column('recommendation_reason_code', sa.String(), nullable=True))
    op.add_column('validation_issues', sa.Column('recommendation_remark', sa.Text(), nullable=True))
    op.add_column('validation_issues', sa.Column('recommended_by', sa.UUID(), nullable=True))
    op.add_column('validation_issues', sa.Column('recommended_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('validation_issues', sa.Column('rejected_by', sa.UUID(), nullable=True))
    op.add_column('validation_issues', sa.Column('rejected_at', sa.DateTime(timezone=True), nullable=True))
    op.add_column('validation_issues', sa.Column('rejection_remark', sa.Text(), nullable=True))
    op.create_foreign_key('fk_validation_issues_recommended_by', 'validation_issues', 'users', ['recommended_by'], ['id'])
    op.create_foreign_key('fk_validation_issues_rejected_by', 'validation_issues', 'users', ['rejected_by'], ['id'])

    op.add_column('order_issue_acknowledgments', sa.Column('reason_code', sa.String(), nullable=True))
    op.add_column('order_issue_acknowledgments', sa.Column('remark', sa.Text(), nullable=True))
    op.add_column('order_issue_acknowledgments', sa.Column('dnbp_model_id', sa.UUID(), nullable=True))


def downgrade() -> None:
    op.drop_column('order_issue_acknowledgments', 'dnbp_model_id')
    op.drop_column('order_issue_acknowledgments', 'remark')
    op.drop_column('order_issue_acknowledgments', 'reason_code')
    op.drop_constraint('fk_validation_issues_rejected_by', 'validation_issues', type_='foreignkey')
    op.drop_constraint('fk_validation_issues_recommended_by', 'validation_issues', type_='foreignkey')
    for col in ('rejection_remark', 'rejected_at', 'rejected_by', 'recommended_at', 'recommended_by',
                'recommendation_remark', 'recommendation_reason_code', 'recommendation_decision',
                'approval_expires_at', 'approval_remark', 'approval_reason_code'):
        op.drop_column('validation_issues', col)
