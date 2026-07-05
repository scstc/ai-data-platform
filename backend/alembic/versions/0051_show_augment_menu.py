"""show augment menu: 数据增强菜单恢复侧边栏直显,退出治理工场场景。

Revision ID: 0051_show_augment_menu
Revises: 0050_show_synthesis_menu
Create Date: 2026-07-05

与 0050(数据合成)同口径:数据增强从治理工场撤出(前端 SCENARIOS 删
augmentation),侧边栏恢复独立菜单入口(回滚 0048 对 menu-100019 的隐藏)。
清洗/蒸馏两个场景菜单维持隐藏,仍走治理工场。

**不要在本仓库对 dev 库执行 upgrade**——只对 adp_gov 库单独执行(与 0044~0050 一致)。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0051_show_augment_menu"
down_revision: str | None = "0050_show_synthesis_menu"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 0021 业务菜单段:menu-100019 = 数据增强(/governance/augment)
_AUGMENT_MENU_ID = "menu-100019"


def upgrade() -> None:
    op.execute(
        f"UPDATE menus SET visible = '0' WHERE id = '{_AUGMENT_MENU_ID}'"
    )


def downgrade() -> None:
    op.execute(
        f"UPDATE menus SET visible = '1' WHERE id = '{_AUGMENT_MENU_ID}'"
    )
