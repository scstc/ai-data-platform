"""hosted s3: dataset_versions.source_datasource_id

Revision ID: 0008_hosted_s3
Revises: 0007_content_safety
Create Date: 2026-06-15

外部 S3 数据托管(#18)落地(设计见 docs/plan/08-外部S3托管设计.md):
- dataset_versions 加列 source_datasource_id(String,nullable)——hosted 版本
  据此找回 S3 凭证;受管版本为空。
- 无其它结构变更(origin/storage_uri/format 复用)。仅加一可空列,不动现网数据(见设计 §5)。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008_hosted_s3"
down_revision: str | None = "0007_content_safety"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "dataset_versions",
        sa.Column("source_datasource_id", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("dataset_versions", "source_datasource_id")
