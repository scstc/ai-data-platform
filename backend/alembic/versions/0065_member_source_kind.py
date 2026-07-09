"""member source kind: 成员来源分类 + 版本级湖快照溯源集合

Revision ID: 0065_member_source_kind
Revises: 0064_dataset_source_channel
Create Date: 2026-07-09

数据集可溯源可复现整改(P1-①):
- dataset_version_tables 加 1 列:
  * source_kind VARCHAR nullable —— 该成员的来源分类(upload/db_ingest/lake 等,
    由调用方显式传,不做内部推断),存量行为空。
- dataset_versions 加 1 列:
  * source_snapshot_ids JSONB nullable —— 该版本(媒体 manifest 整版本形态)
    抽取自哪些湖快照的并集,非湖抽取/表成员形态版本为空。

仅 add column(nullable),无数据迁移、无回填风险。downgrade 反向 drop。

**只对整改库 adp_trace 执行 upgrade**。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0065_member_source_kind"
down_revision: str | None = "0064_dataset_source_channel"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "dataset_version_tables",
        sa.Column("source_kind", sa.String(), nullable=True),
    )
    op.add_column(
        "dataset_versions",
        sa.Column(
            "source_snapshot_ids",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("dataset_versions", "source_snapshot_ids")
    op.drop_column("dataset_version_tables", "source_kind")
