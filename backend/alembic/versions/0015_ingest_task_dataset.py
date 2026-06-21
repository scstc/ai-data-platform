"""ingest_tasks.dataset_id: 采集任务「生成 CSV 数据集」绑定的数据集

Revision ID: 0015_ingest_task_dataset
Revises: 0014_llm_models
Create Date: 2026-06-21

采集任务可「生成数据集」:库数据 → CSV → 平台 MinIO uploads/<dataset_id>/v<n>/。
首次生成建数据集,后续生成在同一数据集追加新版本(不同版本不同文件夹)。
新增可空列 dataset_id(无 FK,沿用本仓库弱关联约定)。

downgrade:删列。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0015_ingest_task_dataset"
down_revision: str | None = "0014_llm_models"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "ingest_tasks",
        sa.Column("dataset_id", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("ingest_tasks", "dataset_id")
