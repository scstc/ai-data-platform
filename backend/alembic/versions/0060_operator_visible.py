"""operator visible: 算子加「是否显示」列 + 市场菜单种「隐藏/显示」按钮权限。

Revision ID: 0060_operator_visible
Revises: 0059_rename_trainset_menu
Create Date: 2026-07-08

不符合要求的算子由管理员置 visible=false:算子市场与任务编排选择器不再展示,
但已编排任务的执行与提交校验(get_operator/runnable_reason)不受影响。
按钮权限 ``operator:visibility`` 挂算子市场(menu-100001)下,F 行约定与 0055 一致
(不回填 role_menus,超管通配放行,其余角色经「分配菜单」矩阵按需勾选)。

**不要在本仓库对 dev 库执行 upgrade**——只对 adp_gov 库单独执行(与 0044~0059 一致)。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0060_operator_visible"
down_revision: str | None = "0059_rename_trainset_menu"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 算子市场 menu-100001 下已有 F:...11 查询 / ...12 上传,本按钮续 3 号
_MENU_ID = "menu-1000013"


def upgrade() -> None:
    op.add_column(
        "operators",
        sa.Column("visible", sa.Boolean(), nullable=False, server_default=sa.true()),
    )
    op.execute(
        sa.text(
            "INSERT INTO menus (id, parent_id, name, menu_type, path, component,"
            " perms, icon, sort, visible, status)"
            " VALUES (:i, 'menu-100001', '算子市场隐藏/显示', 'F', NULL, NULL,"
            " 'operator:visibility', NULL, 3, '0', '0')"
        ).bindparams(i=_MENU_ID)
    )


def downgrade() -> None:
    op.execute(
        sa.text("DELETE FROM role_menus WHERE menu_id = :i").bindparams(i=_MENU_ID)
    )
    op.execute(sa.text("DELETE FROM menus WHERE id = :i").bindparams(i=_MENU_ID))
    op.drop_column("operators", "visible")
