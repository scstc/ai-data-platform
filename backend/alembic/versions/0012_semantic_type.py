"""datasets/dataset_versions: 语义类型 semantic_type(数据接入重构 #1/#2/#8)

Revision ID: 0012_semantic_type
Revises: 0011_job_spec
Create Date: 2026-06-17

新增正交语义维度 semantic_type(10 类 LLM 语义,与 data_type 功能键正交),
分别落在数据集级(datasets)与版本级快照(dataset_versions)。设计见 docs/plan/14。

仅 add column,全部 nullable,无回填、无 CHECK、无 FK,存量行为空,旧查询零影响。
枚举在 Pydantic 层强制,DB 列保持 free-string(与 scan_verdict/origin 一致)。

downgrade:删两列。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0012_semantic_type"
down_revision: str | None = "0011_job_spec"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "datasets",
        sa.Column("semantic_type", sa.String(), nullable=True),
    )
    op.add_column(
        "dataset_versions",
        sa.Column("semantic_type", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("dataset_versions", "semantic_type")
    op.drop_column("datasets", "semantic_type")
