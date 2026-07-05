"""trainset menu: 新增「训练集生成」治理子菜单(独立侧边栏入口)。

Revision ID: 0054_trainset_menu
Revises: 0053_hide_augment_menu
Create Date: 2026-07-05

训练集生成(LLM 从源数据造 QA/COT/偏好训练样本,Job.type='trainset')新增为
「数据治理」下的独立可见子菜单(menu-100030,/governance/trainset),与数据合成
(menu-100018)并列独立显示。perms='governance:trainset:list' 直接种在菜单行,
普通角色(role-000002)回填授权(与其余业务菜单同口径,超管通配无需显式授权)。

菜单 id 用 menu-100030(接业务菜单段,adp_gov 现有最大为 100029=治理工场)。
标注(menu-100020)sort 6→7,给训练集生成腾出 sort=6(紧随合成/增强的生成家族)。

visible='0' = 显示(RuoYi 约定,与 0021 一致;'1'=隐藏)。

**不要在本仓库对 dev 库执行 upgrade**——只对 adp_gov 库单独执行(与 0044~0053 一致)。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0054_trainset_menu"
down_revision: str | None = "0053_hide_augment_menu"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TRAINSET_MENU_ID = "menu-100030"
_GOVERNANCE_PARENT = "menu-100014"
_ANNOTATION_MENU_ID = "menu-100020"
_COMMON_ROLE = "role-000002"


def upgrade() -> None:
    op.bulk_insert(
        sa.table(
            "menus",
            sa.column("id", sa.String), sa.column("parent_id", sa.String),
            sa.column("name", sa.String), sa.column("menu_type", sa.String),
            sa.column("path", sa.String), sa.column("component", sa.String),
            sa.column("perms", sa.String), sa.column("icon", sa.String),
            sa.column("sort", sa.Integer), sa.column("visible", sa.String),
            sa.column("status", sa.String),
        ),
        [
            {
                "id": _TRAINSET_MENU_ID, "parent_id": _GOVERNANCE_PARENT,
                "name": "训练集生成", "menu_type": "C",
                "path": "/governance/trainset", "component": "trainset",
                "perms": "governance:trainset:list", "icon": None,
                "sort": 6, "visible": "0", "status": "0",
            }
        ],
    )
    # 标注让位到 sort=7(训练集生成占 6,紧随合成/增强生成家族)
    op.execute(
        f"UPDATE menus SET sort = 7 WHERE id = '{_ANNOTATION_MENU_ID}'"
    )
    # 普通角色回填授权(超管通配无需显式授权)
    op.bulk_insert(
        sa.table(
            "role_menus",
            sa.column("role_id", sa.String),
            sa.column("menu_id", sa.String),
        ),
        [{"role_id": _COMMON_ROLE, "menu_id": _TRAINSET_MENU_ID}],
    )


def downgrade() -> None:
    op.execute(
        f"DELETE FROM role_menus WHERE menu_id = '{_TRAINSET_MENU_ID}'"
    )
    op.execute(f"DELETE FROM menus WHERE id = '{_TRAINSET_MENU_ID}'")
    op.execute(f"UPDATE menus SET sort = 6 WHERE id = '{_ANNOTATION_MENU_ID}'")
