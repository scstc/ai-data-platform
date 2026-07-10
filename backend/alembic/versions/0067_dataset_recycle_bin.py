"""数据集过期软删除 + 回收站菜单

Revision ID: 0067_dataset_recycle_bin
Revises: 0066_audit_log_detail
Create Date: 2026-07-10

数据集过期治理(#19 生命周期收口):
- datasets 加 deleted_at / deleted_reason —— 过期扫描打删除标记,普通接口
  一律不可见;恢复(清标+续期)仅超管经回收站操作。
- jobs / ingest_tasks 加 deleted_at / deleted_by_dataset_id —— 级联标记
  (输入或产出涉及该数据集即隐藏),归因列记录"因哪个数据集被标记",
  恢复时只解除因它标记的任务(共享任务不误恢复)。
- 菜单:系统管理(menu-000001)下新增 C「数据集回收站」(/system/recycle-bin)及
  查询/恢复两个 F 按钮;仅授超管 role-000001,其余角色按需勾选。

列全部 nullable、无回填,downgrade 反向 drop。

**只对整改库 adp_trace 执行 upgrade**。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0067_dataset_recycle_bin"
down_revision: str | None = "0066_audit_log_detail"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_MENUS_TABLE = sa.table(
    "menus",
    sa.column("id", sa.String),
    sa.column("parent_id", sa.String),
    sa.column("name", sa.String),
    sa.column("menu_type", sa.String),
    sa.column("path", sa.String),
    sa.column("component", sa.String),
    sa.column("perms", sa.String),
    sa.column("icon", sa.String),
    sa.column("sort", sa.Integer),
    sa.column("visible", sa.String),
    sa.column("status", sa.String),
)

_C_ID = "menu-000060"
_MENU_ROWS = [
    {
        "id": _C_ID,
        "parent_id": "menu-000001",
        "name": "数据集回收站",
        "menu_type": "C",
        "path": "/system/recycle-bin",
        "component": "system/recycle-bin",
        "perms": "system:recycle:list",
        "icon": "delete",
        "sort": 6,
        "visible": "0",
        "status": "0",
    },
    *[
        {
            "id": f"{_C_ID}{j}",
            "parent_id": _C_ID,
            "name": f"数据集回收站{label}",
            "menu_type": "F",
            "path": None,
            "component": None,
            "perms": f"system:recycle:{action}",
            "icon": None,
            "sort": j,
            "visible": "0",
            "status": "0",
        }
        for j, (label, action) in enumerate(
            [("查询", "list"), ("恢复", "restore")], start=1
        )
    ],
]


def upgrade() -> None:
    op.add_column(
        "datasets", sa.Column("deleted_at", sa.DateTime(), nullable=True)
    )
    op.add_column(
        "datasets", sa.Column("deleted_reason", sa.String(), nullable=True)
    )
    for tbl in ("jobs", "ingest_tasks"):
        op.add_column(tbl, sa.Column("deleted_at", sa.DateTime(), nullable=True))
        op.add_column(
            tbl, sa.Column("deleted_by_dataset_id", sa.String(), nullable=True)
        )

    op.bulk_insert(_MENUS_TABLE, _MENU_ROWS)
    op.bulk_insert(
        sa.table(
            "role_menus",
            sa.column("role_id", sa.String),
            sa.column("menu_id", sa.String),
        ),
        [{"role_id": "role-000001", "menu_id": row["id"]} for row in _MENU_ROWS],
    )


def downgrade() -> None:
    ids = [row["id"] for row in _MENU_ROWS]
    op.execute(
        sa.text("DELETE FROM role_menus WHERE menu_id = ANY(:ids)").bindparams(
            sa.bindparam("ids", ids)
        )
    )
    op.execute(
        sa.text("DELETE FROM menus WHERE id = ANY(:ids)").bindparams(
            sa.bindparam("ids", ids)
        )
    )
    for tbl in ("ingest_tasks", "jobs"):
        op.drop_column(tbl, "deleted_by_dataset_id")
        op.drop_column(tbl, "deleted_at")
    op.drop_column("datasets", "deleted_reason")
    op.drop_column("datasets", "deleted_at")
