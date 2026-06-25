"""标签路由:全局标签池的管理 API(列表含使用数 / 新建 find-or-create / 重命名 /
删除级联 / 批量删 / 合并去重)。GET /tags 同时供数据集编辑标签 typeahead(只用 id+name,
扩展字段向后兼容)。

契约:
- GET /tags → {data:[TagRead…], success}(所有登录用户)。
- POST /tags (admin) → find-or-create:同名直接返回已存在项。
- PATCH /tags/{id} (admin) → 重命名;404 缺失/409 重名。
- DELETE /tags/{id} (admin) → 级联:删 dataset_tags 关联 + 删标签。
- DELETE /tags (admin) → 批量级联删。
- POST /tags/merge (admin) → {sourceId→targetId}:重指 dataset_tags(去重)+ 删源标签。
"""
from __future__ import annotations

import secrets
from collections import defaultdict
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import delete, func, literal, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.core.db import get_session
from app.models.tag import DatasetTag, Tag
from app.schemas.tag import (
    TagBatchDelete,
    TagCreate,
    TagMerge,
    TagRead,
    TagUpdate,
)

router = APIRouter(tags=["tags"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _new_id() -> str:
    """生成形如 tag-<6位hex> 的主键(与 datasets._new_tag_id 同口径,本路由自洽定义)。"""
    return f"tag-{secrets.token_hex(3)}"


async def _usage_counts(session: AsyncSession) -> dict[str, int]:
    """{tag_id: 引用该标签的数据集数}(一条 GROUP BY,非 N+1)。"""
    rows = (
        await session.execute(
            select(DatasetTag.tag_id, func.count()).group_by(DatasetTag.tag_id)
        )
    ).all()
    return defaultdict(int, {tid: n for tid, n in rows})


async def _usage_count_of(session: AsyncSession, tag_id: str) -> int:
    return (
        await session.scalar(
            select(func.count())
            .select_from(DatasetTag)
            .where(DatasetTag.tag_id == tag_id)
        )
        or 0
    )


def _read(tag: Tag, counts: dict[str, int]) -> dict:
    item = TagRead.model_validate(tag)
    item.usage_count = counts.get(tag.id, 0)
    return item.model_dump(by_alias=True, mode="json")


@router.get("/tags")
async def list_tags(session: SessionDep) -> JSONResponse:
    """列出全部标签(按创建时间倒序)+ 各自 usageCount。所有登录用户可见。"""
    rows = (
        await session.scalars(select(Tag).order_by(Tag.created_at.desc()))
    ).all()
    counts = await _usage_counts(session)
    data = [_read(r, counts) for r in rows]
    return JSONResponse(content={"data": data, "success": True})
