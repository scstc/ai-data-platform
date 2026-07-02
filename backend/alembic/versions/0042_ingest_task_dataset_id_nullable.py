"""ingest_tasks.dataset_id 放开 NOT NULL: 湖优先后新任务不再绑数据集

Revision ID: 0042_ingest_dsid_nullable
Revises: 0041_review_rules_multitable
Create Date: 2026-07-02

治理改造(采集入湖)落地后,新建采集任务只绑 lake_id,dataset_id 恒空;
但 0004 时代该列建成 NOT NULL(数据集优先流程),导致创建任务 INSERT 500。
ORM 模型(ingest_task.py)一直声明 nullable=True,本迁移让库跟上模型。

downgrade 需先保证无 dataset_id 为空的行(湖优先任务),否则加回 NOT NULL 会失败
——这是刻意的诚实失败,不静默回填假值。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0042_ingest_dsid_nullable"
down_revision: str | None = "0041_review_rules_multitable"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column(
        "ingest_tasks",
        "dataset_id",
        existing_type=sa.String(),
        nullable=True,
    )


def downgrade() -> None:
    op.alter_column(
        "ingest_tasks",
        "dataset_id",
        existing_type=sa.String(),
        nullable=False,
    )
