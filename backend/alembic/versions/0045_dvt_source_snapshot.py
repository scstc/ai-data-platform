"""湖→仓结构化血缘：dataset_version_tables 加 source_snapshot_id

Revision ID: 0045_dvt_source_snapshot
Revises: 0044_lake_objects
Create Date: 2026-07-04

背景见 docs/数据湖文件版本模型整改.md。湖抽取生成数据集原本只有记录级
meta（数据文件内）与自由文本 note 两条弱血缘，DB 模型层查不到"这个成员
来自哪个湖快照"。本迁移在成员行（一个成员 = 一次快照抽取）加
source_snapshot_id 弱引用（无 FK），经 snapshot.object_id/version_no 即可
定位"哪个湖文件的哪一版"，正反向溯源均为 SQL 可查。

存量成员无法可靠回填（note 是自由文本、meta 在 MinIO 数据文件里），列
留空如实表示"血缘未知"，不做猜测性回填。

**不要在本仓库对 dev 库执行 upgrade**——只对 adp_gov 库单独执行（与
0044 一致），dev 库 `adp` 不动。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0045_dvt_source_snapshot"
down_revision: str | None = "0044_lake_objects"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "dataset_version_tables",
        sa.Column("source_snapshot_id", sa.String(), nullable=True),
    )
    op.create_index(
        "ix_dvt_source_snapshot", "dataset_version_tables", ["source_snapshot_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_dvt_source_snapshot", table_name="dataset_version_tables")
    op.drop_column("dataset_version_tables", "source_snapshot_id")
