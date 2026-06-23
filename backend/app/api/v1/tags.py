"""标签路由:GET /tags 返回全局标签池(供前端自由输入联想,#标签)。

标签由数据集编辑时自由输入、后端 find-or-create 自动入库;此处只读列表,
不做独立 CRUD(零管理)。所有登录用户可见。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.models.tag import Tag

router = APIRouter(tags=["tags"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.get("/tags")
async def list_tags(session: SessionDep) -> JSONResponse:
    """列出全部标签(按创建时间倒序),供前端 Select 联想。所有登录用户可见。"""
    rows = (
        await session.scalars(select(Tag).order_by(Tag.created_at.desc()))
    ).all()
    data = [{"id": r.id, "name": r.name} for r in rows]
    return JSONResponse(content={"data": data, "success": True})
