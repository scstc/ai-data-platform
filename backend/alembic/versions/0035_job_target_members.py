"""job target members support

Revision ID: 0035_job_target_members
Revises: 0034_dataset_first_multitable
Create Date: 2026-07-02 10:00:00.000000

支持任务指定处理特定表成员，而非强制处理整个版本所有成员。
"""
from typing import Sequence, Union
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0035_job_target_members"
down_revision: Union[str, None] = "0034_dataset_first_multitable"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

def upgrade() -> None:
    op.add_column('jobs', sa.Column('target_members', postgresql.JSONB, nullable=True))

def downgrade() -> None:
    op.drop_column('jobs', 'target_members')
