"""content safety: review_findings + jobs.review_report

Revision ID: 0007_content_safety
Revises: 0006_auth_audit
Create Date: 2026-06-15

内容安全审核(#4)落地(设计见 docs/plan/07-内容安全设计.md):
- 新表 review_findings:逐条命中记录(job_id / created_at 加索引,供分页与倒序)。
- jobs 加 review_report 列(JSONB nullable),存审核汇总报告。
- 仅加表/加列,不动现网数据(见设计 §5)。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0007_content_safety"
down_revision: str | None = "0006_auth_audit"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "review_findings",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("job_id", sa.String(), nullable=False),
        sa.Column("version_id", sa.String(), nullable=False),
        sa.Column("row_index", sa.Integer(), nullable=False),
        sa.Column("category", sa.String(), nullable=False),
        sa.Column("severity", sa.String(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("detail", sa.Text(), nullable=True),
        sa.Column("snippet", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_review_findings_job_id", "review_findings", ["job_id"]
    )
    op.create_index(
        "ix_review_findings_created_at", "review_findings", ["created_at"]
    )

    op.add_column(
        "jobs",
        sa.Column(
            "review_report",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("jobs", "review_report")
    op.drop_index(
        "ix_review_findings_created_at", table_name="review_findings"
    )
    op.drop_index("ix_review_findings_job_id", table_name="review_findings")
    op.drop_table("review_findings")
