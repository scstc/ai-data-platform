"""merge job member configs and data lake multisource branches

Revision ID: 1dee8f8c6c32
Revises: 0036_job_member_configs, 0039_ingest_task_lake_id
Create Date: 2026-07-02 10:07:29.269919

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '1dee8f8c6c32'
down_revision: Union[str, None] = ('0036_job_member_configs', '0039_ingest_task_lake_id')
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
