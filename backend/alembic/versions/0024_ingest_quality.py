"""ingest quality: 采集质量字段 + 任务级 quality_policy

Revision ID: 0024_ingest_quality
Revises: 0023_hide_permission_menu
Create Date: 2026-06-26

切片 B(采集运行可观测与治理)数据模型落地:
- dataset_versions 加 3 列:
  * quality_stats   JSONB nullable —— 采集质量统计(行数、逐列 null 率等)。
  * schema_snapshot JSONB nullable —— 采集时的表结构快照(列名/类型),用于
    后续 schema drift 比较。
  * quality_verdict  String NOT NULL server_default 'skipped'
    —— 采集质量门结论:skipped(未配置策略) | passed | failed。
- ingest_tasks 加 1 列:
  * quality_policy  JSONB nullable —— 任务级质量策略({maxNullRate, blockOnSchemaDrift}),
    与 schemas.ingest_task.QualityPolicy 同形。

两列 JSONB 全 nullable、String 列带 server_default,存量版本/任务安全回填,
不影响任何现有 接入/加工/质量/审核 流程。downgrade 反向 drop。

仅 add column,无数据迁移、无回填风险。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0024_ingest_quality"
down_revision: str | None = "0023_hide_permission_menu"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # dataset_versions:质量统计 / schema 快照 / 质量门结论
    op.add_column(
        "dataset_versions",
        sa.Column(
            "quality_stats",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "dataset_versions",
        sa.Column(
            "schema_snapshot",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "dataset_versions",
        sa.Column(
            "quality_verdict",
            sa.String(),
            nullable=False,
            server_default="skipped",
        ),
    )

    # ingest_tasks:任务级质量策略
    op.add_column(
        "ingest_tasks",
        sa.Column(
            "quality_policy",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("ingest_tasks", "quality_policy")
    op.drop_column("dataset_versions", "quality_verdict")
    op.drop_column("dataset_versions", "schema_snapshot")
    op.drop_column("dataset_versions", "quality_stats")
