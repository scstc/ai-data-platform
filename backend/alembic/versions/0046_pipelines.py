"""治理工场:新增 pipelines 表(命名的算子编排) + jobs 加 pipeline_id 回指。

Revision ID: 0046_pipelines
Revises: 0045_dvt_source_snapshot
Create Date: 2026-07-05

背景见治理整改「治理工场」设计:清洗/蒸馏/合成/增强四场景收口为统一工作台,
引入 pipelines 实体承载可复用的算子编排;任务实例仍落既有 jobs 表,经
pipeline_id 弱引用回指来源流水线(手工建任务该列为空)。预置模板(如「标准
文本清洗」)不入库,纯代码常量(见 app/services/pipeline_presets.py)。

**不要在本仓库对 dev 库执行 upgrade**——只对 adp_gov 库单独执行(与 0044/0045 一致),
dev 库 `adp` 不动。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0046_pipelines"
down_revision: str | None = "0045_dvt_source_snapshot"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "pipelines",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("scenario", sa.String(), nullable=False),
        sa.Column("spec", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by", sa.String(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pipelines_scenario", "pipelines", ["scenario"])

    op.add_column("jobs", sa.Column("pipeline_id", sa.String(), nullable=True))
    op.create_index("ix_jobs_pipeline_id", "jobs", ["pipeline_id"])


def downgrade() -> None:
    op.drop_index("ix_jobs_pipeline_id", table_name="jobs")
    op.drop_column("jobs", "pipeline_id")

    op.drop_index("ix_pipelines_scenario", table_name="pipelines")
    op.drop_table("pipelines")
