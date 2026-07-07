"""rename make menu: 侧边栏「数据合成」更名「数据合并」。

Revision ID: 0058_rename_make_menu
Revises: 0057_data_lake_acl
Create Date: 2026-07-07

数据合成已改为纯 Python 多 jsonl 按行拼接/追加(mode=merge/concat,见 0050),
功能语义就是"合并",菜单名同步更名。侧边栏标签直接取 menus.name
(getRouters → build_router_tree),故只需改 DB;menu id/perms/路由均不动。

**不要在本仓库对 dev 库执行 upgrade**——只对 adp_gov 库单独执行(与 0044~0057 一致)。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0058_rename_make_menu"
down_revision: str | None = "0057_data_lake_acl"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 0021 业务菜单段:menu-100018 = 数据合并(/governance/make)
_MAKE_MENU_ID = "menu-100018"


def upgrade() -> None:
    op.execute(
        f"UPDATE menus SET name = '数据合并' WHERE id = '{_MAKE_MENU_ID}'"
    )


def downgrade() -> None:
    op.execute(
        f"UPDATE menus SET name = '数据合成' WHERE id = '{_MAKE_MENU_ID}'"
    )
