"""分类路由(#15,受控扁平分类库):CRUD + 跨实体用量统计。

契约见 docs/plan/09-分类管理设计.md §2.3:
- GET /categories  → {data:[CategoryRead…], success:true}(所有登录用户)。
- POST /categories (require_admin) → 建,name 重复 409。
- PATCH /categories/{id} (require_admin) → 改名/改备注,404/409。
- DELETE /categories/{id} (require_admin) → 被引用 409,空则删。

usageCount = 三实体(数据集/数据源/采集任务)引用该分类的总数,
用分表 GROUP BY 聚合(非逐行 N+1)。

另导出 build_category_name_map 供三个实体列表批量回填 categoryName(避免 N+1)。
"""

from __future__ import annotations

import secrets
from collections import defaultdict
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.core.db import get_session
from app.models.category import Category
from app.models.dataset import Dataset
from app.models.datasource import DataSource
from app.models.ingest_task import IngestTask
from app.schemas.category import CategoryCreate, CategoryRead, CategoryUpdate

router = APIRouter(tags=["categories"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _new_id() -> str:
    """生成形如 cat-<6位hex> 的主键。"""
    return f"cat-{secrets.token_hex(3)}"


async def build_category_name_map(
    session: AsyncSession, ids: list[str | None]
) -> dict[str, str]:
    """批量取 {category_id: name} 字典,供列表/详情回填 categoryName(避免 N+1)。

    入参 ids 可含 None/重复;返回字典只含命中的非空 id。空入参直接返回空字典。
    """
    wanted = {i for i in ids if i}
    if not wanted:
        return {}
    rows = (
        await session.execute(
            select(Category.id, Category.name).where(Category.id.in_(wanted))
        )
    ).all()
    return {cid: name for cid, name in rows}


async def _usage_counts(session: AsyncSession) -> dict[str, int]:
    """三实体按 category_id 分组计数后合并 → {category_id: 总引用数}。

    每表一条 GROUP BY 查询(共 3 条),不做逐行 N+1。
    """
    counts: dict[str, int] = defaultdict(int)
    for model in (Dataset, DataSource, IngestTask):
        rows = (
            await session.execute(
                select(model.category_id, func.count())
                .where(model.category_id.is_not(None))
                .group_by(model.category_id)
            )
        ).all()
        for cid, n in rows:
            counts[cid] += n
    return counts


async def _usage_count_of(session: AsyncSession, category_id: str) -> int:
    """单个分类的引用总数(供删除守卫)。"""
    total = 0
    for model in (Dataset, DataSource, IngestTask):
        total += (
            await session.scalar(
                select(func.count())
                .select_from(model)
                .where(model.category_id == category_id)
            )
            or 0
        )
    return total


@router.get("/categories")
async def list_categories(session: SessionDep) -> JSONResponse:
    """列出全部分类(创建时间倒序)+ 各自 usageCount。所有登录用户可见。"""
    rows = (
        await session.scalars(
            select(Category).order_by(Category.created_at.desc())
        )
    ).all()
    counts = await _usage_counts(session)
    data = []
    for row in rows:
        item = CategoryRead.model_validate(row)
        item.usage_count = counts.get(row.id, 0)
        data.append(item.model_dump(by_alias=True, mode="json"))
    return JSONResponse(content={"data": data, "success": True})


@router.post("/categories", dependencies=[Depends(require_admin)])
async def create_category(
    body: CategoryCreate, session: SessionDep
) -> JSONResponse:
    """新建分类;name 重复 → 409。新建分类用量恒为 0。"""
    existing = await session.scalar(
        select(Category.id).where(Category.name == body.name)
    )
    if existing is not None:
        return JSONResponse(
            status_code=409,
            content={"success": False, "message": "分类名已存在"},
        )
    category = Category(
        id=_new_id(),
        name=body.name,
        note=body.note,
        creator="admin",
    )
    session.add(category)
    await session.commit()
    await session.refresh(category)
    item = CategoryRead.model_validate(category)
    return JSONResponse(
        content={
            "data": item.model_dump(by_alias=True, mode="json"),
            "success": True,
        }
    )


@router.patch("/categories/{category_id}", dependencies=[Depends(require_admin)])
async def update_category(
    category_id: str, body: CategoryUpdate, session: SessionDep
) -> JSONResponse:
    """改名/改备注;404 缺失;改名撞已有名 → 409。"""
    category = await session.get(Category, category_id)
    if category is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "分类不存在"},
        )
    updates = body.model_dump(exclude_unset=True)
    if "name" in updates and updates["name"] is not None:
        clash = await session.scalar(
            select(Category.id)
            .where(Category.name == updates["name"])
            .where(Category.id != category_id)
        )
        if clash is not None:
            return JSONResponse(
                status_code=409,
                content={"success": False, "message": "分类名已存在"},
            )
    for field, value in updates.items():
        setattr(category, field, value)
    await session.commit()
    await session.refresh(category)
    item = CategoryRead.model_validate(category)
    item.usage_count = await _usage_count_of(session, category_id)
    return JSONResponse(
        content={
            "data": item.model_dump(by_alias=True, mode="json"),
            "success": True,
        }
    )


@router.delete(
    "/categories/{category_id}", dependencies=[Depends(require_admin)]
)
async def delete_category(
    category_id: str, session: SessionDep
) -> JSONResponse:
    """删除分类:404 缺失;被引用(usageCount>0)→ 409;空则删除成功。"""
    category = await session.get(Category, category_id)
    if category is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "分类不存在"},
        )
    usage = await _usage_count_of(session, category_id)
    if usage > 0:
        return JSONResponse(
            status_code=409,
            content={
                "success": False,
                "message": f"分类正被 {usage} 处引用,无法删除",
            },
        )
    await session.delete(category)
    await session.commit()
    return JSONResponse(content={"success": True})
