"""tags + dataset_tags: 数据集多对多标签

Revision ID: 0018_dataset_tags
Revises: 0017_category_parent_id
Create Date: 2026-06-22

数据集标签功能:一个数据集多个标签、一个标签多个数据集。
- tags:全局标签池(name 唯一;自由输入时由后端 find-or-create)。
- dataset_tags:关联表(复合 PK dataset_id+tag_id),沿用无-FK 约定(纯 String 引用)。

颜色不落库——前端按 tag.name 做 deterministic 哈希到预设色板,零管理。
downgrade:删两表。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0018_dataset_tags"
down_revision: str | None = "0017_category_parent_id"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "tags",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_tags_name"),
    )
    op.create_table(
        "dataset_tags",
        sa.Column("dataset_id", sa.String(), nullable=False),
        sa.Column("tag_id", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("dataset_id", "tag_id"),
    )


def downgrade() -> None:
    op.drop_table("dataset_tags")
    op.drop_table("tags")
