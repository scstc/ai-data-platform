"""add eval_results table and jobs.eval_report (治理整改 G4/G5)

Revision ID: 0032_add_eval_judge
Revises: 0031_add_train_type_metadata
Create Date: 2026-06-30 00:32:00.000000

评估裁判员:新增 eval_results 表(逐条打分)+ jobs.eval_report 列(裁判汇总)。
全列 nullable / 有默认,可安全回退。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = '0032_add_eval_judge'
down_revision: Union[str, None] = '0031_add_train_type_metadata'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'eval_results',
        sa.Column('id', sa.String(), primary_key=True),
        sa.Column('job_id', sa.String(), nullable=False),
        sa.Column('version_id', sa.String(), nullable=False),
        sa.Column('row_index', sa.Integer(), nullable=False),
        sa.Column('prompt', sa.Text(), nullable=False),
        sa.Column('reference', sa.Text(), nullable=False),
        sa.Column('completion', sa.Text(), nullable=False),
        sa.Column('score', sa.Integer(), nullable=True),
        sa.Column('verdict', sa.String(), nullable=False),
        sa.Column('category', sa.String(), nullable=True),
        sa.Column('reason', sa.Text(), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index('ix_eval_results_job_id', 'eval_results', ['job_id'])
    op.create_index('ix_eval_results_created_at', 'eval_results', ['created_at'])
    op.add_column(
        'jobs',
        sa.Column('eval_report', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('jobs', 'eval_report')
    op.drop_index('ix_eval_results_created_at', table_name='eval_results')
    op.drop_index('ix_eval_results_job_id', table_name='eval_results')
    op.drop_table('eval_results')
