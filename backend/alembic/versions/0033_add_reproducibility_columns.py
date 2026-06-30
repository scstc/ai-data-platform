"""add reproducibility columns to jobs (治理整改 G18)

Revision ID: 0033_add_reproducibility_columns
Revises: 0032_add_eval_judge
Create Date: 2026-06-30 00:33:00.000000

可复现凭证(规范 Q9):jobs 表追加 dj_version / image_tag / executor_type。
全列 nullable,无 server_default,可安全回退。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0033_add_reproducibility_columns'
down_revision: Union[str, None] = '0032_add_eval_judge'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column('jobs', sa.Column('dj_version', sa.String(), nullable=True))
    op.add_column('jobs', sa.Column('image_tag', sa.String(), nullable=True))
    op.add_column('jobs', sa.Column('executor_type', sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column('jobs', 'executor_type')
    op.drop_column('jobs', 'image_tag')
    op.drop_column('jobs', 'dj_version')
