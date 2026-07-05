"""show synthesis menu: 数据合成菜单恢复侧边栏直显,退出治理工场场景。

Revision ID: 0050_show_synthesis_menu
Revises: 0049_dvt_storage_uri_index
Create Date: 2026-07-05

数据合成已改为纯 Python 多 jsonl 按行拼接(mode=merge),不再是"算子流水线"
形态,与治理工场的流水线场景 Tab 不匹配 → 从工场撤出(前端 SCENARIOS 同步
删除 synthesis),侧边栏恢复独立菜单入口(回滚 0048 对 menu-100018 的隐藏)。
清洗/蒸馏/增强三个场景菜单维持隐藏,仍走治理工场。

**不要在本仓库对 dev 库执行 upgrade**——只对 adp_gov 库单独执行(与 0044~0049 一致)。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0050_show_synthesis_menu"
down_revision: str | None = "0049_dvt_storage_uri_index"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 0021 业务菜单段:menu-100018 = 数据合成(/governance/make)
_SYNTHESIS_MENU_ID = "menu-100018"


def upgrade() -> None:
    op.execute(
        f"UPDATE menus SET visible = '0' WHERE id = '{_SYNTHESIS_MENU_ID}'"
    )


def downgrade() -> None:
    op.execute(
        f"UPDATE menus SET visible = '1' WHERE id = '{_SYNTHESIS_MENU_ID}'"
    )
