"""站内通知路由:列出 / 未读计数 / 标记已读 / 全部已读。

所有端点强制 ``recipient = 当前登录用户``——任何用户只能看 / 标自己的通知,
不可见他人(越权防护在后端这一层,见 docs 设计 §5)。仅需登录、不限角色。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_user
from app.core.db import get_session
from app.models.user import User
from app.schemas.common import PageResponse
from app.schemas.notification import NotificationOut, UnreadCountResponse
from app.services import notifications as notif_svc

router = APIRouter(tags=["notifications"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]
CurrentUser = Annotated[User, Depends(require_user)]


@router.get("/notifications", response_model=PageResponse[NotificationOut])
async def list_notifications(
    session: SessionDep,
    user: CurrentUser,
    only_unread: bool = Query(False, alias="onlyUnread"),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100, alias="pageSize"),
) -> PageResponse[NotificationOut]:
    """分页列出当前用户的通知(按创建时间倒序);onlyUnread=true 仅未读。"""
    rows, total = await notif_svc.list_for(
        session,
        user.username,
        only_unread=only_unread,
        page=page,
        page_size=page_size,
    )
    data = [NotificationOut.model_validate(r) for r in rows]
    return PageResponse[NotificationOut](data=data, total=total)


@router.get("/notifications/unread-count", response_model=UnreadCountResponse)
async def get_unread_count(
    session: SessionDep,
    user: CurrentUser,
) -> UnreadCountResponse:
    """当前用户的未读通知数(前端铃铛 Badge 轮询此端点)。"""
    count = await notif_svc.unread_count(session, user.username)
    return UnreadCountResponse(count=count)


@router.post("/notifications/{notification_id}/read")
async def read_notification(
    notification_id: str,
    session: SessionDep,
    user: CurrentUser,
) -> JSONResponse:
    """标记一条通知已读(只能标自己的;幂等)。他人 / 不存在 → 404。"""
    ok = await notif_svc.mark_read(session, user.username, notification_id)
    if not ok:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "通知不存在"},
        )
    return JSONResponse(content={"success": True})


@router.post("/notifications/read-all")
async def read_all_notifications(
    session: SessionDep,
    user: CurrentUser,
) -> JSONResponse:
    """把当前用户的全部未读通知标已读,返回更新条数。"""
    updated = await notif_svc.mark_all_read(session, user.username)
    return JSONResponse(content={"success": True, "updated": updated})
