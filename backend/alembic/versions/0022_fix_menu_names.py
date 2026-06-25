"""fix business menu names: 把 0021 简写的侧边栏菜单名改回 routes.ts/i18n 里的全名

Revision ID: 0022_fix_menu_names
Revises: 0021_seed_business_menus
Create Date: 2026-06-25

动态菜单的侧边栏标签直接取 menus.name(getRouters → build_router_tree),
0021 种子里写成了简写(数据源/接入任务/数据集/预设/分类/标签/清洗/蒸馏/合成/增强/标注/血缘),
与 frontend/src/locales/zh-CN/menu.ts 里原有的全名对不上。本迁移 1:1 改回全名。
其余菜单名 0021 已与 i18n 一致,不动。
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0022_fix_menu_names"
down_revision: str | None = "0021_seed_business_menus"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# menu_id -> (全名, 0021 简写) ；全名取自 frontend/src/locales/zh-CN/menu.ts
_RENAMES: list[tuple[str, str, str]] = [
    ("menu-100003", "数据源管理", "数据源"),
    ("menu-100004", "采集任务", "接入任务"),
    ("menu-100008", "数据集列表", "数据集"),
    ("menu-100009", "已发布数据集", "预设"),
    ("menu-100010", "分类管理", "分类"),
    ("menu-100011", "标签管理", "标签"),
    ("menu-100016", "数据清洗", "清洗"),
    ("menu-100017", "数据蒸馏", "蒸馏"),
    ("menu-100018", "数据合成", "合成"),
    ("menu-100019", "数据增强", "增强"),
    ("menu-100020", "数据标注", "标注"),
    ("menu-100023", "数据血缘", "血缘"),
]


def upgrade() -> None:
    for mid, full, _short in _RENAMES:
        op.execute(f"UPDATE menus SET name = '{full}' WHERE id = '{mid}'")


def downgrade() -> None:
    for mid, _full, short in _RENAMES:
        op.execute(f"UPDATE menus SET name = '{short}' WHERE id = '{mid}'")
