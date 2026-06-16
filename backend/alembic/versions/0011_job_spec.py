"""jobs: 加工任务重跑规格(spec)

Revision ID: 0011_job_spec
Revises: 0010_publish_gate
Create Date: 2026-06-16

加工任务重跑(rerun)需要原始执行规格:建任务时把 JobCreate(算子 + 输出去向 +
输入版本)存进 jobs.spec(JSONB),POST /jobs/{id}/rerun 据此对原输入版本再跑
一次产新版本。仅 add column,nullable,存量任务为空(不可重跑),无回填风险。

downgrade:删该列。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0011_job_spec"
down_revision: str | None = "0010_publish_gate"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "jobs",
        sa.Column("spec", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("jobs", "spec")
