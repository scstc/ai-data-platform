"""seed data lake menu: 把数据湖菜单种进 menus 表并授权给普通用户角色。

Revision ID: 0037_seed_data_lake_menu
Revises: 0036_add_data_lake_tables
Create Date: 2026-07-01

配套 0036 建的数据湖两张表(data_lakes / data_lake_snapshots),把动态菜单
(RBAC B1) 需要的侧边栏项种进 menus 表。菜单 id 用 menu-100027 起,续在
0021 的业务菜单段(menu-100001 ~ menu-100026)之后,避开系统菜单
(menu-000xxx) 与数据湖表 id 冲突。

菜单位置(见 config/routes.ts):数据接入(sort=10) → 数据湖(sort=15) →
数据集仓库(sort=20)。sort=15 让数据湖排在数据接入之后、数据集仓库之前,
符合"接入 → 湖归档 → 数据集加工"的湖集分离流程顺序。

授权:普通用户角色(role-000002)可见数据湖菜单;超管走通配无需显式授权。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0037_seed_data_lake_menu"
down_revision: str | None = "0036_add_data_lake_tables"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COMMON_ROLE = "role-000002"

# 菜单 id 段:menu-100027(顶级 M) / menu-100028(叶子 C)
# 续在 0021 业务菜单最后一个 id (menu-100026) 之后
_MENU_TOP = "menu-100027"
_MENU_LIST = "menu-100028"


def upgrade() -> None:
    menus_table = sa.table(
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
    op.bulk_insert(
        menus_table,
        [
            {
                "id": _MENU_TOP,
                "parent_id": None,
                "name": "数据湖",
                "menu_type": "M",
                "path": "/data-lakes",
                "component": None,
                "perms": None,
                "icon": "cluster",
                "sort": 15,  # 数据接入(10) 之后、数据集仓库(20) 之前
                "visible": "0",
                "status": "0",
            },
            {
                "id": _MENU_LIST,
                "parent_id": _MENU_TOP,
                "name": "数据湖列表",
                "menu_type": "C",
                "path": "/data-lakes/list",
                "component": "data-lakes",
                "perms": None,
                "icon": None,
                "sort": 1,
                "visible": "0",
                "status": "0",
            },
        ],
    )

    # 授权:普通用户角色 role-000002 可见数据湖菜单
    op.bulk_insert(
        sa.table(
            "role_menus",
            sa.column("role_id", sa.String),
            sa.column("menu_id", sa.String),
        ),
        [
            {"role_id": _COMMON_ROLE, "menu_id": _MENU_TOP},
            {"role_id": _COMMON_ROLE, "menu_id": _MENU_LIST},
        ],
    )


def downgrade() -> None:
    op.execute(
        f"DELETE FROM role_menus WHERE menu_id IN ('{_MENU_TOP}', '{_MENU_LIST}')"
    )
    op.execute(f"DELETE FROM menus WHERE id IN ('{_MENU_TOP}', '{_MENU_LIST}')")
