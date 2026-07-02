"""content safety: findings 加 table_name + 新表 review_rules

Revision ID: 0041_review_rules_multitable
Revises: 0040_dvt_stats_uri
Create Date: 2026-07-02

内容安全多文件兼容 + 规则库:
- review_findings.table_name:命中所在的版本成员表名(多表版本);旧数据/
  单文件版本为 NULL,row_index 语义不变(成员内相对行号)。
- review_rules:可复用的自定义审核规则库(敏感词/正则),建审核任务与上传
  前置预检共同消费;enabled=false 即下线不删除。
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0041_review_rules_multitable"
down_revision: Union[str, None] = "0040_dvt_stats_uri"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "review_findings", sa.Column("table_name", sa.String(), nullable=True)
    )
    op.create_table(
        "review_rules",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("pattern", sa.Text(), nullable=False),
        sa.Column("category", sa.String(), nullable=False, server_default="other"),
        sa.Column("severity", sa.String(), nullable=False, server_default="medium"),
        sa.Column(
            "enabled", sa.Boolean(), nullable=False, server_default=sa.text("true")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_review_rules_enabled", "review_rules", ["enabled"])


def downgrade() -> None:
    op.drop_index("ix_review_rules_enabled", table_name="review_rules")
    op.drop_table("review_rules")
    op.drop_column("review_findings", "table_name")
