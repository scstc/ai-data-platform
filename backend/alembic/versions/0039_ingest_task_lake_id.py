"""ingest_task lake_id: 采集任务新增目标数据湖字段

Revision ID: 0039_ingest_task_lake_id
Revises: 0038_data_lake_multisource
Create Date: 2026-07-01

按数据治理规范(docs/数据治理.md §5),采集任务的目标从"数据集"改为"数据湖":
- 存量任务:dataset_id 保留(未迁移前继续落数据集,零回归)
- 新任务:lake_id 优先,落湖快照,数据集通过'从湖抽取'作业单独产生

本迁移只加字段不改行为——connector 端的路由分派放后续 PR。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0039_ingest_task_lake_id"
down_revision: str | None = "0038_data_lake_multisource"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ingest_tasks",
        sa.Column("lake_id", sa.String(), nullable=True),
    )
    op.create_index(
        "ix_ingest_tasks_lake_id", "ingest_tasks", ["lake_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_ingest_tasks_lake_id", table_name="ingest_tasks")
    op.drop_column("ingest_tasks", "lake_id")
