"""API 推送幂等 ORM 模型(迁移 0069)。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class PushIdempotency(Base):
    """推送入站幂等记录:同一 key 在有效期内重放,直接复用首次结果,不重复落数据。

    `task_id` 回指所属采集任务(ingest_tasks.id);`dataset_version_id` 在已
    落版本后回填,未落版本(如仅校验/去重阶段)为空。过期(`expires_at`)后
    该 key 可被新请求复用,由调用方按 `expires_at` 判断是否失效而非物理删除。
    """

    __tablename__ = "push_idempotency"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    task_id: Mapped[str] = mapped_column(String, nullable=False)
    dataset_version_id: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
