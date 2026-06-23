"""categories: 多级树 parent_id(邻接表) + 同级 (parent_id,name) 唯一

Revision ID: 0017_category_parent_id
Revises: 0016_source_kind_format
Create Date: 2026-06-22

分类从扁平单层升级为多级树(需求:分级分类管理按层级):
- 加 parent_id(String,nullable,根为 null),沿用无-FK 约定(纯引用,
  完整性环检测/后代判定走应用层 categories.py)。
- name 唯一性从全局放宽为同级 (parent_id, name) 唯一:
  drop uq_categories_name,add uq_categories_parent_name。

PG 复合 unique 下 NULL 互不冲突 → 两个根(parent_id=NULL)同名不被 DB 拦;
根的同级查重由应用层(IS NOT DISTINCT FROM)兜底。存量分类 parent_id 全 null
(都成根),行为不变。downgrade 还原全局 name 唯一、删 parent_id 列。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0017_category_parent_id"
down_revision: str | None = "0016_source_kind_format"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "categories", sa.Column("parent_id", sa.String(), nullable=True)
    )
    # 全局 name 唯一 → 同级 (parent_id, name) 唯一
    op.drop_constraint("uq_categories_name", "categories", type_="unique")
    op.create_unique_constraint(
        "uq_categories_parent_name", "categories", ["parent_id", "name"]
    )


def downgrade() -> None:
    op.drop_constraint("uq_categories_parent_name", "categories", type_="unique")
    op.create_unique_constraint("uq_categories_name", "categories", ["name"])
    op.drop_column("categories", "parent_id")
