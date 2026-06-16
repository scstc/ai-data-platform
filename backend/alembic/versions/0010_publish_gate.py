"""dataset_versions: 安全扫描结论 + 发布状态(版本级发布门)

Revision ID: 0010_publish_gate
Revises: 0009_categories
Create Date: 2026-06-16

数据安全扫描 = 版本级发布门(#4 通过性指标,设计见 docs/plan/11):
- 给 dataset_versions 加 5 列:scan_verdict / verdict_source / verdict_note /
  publish_status / published_at。
- 两个非空状态列带 server_default('unscanned' / 'draft'),存量版本安全回填,
  不影响任何现有 接入/加工/质量/审核 流程。
- downgrade:逆序删 5 列。

仅 add column,无数据迁移、无回填风险。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010_publish_gate"
down_revision: str | None = "0009_categories"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "dataset_versions",
        sa.Column(
            "scan_verdict",
            sa.String(),
            nullable=False,
            server_default="unscanned",
        ),
    )
    op.add_column(
        "dataset_versions",
        sa.Column("verdict_source", sa.String(), nullable=True),
    )
    op.add_column(
        "dataset_versions",
        sa.Column("verdict_note", sa.String(), nullable=True),
    )
    op.add_column(
        "dataset_versions",
        sa.Column(
            "publish_status",
            sa.String(),
            nullable=False,
            server_default="draft",
        ),
    )
    op.add_column(
        "dataset_versions",
        sa.Column("published_at", sa.DateTime(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("dataset_versions", "published_at")
    op.drop_column("dataset_versions", "publish_status")
    op.drop_column("dataset_versions", "verdict_note")
    op.drop_column("dataset_versions", "verdict_source")
    op.drop_column("dataset_versions", "scan_verdict")
