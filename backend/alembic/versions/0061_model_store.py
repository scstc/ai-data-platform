"""system_settings 表 + 模型仓库菜单(C/F)与超管授权

Revision ID: 0061_model_store
Revises: 0060_operator_visible
Create Date: 2026-07-08

- system_settings：平台级 KV 设置表（首个 key：dj_model_home 模型仓库根路径）。
- 菜单：运维监控(menu-100021)下新增 C「模型仓库」(/ops/model-store) 及
  查询/配置路径/重新扫描三个 F 按钮；对齐 LLM 配置的授权口径（仅超管
  role-000001；其余角色由管理员在角色管理里按需勾选）。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0061_model_store"
down_revision = "0060_operator_visible"
branch_labels = None
depends_on = None

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

_C_ID = "menu-100031"
_MENU_ROWS = [
    {
        "id": _C_ID,
        "parent_id": "menu-100021",
        "name": "模型仓库",
        "menu_type": "C",
        "path": "/ops/model-store",
        "component": "ops/model-store",
        "perms": "ops:model:list",
        "icon": None,
        "sort": 5,
        "visible": "0",
        "status": "0",
    },
    *[
        {
            "id": f"{_C_ID}{j}",
            "parent_id": _C_ID,
            "name": f"模型仓库{label}",
            "menu_type": "F",
            "path": None,
            "component": None,
            "perms": f"ops:model:{action}",
            "icon": None,
            "sort": j,
            "visible": "0",
            "status": "0",
        }
        for j, (label, action) in enumerate(
            [("查询", "list"), ("配置路径", "edit"), ("重新扫描", "scan")], start=1
        )
    ],
]


def upgrade() -> None:
    op.create_table(
        "system_settings",
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("value", sa.Text(), nullable=True),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
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
    op.drop_table("system_settings")
