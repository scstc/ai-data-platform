"""站内通知对外 schema。"""

from __future__ import annotations

from app.schemas.common import CamelModel, UtcDateTime


class NotificationOut(CamelModel):
    """单条通知读模型(不暴露 recipient——接收者恒为当前用户)。"""

    id: str
    level: str
    source_type: str
    source_id: str
    title: str
    body: str | None = None
    read: bool
    created_at: UtcDateTime
    read_at: UtcDateTime | None = None


class UnreadCountResponse(CamelModel):
    """未读计数响应:{count, success}。"""

    count: int
    success: bool = True
