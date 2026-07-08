"""llm_usage 加 job_id 列(算子/任务级用量归属)

Revision ID: 0062_llm_usage_job_id
Revises: 0061_model_store
Create Date: 2026-07-08

dj-process 算子的 LLM 调用经 /api/v1/llm-proxy/{job_id} 代理转发后记入
llm_usage(feature='operator');job_id 关联 jobs 表,支撑任务级用量统计。
平台内建 AI 功能的既有记录无任务归属,列可空。
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0062_llm_usage_job_id"
down_revision = "0061_model_store"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("llm_usage", sa.Column("job_id", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("llm_usage", "job_id")
