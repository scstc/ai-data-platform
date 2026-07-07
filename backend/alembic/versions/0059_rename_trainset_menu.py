"""rename trainset menu: 侧边栏「训练集生成」更名「数据合成」。

Revision ID: 0059_rename_trainset_menu
Revises: 0058_rename_make_menu
Create Date: 2026-07-07

原「数据合成」菜单(menu-100018)已在 0058 更名「数据合并」,「数据合成」名称
空出,由训练集生成(LLM 造 QA/COT/偏好样本)接替。侧边栏标签直接取 menus.name
(getRouters → build_router_tree),故只需改 DB;menu id/perms/路由/Job.type
等内部标识 ``trainset`` 均冻结不动。

**不要在本仓库对 dev 库执行 upgrade**——只对 adp_gov 库单独执行(与 0044~0058 一致)。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0059_rename_trainset_menu"
down_revision: str | None = "0058_rename_make_menu"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 0054 新增:menu-100030 = 数据合成(/governance/trainset)
_TRAINSET_MENU_ID = "menu-100030"


def upgrade() -> None:
    op.execute(
        f"UPDATE menus SET name = '数据合成' WHERE id = '{_TRAINSET_MENU_ID}'"
    )


def downgrade() -> None:
    op.execute(
        f"UPDATE menus SET name = '训练集生成' WHERE id = '{_TRAINSET_MENU_ID}'"
    )
