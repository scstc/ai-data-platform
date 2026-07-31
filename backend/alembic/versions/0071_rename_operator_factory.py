"""「算子市场」更名「算子工厂」(数据迁移,无 schema 变更)。

Revision ID: 0071_rename_operator_factory
Revises: 0070_seed_custom_operators
Create Date: 2026-07-31

前端菜单名与页面标题取自 menus 表(0021 播种),仅改前端文案不够,
须同步改库:menu-100001 主菜单 + 其下 3 个 F 按钮行(0055/0060 播种)。
幂等:按 id 定位,replace 只改「算子市场」前缀,已改过的行不受影响。
"""

from __future__ import annotations

from alembic import op

revision: str = "0071_rename_operator_factory"
down_revision: str | None = "0070_seed_custom_operators"
branch_labels = None
depends_on = None

_IDS = "('menu-100001', 'menu-1000011', 'menu-1000012', 'menu-1000013')"


def upgrade() -> None:
    op.execute(
        "UPDATE menus SET name = replace(name, '算子市场', '算子工厂')"
        f" WHERE id IN {_IDS}"
    )


def downgrade() -> None:
    op.execute(
        "UPDATE menus SET name = replace(name, '算子工厂', '算子市场')"
        f" WHERE id IN {_IDS}"
    )
