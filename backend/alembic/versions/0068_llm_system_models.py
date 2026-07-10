"""系统默认模型表（按能力位）

Revision ID: 0068_llm_system_models
Revises: 0067_dataset_recycle_bin
Create Date: 2026-07-10

Dify 风格「系统模型设置」：每个能力位（chat / embedding / rerank /
speech2text / tts）至多一行，记录默认 provider_id + model。
chat 位由 API 层与 llm_providers.is_active 联动，其余能力位先落库备用。

**只对整改库 adp_trace 执行 upgrade**。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0068_llm_system_models"
down_revision: str | None = "0067_dataset_recycle_bin"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_system_models",
        sa.Column("capability", sa.String(), nullable=False),
        sa.Column("provider_id", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("capability"),
    )


def downgrade() -> None:
    op.drop_table("llm_system_models")
