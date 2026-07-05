"""hide augment menu: 数据增强切回治理工场,侧边栏隐藏独立菜单。

Revision ID: 0053_hide_augment_menu
Revises: 0052_content_safety_menu_first
Create Date: 2026-07-05

数据增强重新并入治理工场(前端 SCENARIOS 加回 augmentation、菜单薄入口渲染
工场),与清洗/蒸馏同口径——侧边栏不再保留独立入口,统一从 治理工场 场景 Tab 进入。
本迁移回滚 0051 对 menu-100019 的直显(visible '0'→'1');数据合成(menu-100018)
维持独立菜单不变(合成已改多文件 merge,非算子流水线形态,不进工场)。

visible='1' 而非删行:rbac.build_router_tree 按 visible=='0' 过滤侧边栏,路由/
深链不受影响,评标演示若需露出可在「菜单管理」直接打开显示开关,无需再跑迁移。

**不要在本仓库对 dev 库执行 upgrade**——只对 adp_gov 库单独执行(与 0044~0052 一致)。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0053_hide_augment_menu"
down_revision: str | None = "0052_content_safety_menu_first"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 0021 业务菜单段:menu-100019 = 数据增强(/governance/augment)
_AUGMENT_MENU_ID = "menu-100019"


def upgrade() -> None:
    op.execute(f"UPDATE menus SET visible = '1' WHERE id = '{_AUGMENT_MENU_ID}'")


def downgrade() -> None:
    op.execute(f"UPDATE menus SET visible = '0' WHERE id = '{_AUGMENT_MENU_ID}'")
