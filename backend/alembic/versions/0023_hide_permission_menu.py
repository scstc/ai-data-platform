"""hide permission menu: 从侧边栏移除「权限管理」(menu-000050 及其 F 子项)

Revision ID: 0023_hide_permission_menu
Revises: 0022_fix_menu_names
Create Date: 2026-06-25

「权限管理」(/system/permission)是只读审计视图,不是权限配置入口(配置在
「菜单管理」定义权限码 +「角色管理」授权)。当前对普通用户恒为「无」、只有 admin 有内容,
价值低,先从菜单里拿掉。删的是 menus 表里 0019 种的 menu-000050(C)+ 4 个 F 按钮,
以及它们对 admin 的 role_menus 授权;前端页面/路由/overview 接口一律保留,URL 仍可直达。

注:本表侧边栏由 status 过滤(rbac.build_router_tree),不看 visible;故只能删行或置
status,不能靠「菜单管理」的显示开关隐藏。删行最干净、downgrade 可原样种回。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0023_hide_permission_menu"
down_revision: str | None = "0022_fix_menu_names"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROLE_ADMIN = "role-000001"

# 0019 原始数据:1 个 C(权限管理)+ 4 个 F 按钮(查询/新增/修改/删除)
_MENU_ROW = {
    "id": "menu-000050", "parent_id": "menu-000001", "name": "权限管理",
    "menu_type": "C", "path": "/system/permission", "component": "system/permission",
    "perms": None, "icon": "safety", "sort": 5, "visible": "0", "status": "0",
}
_F_ROWS = [
    {"id": "menu-0000501", "name": "权限管理查询", "perms": "system:perm:list", "sort": 1},
    {"id": "menu-0000502", "name": "权限管理新增", "perms": "system:perm:add", "sort": 2},
    {"id": "menu-0000503", "name": "权限管理修改", "perms": "system:perm:edit", "sort": 3},
    {"id": "menu-0000504", "name": "权限管理删除", "perms": "system:perm:remove", "sort": 4},
]


def upgrade() -> None:
    # 先删授权(admin 持全部菜单),再删菜单行
    op.execute("DELETE FROM role_menus WHERE menu_id LIKE 'menu-000050%'")
    op.execute("DELETE FROM menus WHERE id LIKE 'menu-000050%'")


def downgrade() -> None:
    menus_tbl = sa.table(
        "menus",
        sa.column("id", sa.String), sa.column("parent_id", sa.String),
        sa.column("name", sa.String), sa.column("menu_type", sa.String),
        sa.column("path", sa.String), sa.column("component", sa.String),
        sa.column("perms", sa.String), sa.column("icon", sa.String),
        sa.column("sort", sa.Integer), sa.column("visible", sa.String),
        sa.column("status", sa.String),
    )
    rows = [_MENU_ROW]
    for f in _F_ROWS:
        rows.append({
            "id": f["id"], "parent_id": "menu-000050", "name": f["name"],
            "menu_type": "F", "path": None, "component": None,
            "perms": f["perms"], "icon": None, "sort": f["sort"],
            "visible": "0", "status": "0",
        })
    op.bulk_insert(menus_tbl, rows)
    op.bulk_insert(
        sa.table("role_menus", sa.column("role_id", sa.String), sa.column("menu_id", sa.String)),
        [{"role_id": _ROLE_ADMIN, "menu_id": r["id"]} for r in rows],
    )
