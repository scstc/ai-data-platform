"""dataset_version_tables 加 storage_uri 索引(共享引用计数查询)。

Revision ID: 0049_dvt_storage_uri_index
Revises: 0048_hide_scenario_menus
Create Date: 2026-07-05

零拷贝结转(engine.carry_over_members)让多个版本的成员行共享同一 storage_uri;
删成员(datasets.delete_version_members)前按 storage_uri 查共享引用,有共享
只删行不删对象。该查询走此索引。

**不要在本仓库对 dev 库执行 upgrade**——只对 adp_gov 库单独执行(与 0044~0048 一致),
dev 库 `adp` 不动。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0049_dvt_storage_uri_index"
down_revision: str | None = "0048_hide_scenario_menus"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_dvt_storage_uri", "dataset_version_tables", ["storage_uri"]
    )


def downgrade() -> None:
    op.drop_index("ix_dvt_storage_uri", table_name="dataset_version_tables")
