"""任务队列可靠性字段 + 推送幂等表

Revision ID: 0069_job_queue_push_idem
Revises: 0068_llm_system_models
Create Date: 2026-07-11

任务队列整改 P0 地基(供后续 runner 并行改造消费):
- jobs 加 attempts/max_attempts(重试计数与上限)、heartbeat_at/claimed_by
  (worker 认领与存活探测)、queued_at(入队时间,区分 pending 等待时长)、
  depends_on_job_id(任务依赖,预留未消费)、warnings(非致命告警列表)。
- 新建 push_idempotency 表:API 推送入站按 key 去重,记录关联的采集任务与
  (若已落版本)对应的数据集版本,过期后允许复用同一 key。

`push_idempotency.task_id` / `dataset_version_id` 概念上是 UUID,但本仓库
主键一律是 "job-"/"task-"/"dsv-" + 6 位 hex 的字符串(非真实 Postgres UUID,
见 jobs.id / ingest_tasks.id / dataset_versions.id),故落库为 String 而非
原生 UUID 类型,与既有外键无关联但格式对齐的引用列保持一致——否则真实
UUID 列会在写入这些字符串 ID 时报类型错误。`depends_on_job_id` 同理。

**只对整改库 adp_flow 执行 upgrade**。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "0069_job_queue_push_idem"
down_revision: str | None = "0068_llm_system_models"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column(
            "attempts", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.add_column(
        "jobs",
        sa.Column(
            "max_attempts", sa.Integer(), nullable=False, server_default="3"
        ),
    )
    op.add_column(
        "jobs", sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True)
    )
    op.add_column(
        "jobs", sa.Column("claimed_by", sa.String(length=64), nullable=True)
    )
    op.add_column(
        "jobs", sa.Column("queued_at", sa.DateTime(timezone=True), nullable=True)
    )
    # 预留:任务依赖(如 B 需等 A 完成才可入队),本迁移仅建列不消费
    op.add_column(
        "jobs", sa.Column("depends_on_job_id", sa.String(), nullable=True)
    )
    op.add_column("jobs", sa.Column("warnings", JSONB(), nullable=True))

    op.create_table(
        "push_idempotency",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column("task_id", sa.String(), nullable=False),
        sa.Column("dataset_version_id", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )


def downgrade() -> None:
    op.drop_table("push_idempotency")
    op.drop_column("jobs", "warnings")
    op.drop_column("jobs", "depends_on_job_id")
    op.drop_column("jobs", "queued_at")
    op.drop_column("jobs", "claimed_by")
    op.drop_column("jobs", "heartbeat_at")
    op.drop_column("jobs", "max_attempts")
    op.drop_column("jobs", "attempts")
