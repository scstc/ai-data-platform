"""drop datasets.sensitivity_level: 数据集分级下线(由数据权限管理/ACL 取代)

Revision ID: 0029_drop_sensitivity_level
Revises: 0028_route_perms
Create Date: 2026-06-28

背景:数据集「分级」(sensitivity_level:public|internal|confidential)是访问控制
未就绪时的临时敏感度标注。数据权限管理(dataset_acl,迁移 0020)上线后,访问控制
由 ACL 统一承载,分级字段冗余,故彻底删列。

downgrade 重新加回该列(nullable),但不恢复历史值(已随 upgrade 丢弃)。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0029_drop_sensitivity_level"
down_revision: str | None = "0028_route_perms"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_column("datasets", "sensitivity_level")


def downgrade() -> None:
    op.add_column(
        "datasets",
        sa.Column("sensitivity_level", sa.String(), nullable=True),
    )
