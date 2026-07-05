"""seed workbench menu: 把治理工场菜单种进 menus 表并授权给普通用户角色。

Revision ID: 0047_seed_workbench_menu
Revises: 0046_pipelines
Create Date: 2026-07-05

配套 0046 的 pipelines 实体:侧边栏由后端 getRouters 按 menus 表下发
(RBAC B1),routes.ts 新增的 /governance/workbench 需要对应菜单行才可见。

菜单位置:数据治理(menu-100014)首个子项(sort=0,排在内容安全 sort=1 之前),
体现"工场为治理主入口、四场景菜单为薄入口"的信息架构。id 续在业务菜单段
当前最大值 menu-100028(0037 数据湖)之后。

perms 留空(同 0037 先例):流水线接口按 scenario 复用既有
governance:{cleaning,distillation,make,augment}:list 权限码,不新造码。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0047_seed_workbench_menu"
down_revision: str | None = "0046_pipelines"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_COMMON_ROLE = "role-000002"
_GOVERNANCE_TOP = "menu-100014"
_MENU_WORKBENCH = "menu-100029"


def upgrade() -> None:
    op.bulk_insert(
        sa.table(
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
        ),
        [
            {
                "id": _MENU_WORKBENCH,
                "parent_id": _GOVERNANCE_TOP,
                "name": "治理工场",
                "menu_type": "C",
                "path": "/governance/workbench",
                "component": "governance/workbench",
                "perms": None,
                "icon": None,
                "sort": 0,  # 内容安全(1) 之前:工场是治理主入口
                "visible": "0",
                "status": "0",
            },
        ],
    )

    op.bulk_insert(
        sa.table(
            "role_menus",
            sa.column("role_id", sa.String),
            sa.column("menu_id", sa.String),
        ),
        [{"role_id": _COMMON_ROLE, "menu_id": _MENU_WORKBENCH}],
    )


def downgrade() -> None:
    op.execute(f"DELETE FROM role_menus WHERE menu_id = '{_MENU_WORKBENCH}'")
    op.execute(f"DELETE FROM menus WHERE id = '{_MENU_WORKBENCH}'")
