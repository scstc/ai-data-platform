"""notifications: 站内通知中心(任务终态给创建者写一条通知)

Revision ID: 0027_notifications
Revises: 0026_version_modalities
Create Date: 2026-06-28

新建 notifications 表:采集任务 / data-juicer 任务到达终态(success/failed)时,
经 ``app.services.notifications.emit`` 给创建者(recipient)写一行,前端顶部铃铛
轮询展示未读计数与列表。

- 无 DB 级外键(沿用 llm_usage / ingest_task 的弱关联约定),source_id 仅作应用层
  回指(job-… / task-…)。
- 索引:recipient(列「我的通知」)、(recipient, read) 复合(未读计数 / 仅未读过滤)。

downgrade:删除该表。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0027_notifications"
down_revision: str | None = "0026_version_modalities"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "notifications",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("recipient", sa.String(), nullable=False),
        sa.Column("level", sa.String(), nullable=False),
        sa.Column("source_type", sa.String(), nullable=False),
        sa.Column("source_id", sa.String(), nullable=False),
        sa.Column("title", sa.String(), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column(
            "read", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("read_at", sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_notifications_recipient", "notifications", ["recipient"]
    )
    op.create_index(
        "ix_notifications_recipient_read",
        "notifications",
        ["recipient", "read"],
    )


def downgrade() -> None:
    op.drop_index("ix_notifications_recipient_read", table_name="notifications")
    op.drop_index("ix_notifications_recipient", table_name="notifications")
    op.drop_table("notifications")
