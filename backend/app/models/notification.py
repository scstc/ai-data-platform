"""站内通知 ORM 模型。"""

from __future__ import annotations

import secrets
from datetime import datetime

from sqlalchemy import Boolean, Index, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


def _new_notification_id() -> str:
    """生成形如 ntf-<6位hex> 的主键。"""
    return f"ntf-{secrets.token_hex(3)}"


class Notification(Base):
    """站内通知:任务到达终态(success/failed)时给创建者写一行。

    无 DB 级外键(沿用 llm_usage / ingest_task 的弱关联约定),source_id 仅作
    应用层回指(job-… / task-…)。索引 recipient 与 (recipient, read) 复合索引
    分别服务「列出我的通知」与「未读计数 / 仅未读过滤」。
    """

    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notifications_recipient", "recipient"),
        Index("ix_notifications_recipient_read", "recipient", "read"),
    )

    # 主键形如 "ntf-" + 6 位 hex
    id: Mapped[str] = mapped_column(String, primary_key=True)
    # 接收者 username(Job.created_by / IngestTask.creator)
    recipient: Mapped[str] = mapped_column(String, nullable=False)
    # 级别:success | error
    level: Mapped[str] = mapped_column(String, nullable=False)
    # 来源类型:job | ingest_task
    source_type: Mapped[str] = mapped_column(String, nullable=False)
    # 来源任务 id(job-… / task-…)
    source_id: Mapped[str] = mapped_column(String, nullable=False)
    # 标题,如 "蒸馏任务 xxx 已完成"
    title: Mapped[str] = mapped_column(String, nullable=False)
    # 正文(失败时放 error 摘要);可空
    body: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 已读标记
    read: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default="false"
    )
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )
    # 标记已读时间;未读为空
    read_at: Mapped[datetime | None] = mapped_column(nullable=True)
