"""schedule + incremental: jobs.trigger + ingest_tasks.incremental/watermark

Revision ID: 0025_schedule_incremental
Revises: 0024_ingest_quality
Create Date: 2026-06-26

切片 C(采集调度与增量)数据模型落地:
- jobs 加 1 列:
  * trigger  String NOT NULL server_default 'manual'
    —— 任务触发来源:manual(手工) | cron(定时调度,Task 3 真跑)。
    与 IngestTask.schedule.mode='cron' 配套;存量 job 一律 'manual'。
- ingest_tasks 加 2 列:
  * incremental  JSONB nullable —— 增量采集配置(二选一形,与
    schemas.ingest_task.Incremental 同形):
      库形  {column, type:'timestamp'|'integer'}  按 DB 列水位推进;
      文件形 {by:'mtime'|'name'}                 按 mtime/文件名推进。
  * watermark    JSONB nullable —— 最近一次增量水位快照
    ({value, updatedAt});由运行期写入,空表示尚未增量跑过。

三列:trigger 带 server_default 安全回填存量,两列 JSONB 全 nullable 不影响
任何现有 接入/调度/审核 流程。downgrade 反向 drop。

仅 add column,无数据迁移、无回填风险。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0025_schedule_incremental"
down_revision: str | None = "0024_ingest_quality"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # jobs:触发来源(manual / cron)
    op.add_column(
        "jobs",
        sa.Column(
            "trigger",
            sa.String(),
            nullable=False,
            server_default="manual",
        ),
    )

    # ingest_tasks:增量配置 + 水位
    op.add_column(
        "ingest_tasks",
        sa.Column(
            "incremental",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "ingest_tasks",
        sa.Column(
            "watermark",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("ingest_tasks", "watermark")
    op.drop_column("ingest_tasks", "incremental")
    op.drop_column("jobs", "trigger")
