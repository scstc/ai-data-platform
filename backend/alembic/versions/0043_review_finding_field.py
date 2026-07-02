"""review_findings 加 field 列: 逐字段扫描时记录命中字段名

Revision ID: 0043_review_finding_field
Revises: 0042_ingest_dsid_nullable
Create Date: 2026-07-02

内容安全审核支持按成员表配置扫描字段(config.scanFields)后,规则/PII 命中
需落到具体字段以便追溯(尤其 delete 处置的"这行为什么被删")。
旧数据与默认扫描/LLM 行级命中为 NULL。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0043_review_finding_field"
down_revision: str | None = "0042_ingest_dsid_nullable"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("review_findings", sa.Column("field", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("review_findings", "field")
