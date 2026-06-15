"""审计日志相关 schema。"""

from __future__ import annotations

from datetime import datetime

from app.schemas.common import CamelModel


class AuditLogRead(CamelModel):
    """审计日志读模型(响应);snake_case ⇄ camelCase 由 CamelModel 处理。"""

    id: str
    username: str
    action: str
    method: str
    path: str
    target: str | None = None
    status_code: int
    created_at: datetime
