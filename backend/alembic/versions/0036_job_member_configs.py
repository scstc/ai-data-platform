"""job member configs support

Revision ID: 0036_job_member_configs
Revises: 0035_job_target_members
Create Date: 2026-07-02 12:00:00.000000

支持为每个成员配置独立的算子流水线。
member_configs: [{memberName, operators: [...]}]
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = "0036_job_member_configs"
down_revision: Union[str, None] = "0035_job_target_members"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("jobs", sa.Column("member_configs", postgresql.JSONB, nullable=True))


def downgrade() -> None:
    op.drop_column("jobs", "member_configs")
