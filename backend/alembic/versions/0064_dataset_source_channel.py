"""dataset source channel: 数据集来源渠道(湖→仓血缘)

Revision ID: 0064_dataset_source_channel
Revises: 0063_operator_star
Create Date: 2026-07-08

从数据湖抽取到数据集时,把湖文件的来源渠道(upload_channel)带过去:
- dataset_version_tables 加 1 列:
  * source_upload_channel VARCHAR nullable —— 该成员抽取自哪个上传渠道
    (与既有 source_snapshot_id 同批写入,非湖抽取来源为空)。
- dataset_versions 加 1 列:
  * source_channels JSONB nullable —— 该版本各表成员 source_upload_channel 的
    去重列表,每次 add_table_member 追加成员时并集更新(与只写一次的 modalities
    不同)。全部成员都非湖抽取来源时为空。

仅 add column(nullable),无数据迁移、无回填风险。downgrade 反向 drop。

**不要在本仓库对 dev 库执行 upgrade**——只对 adp_gov 库单独执行(与 0044~0063 一致)。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0064_dataset_source_channel"
down_revision: str | None = "0063_operator_star"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "dataset_version_tables",
        sa.Column("source_upload_channel", sa.String(), nullable=True),
    )
    op.add_column(
        "dataset_versions",
        sa.Column(
            "source_channels",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("dataset_versions", "source_channels")
    op.drop_column("dataset_version_tables", "source_upload_channel")
