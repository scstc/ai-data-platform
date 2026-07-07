"""data lake acl: 数据湖级共享/成员权限(view/edit/admin × user/all)+ 存量回填

Revision ID: 0057_data_lake_acl
Revises: 0056_backfill_memberless_dvt
Create Date: 2026-07-07

镜像 0020_dataset_acl 模式(角色授权已取消,主体只有 user/all)。
存量回填:给每条现存数据湖授「组织内所有人」view 级,保现有可见性
(改造前湖列表对所有登录用户开放);新建数据湖不回填(默认私有)。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0057_data_lake_acl"
down_revision: str | None = "0056_backfill_memberless_dvt"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "data_lake_acl",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("lake_id", sa.String(), nullable=False),
        sa.Column("subject_type", sa.String(), nullable=False),
        sa.Column("subject_id", sa.String(), nullable=False),
        sa.Column("level", sa.String(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "lake_id", "subject_type", "subject_id", name="uq_data_lake_acl_subject"
        ),
    )
    op.create_index("ix_data_lake_acl_lake_id", "data_lake_acl", ["lake_id"])

    # 存量回填:每条现存数据湖授组织内所有人 view(幂等,NOT EXISTS 防重)
    op.execute(
        """
        INSERT INTO data_lake_acl
            (id, lake_id, subject_type, subject_id, level, created_at)
        SELECT 'lac-seed-' || l.id, l.id, 'all', '*', 'view', now()
        FROM data_lakes l
        WHERE NOT EXISTS (
            SELECT 1 FROM data_lake_acl a
            WHERE a.lake_id = l.id AND a.subject_type = 'all'
        )
        """
    )


def downgrade() -> None:
    op.drop_index("ix_data_lake_acl_lake_id", table_name="data_lake_acl")
    op.drop_table("data_lake_acl")
