"""dataset_version_tables add stats_uri

Revision ID: 0040_dvt_stats_uri
Revises: 1dee8f8c6c32
Create Date: 2026-07-02

质量评估支持多文件(成员级):dataset_version_tables 补 stats_uri 列,
承接每个成员独立的 dj-analyze stats 产物路径(此前 quality.py 靠 hasattr
误判该字段存在,写入被静默丢弃)。只加列,历史成员本无 stats,无需回填。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "0040_dvt_stats_uri"
down_revision: Union[str, None] = "1dee8f8c6c32"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "dataset_version_tables", sa.Column("stats_uri", sa.String(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("dataset_version_tables", "stats_uri")
