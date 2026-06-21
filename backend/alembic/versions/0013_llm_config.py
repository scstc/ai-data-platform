"""llm_providers / llm_usage: 可配置 LLM 提供商 + 调用用量追踪

Revision ID: 0013_llm_config
Revises: 0012_semantic_type
Create Date: 2026-06-21

新建两张表：
- llm_providers: 存储平台可配的 LLM 提供商（多条，至多一条 is_active=True）。
- llm_usage: 按特性/模型记录每次 LLM 调用的 token 用量与延迟（best-effort，不影响主流程）。

downgrade：删除两张表。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013_llm_config"
down_revision: str | None = "0012_semantic_type"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_providers",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("provider", sa.String(), nullable=False),
        sa.Column("base_url", sa.String(), nullable=False),
        sa.Column("api_key", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "llm_usage",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("provider_id", sa.String(), nullable=True),
        sa.Column("feature", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("prompt_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "completion_tokens", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("total_tokens", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("success", sa.Boolean(), nullable=False),
        sa.Column("latency_ms", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("llm_usage")
    op.drop_table("llm_providers")
