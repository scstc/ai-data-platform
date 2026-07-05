"""content safety menu first: 内容安全移到数据治理菜单首位。

Revision ID: 0052_content_safety_menu_first
Revises: 0051_show_augment_menu
Create Date: 2026-07-05

数据治理子菜单排序调整:内容安全(menu-100015)与治理工场(menu-100029)
对调 sort(1↔0),其余不动 → 内容安全 / 治理工场 / 数据合成 / 数据增强。

**不要在本仓库对 dev 库执行 upgrade**——只对 adp_gov 库单独执行(与 0044~0051 一致)。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0052_content_safety_menu_first"
down_revision: str | None = "0051_show_augment_menu"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CONTENT_SAFETY = "menu-100015"  # 内容安全
_WORKBENCH = "menu-100029"  # 治理工场


def upgrade() -> None:
    op.execute(f"UPDATE menus SET sort = 0 WHERE id = '{_CONTENT_SAFETY}'")
    op.execute(f"UPDATE menus SET sort = 1 WHERE id = '{_WORKBENCH}'")


def downgrade() -> None:
    op.execute(f"UPDATE menus SET sort = 1 WHERE id = '{_CONTENT_SAFETY}'")
    op.execute(f"UPDATE menus SET sort = 0 WHERE id = '{_WORKBENCH}'")
