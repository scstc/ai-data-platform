"""dataset acl: 数据集级共享/成员权限(view/edit/admin × user/role)+ 存量回填

Revision ID: 0020_dataset_acl
Revises: 0019_rbac_system
Create Date: 2026-06-25

设计见 docs/superpowers/plans/2026-06-25-dataset-acl.md。迁移自包含。
存量回填:给每条现存数据集授「普通用户」角色(role-000002)view 级,保现有可见性;
新建数据集不回填(默认私有,仅 owner+超管可见)。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0020_dataset_acl"
down_revision: str | None = "0019_rbac_system"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COMMON_ROLE = "role-000002"


def upgrade() -> None:
    op.create_table(
        "dataset_acl",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("dataset_id", sa.String(), nullable=False),
        sa.Column("subject_type", sa.String(), nullable=False),
        sa.Column("subject_id", sa.String(), nullable=False),
        sa.Column("level", sa.String(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "dataset_id", "subject_type", "subject_id", name="uq_dataset_acl_subject"
        ),
    )
    op.create_index("ix_dataset_acl_dataset_id", "dataset_acl", ["dataset_id"])

    # 存量回填:每条现存数据集授普通用户角色 view(幂等,NOT EXISTS 防重)
    op.execute(
        f"""
        INSERT INTO dataset_acl (id, dataset_id, subject_type, subject_id, level, created_at)
        SELECT 'dac-seed-' || d.id, d.id, 'role', '{_COMMON_ROLE}', 'view', now()
        FROM datasets d
        WHERE NOT EXISTS (
            SELECT 1 FROM dataset_acl a
            WHERE a.dataset_id = d.id
              AND a.subject_type = 'role'
              AND a.subject_id = '{_COMMON_ROLE}'
        )
        """
    )


def downgrade() -> None:
    op.drop_index("ix_dataset_acl_dataset_id", table_name="dataset_acl")
    op.drop_table("dataset_acl")
