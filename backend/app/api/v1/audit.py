"""审计日志路由:GET /audit(仅 admin),分页 + 过滤,按时间倒序。

契约见 docs/plan/06 §2.4:username(ilike)/action(ilike)/method/时间区间过滤,
返回 PageResponse[AuditLogRead]。门控 require_admin(越权防护在后端)。
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.core.db import get_session
from app.models.audit_log import AuditLog
from app.schemas.audit import AuditLogRead
from app.schemas.common import PageResponse

router = APIRouter(tags=["audit"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
CreatedStartQuery = Annotated[datetime | None, Query(alias="createdStart")]
CreatedEndQuery = Annotated[datetime | None, Query(alias="createdEnd")]


@router.get(
    "/audit",
    response_model=PageResponse[AuditLogRead],
    dependencies=[Depends(require_admin)],
)
async def list_audit_logs(
    session: SessionDep,
    current: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, alias="pageSize"),
    username: str | None = Query(None),
    action: str | None = Query(None),
    method: str | None = Query(None),
    created_start: CreatedStartQuery = None,
    created_end: CreatedEndQuery = None,
) -> PageResponse[AuditLogRead]:
    """分页查询审计日志,按 created_at 倒序;可选 username/action/method/时间区间过滤。"""
    conds = []
    if username:
        conds.append(AuditLog.username.ilike(f"%{username}%"))
    if action:
        conds.append(AuditLog.action.ilike(f"%{action}%"))
    if method:
        conds.append(AuditLog.method == method)
    if created_start is not None:
        conds.append(AuditLog.created_at >= created_start)
    if created_end is not None:
        conds.append(AuditLog.created_at <= created_end)

    total = await session.scalar(
        select(func.count()).select_from(AuditLog).where(*conds)
    )
    offset = (current - 1) * page_size
    rows = (
        await session.scalars(
            select(AuditLog)
            .where(*conds)
            .order_by(AuditLog.created_at.desc())
            .offset(offset)
            .limit(page_size)
        )
    ).all()
    data = [AuditLogRead.model_validate(r) for r in rows]
    return PageResponse[AuditLogRead](data=data, total=total or 0)
