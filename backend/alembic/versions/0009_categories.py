"""categories: 受控分类库 + 三实体 category_id,收口 datasets.business_category

Revision ID: 0009_categories
Revises: 0008_hosted_s3
Create Date: 2026-06-15

分类管理(#15,设计见 docs/plan/09-分类管理设计.md):
- 建 categories 表(扁平、name 唯一)。
- datasets / datasources / ingest_tasks 各加 category_id(String,nullable,无 FK)。
- 数据集分类收口:从 datasets.business_category 的非空 distinct 值播种 categories
  (生成 cat- id),按名字回填 datasets.category_id,再删 business_category 列。
- downgrade:加回 business_category(从 category_id 反查名回填)、删三个
  category_id、删 categories 表。

仅加表 + 加列 + 数据迁移 + 删一列;播种保证旧自由填值不丢(设计 §5)。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009_categories"
down_revision: str | None = "0008_hosted_s3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "categories",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("note", sa.String(), nullable=True),
        sa.Column("creator", sa.String(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name", name="uq_categories_name"),
    )

    op.add_column(
        "datasets", sa.Column("category_id", sa.String(), nullable=True)
    )
    op.add_column(
        "datasources", sa.Column("category_id", sa.String(), nullable=True)
    )
    op.add_column(
        "ingest_tasks", sa.Column("category_id", sa.String(), nullable=True)
    )

    # 播种:把数据集旧的自由填 business_category 收口为受控分类项(creator=admin)。
    op.execute(
        """
        INSERT INTO categories (id, name, creator)
        SELECT 'cat-' || substr(md5(random()::text), 1, 6), bc, 'admin'
        FROM (
            SELECT DISTINCT business_category AS bc
            FROM datasets
            WHERE business_category IS NOT NULL AND business_category <> ''
        ) AS s
        """
    )
    # 回填:按名字把 business_category 映射到对应 categories.id。
    op.execute(
        """
        UPDATE datasets d
        SET category_id = c.id
        FROM categories c
        WHERE d.business_category = c.name
        """
    )
    op.drop_column("datasets", "business_category")


def downgrade() -> None:
    op.add_column(
        "datasets", sa.Column("business_category", sa.String(), nullable=True)
    )
    # 反查:从 category_id 把分类名回填到 business_category。
    op.execute(
        """
        UPDATE datasets d
        SET business_category = c.name
        FROM categories c
        WHERE d.category_id = c.id
        """
    )
    op.drop_column("ingest_tasks", "category_id")
    op.drop_column("datasources", "category_id")
    op.drop_column("datasets", "category_id")
    op.drop_table("categories")
