"""hide scenario menus: 四个治理场景菜单收进治理工场,侧边栏隐藏薄入口。

Revision ID: 0048_hide_scenario_menus
Revises: 0047_seed_workbench_menu
Create Date: 2026-07-05

治理工场(0047)上线后,清洗/蒸馏/合成/增强四个菜单项与工场内场景 Tab 重复,
按信息架构收口:数据治理下仅保留 治理工场 / 内容安全(数据标注维持隐藏)。

置 visible='1' 而非删行:rbac.build_router_tree 按 status+visible 过滤侧边栏,
路由/深链不受影响,评标演示若需露出四个入口可在「菜单管理」直接打开显示开关,
无需再跑迁移。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0048_hide_scenario_menus"
down_revision: str | None = "0047_seed_workbench_menu"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 0021 业务菜单段:清洗/蒸馏/合成/增强
_SCENARIO_MENU_IDS = (
    "menu-100016",
    "menu-100017",
    "menu-100018",
    "menu-100019",
)
_IDS_SQL = ", ".join(f"'{m}'" for m in _SCENARIO_MENU_IDS)


def upgrade() -> None:
    op.execute(f"UPDATE menus SET visible = '1' WHERE id IN ({_IDS_SQL})")


def downgrade() -> None:
    op.execute(f"UPDATE menus SET visible = '0' WHERE id IN ({_IDS_SQL})")
