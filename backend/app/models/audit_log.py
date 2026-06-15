"""操作审计日志 ORM 模型。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class AuditLog(Base):
    """一条写操作审计:谁(username)/何动作(action)/方法/路径/目标/状态码/时间。

    由审计中间件(app/core/audit.py)在响应后异步写入;读请求不记。
    """

    __tablename__ = "audit_logs"

    # 主键形如 "aud-" + 6 位 hex
    id: Mapped[str] = mapped_column(String, primary_key=True)
    # 操作者(cookie 解析失败记 "anonymous")
    username: Mapped[str] = mapped_column(String, nullable=False)
    # 动作语义,如 "dataset.delete"
    action: Mapped[str] = mapped_column(String, nullable=False)
    method: Mapped[str] = mapped_column(String, nullable=False)
    path: Mapped[str] = mapped_column(String, nullable=False)
    # 目标资源标识(路径末段,形如 id 时),可空
    target: Mapped[str | None] = mapped_column(String, nullable=True)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False, index=True
    )
