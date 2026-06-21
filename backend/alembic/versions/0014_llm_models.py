"""llm_models: 供应商下可选模型清单（获取模型 / 手动添加）

Revision ID: 0014_llm_models
Revises: 0013_llm_config
Create Date: 2026-06-21

新建 llm_models 表：记录每个 LLM 提供商「可选用的模型」清单。
- 「当前生效模型」仍由 llm_providers.model 决定，本表只是候选清单。
- UNIQUE(provider_id, model)：同供应商下模型名唯一，便于拉取时做 upsert。
- 数据迁移：把每个现有 provider 的 model 灌入一行（source=manual），
  保证老数据无缝纳入管理。

downgrade：删除该表。
"""

from __future__ import annotations

import secrets
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0014_llm_models"
down_revision: str | None = "0013_llm_config"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_models",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("provider_id", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False, server_default="manual"),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "provider_id", "model", name="uq_llm_models_provider_model"
        ),
    )
    op.create_index("ix_llm_models_provider_id", "llm_models", ["provider_id"])

    # 数据迁移：现有 provider.model → llm_models 一行（source=manual）
    bind = op.get_bind()
    rows = bind.execute(
        sa.text("SELECT id, model FROM llm_providers WHERE model <> ''")
    ).fetchall()
    for provider_id, model in rows:
        bind.execute(
            sa.text(
                "INSERT INTO llm_models (id, provider_id, model, source) "
                "VALUES (:id, :provider_id, :model, 'manual')"
            ),
            {
                "id": f"lmd-{secrets.token_hex(3)}",
                "provider_id": provider_id,
                "model": model,
            },
        )


def downgrade() -> None:
    op.drop_index("ix_llm_models_provider_id", table_name="llm_models")
    op.drop_table("llm_models")
