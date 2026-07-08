"""operator star: 算子加「星标计数」列(纯正向点击计数,无需权限)。

Revision ID: 0063_operator_star
Revises: 0062_llm_usage_job_id
Create Date: 2026-07-08

算子市场卡片 + 详情页的五角星按钮,每次点击 +1,不做撤销/去重——
纯人气信号,与管理员维护的 recommend 字段无关。

**不要在本仓库对 dev 库执行 upgrade**——只对 adp_gov 库单独执行(与 0044~0060 一致)。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0063_operator_star"
down_revision = "0062_llm_usage_job_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "operators",
        sa.Column(
            "star_count", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
    )


def downgrade() -> None:
    op.drop_column("operators", "star_count")
