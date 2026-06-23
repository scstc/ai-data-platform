"""分类路由(#15,受控分类库,多级树):CRUD + 跨实体用量统计 + 层级(环检测/路径)。

契约:
- GET /categories → {data:[CategoryRead 嵌套树…], success}(所有登录用户)。
- POST /categories (require_admin) → 建;上级不存在 404;同级 (parent_id,name)
  重名 409。
- PATCH /categories/{id} (require_admin) → 改名/改备注/移父;404 缺失/上级不存在 404/
  同级重名 409/成环(上级是自身或子分类) 409。
- DELETE /categories/{id} (require_admin) → 有子分类 409/被引用 409/空则删。

usageCount = 三实体(数据集/数据源/采集任务)引用该分类的总数,分表 GROUP BY 聚合(非 N+1)。
build_category_path_map 供三实体列表批量回填分类路径(categoryName="父/子",避免 N+1);
build_category_name_map 为其向后兼容别名,老调用点无需改动。
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


async def build_category_path_map(
    session: AsyncSession, ids: list[str | None]
) -> dict[str, str]:
    """批量取 {category_id: 分类路径} 字典,供列表/详情回填 categoryName(避免 N+1)。

    路径形如 "父/子"(根分类为裸 name)。沿 parent_id 链上溯拼接;含 seen 防御坏数据成环。
    入参 ids 可含 None/重复;返回字典只含命中的非空 id。空入参直接返回空字典。
    """
    wanted = {i for i in ids if i}
    if not wanted:
        return {}
    rows = (
        await session.execute(
            select(Category.id, Category.name, Category.parent_id)
        )
    ).all()
    by_id = {cid: (name, pid) for cid, name, pid in rows}
    result: dict[str, str] = {}
    for cid in wanted:
        if cid not in by_id:
            continue
        chain: list[str] = []
        cur: str | None = cid
        seen: set[str] = set()
        while cur and cur in by_id and cur not in seen:
            seen.add(cur)
            name, pid = by_id[cur]
            chain.append(name)
            cur = pid
        chain.reverse()
        result[cid] = "/".join(chain)
    return result


# 向后兼容别名:历史名,value 实为分类路径(父/子)。老调用点(datasets/datasources/
# ingest_tasks 回填 categoryName)无需改动,展示自动从裸名升级为层级路径。
build_category_name_map = build_category_path_map


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


async def _child_count(session: AsyncSession, category_id: str) -> int:
    """直接子分类数(供删除守卫:有子则禁删)。"""
    return (
        await session.scalar(
            select(func.count())
            .select_from(Category)
            .where(Category.parent_id == category_id)
        )
        or 0
    )


def _is_self_or_descendant(
    parent_map: dict[str, str | None], node_id: str, candidate_id: str
) -> bool:
    """candidate 是否等于 node、或位于 node 的子树(node 是 candidate 的祖先或自身)。

    用于环检测:把 node 的 parent 改为 candidate 时,若 True 则会成环。
    parent_map = {id: parent_id};沿 candidate 的 parent 链上溯,遇 node 即 True。
    含 seen 防御坏数据成环。
    """
    cur: str | None = candidate_id
    seen: set[str] = set()
    while cur and cur not in seen:
        if cur == node_id:
            return True
        seen.add(cur)
        cur = parent_map.get(cur)
    return False


@router.get("/categories")
async def list_categories(session: SessionDep) -> JSONResponse:
    """列出全部分类,组装成嵌套树(同层按创建时间倒序)+ 各自 usageCount。

    所有登录用户可见。返回 roots 数组,每个节点的 children 为其直接子分类。
    """
    rows = (
        await session.scalars(
            select(Category).order_by(Category.created_at.desc())
        )
    ).all()
    counts = await _usage_counts(session)
    by_id: dict[str, CategoryRead] = {}
    for row in rows:
        item = CategoryRead.model_validate(row)
        item.usage_count = counts.get(row.id, 0)
        by_id[row.id] = item
    roots: list[CategoryRead] = []
    for row in rows:
        item = by_id[row.id]
        pid = row.parent_id
        if pid and pid in by_id:
            by_id[pid].children.append(item)
        else:
            roots.append(item)
    data = [r.model_dump(by_alias=True, mode="json") for r in roots]
    return JSONResponse(content={"data": data, "success": True})


@router.post("/categories", dependencies=[Depends(require_admin)])
async def create_category(
    body: CategoryCreate, session: SessionDep
) -> JSONResponse:
    """新建分类;上级不存在 404;同级 (parent_id,name) 重名 409。新建分类用量恒为 0。"""
    new_parent = body.parent_id or None
    if new_parent:
        parent = await session.get(Category, new_parent)
        if parent is None:
            return JSONResponse(
                status_code=404,
                content={"success": False, "message": "上级分类不存在"},
            )
    existing = await session.scalar(
        select(Category.id)
        .where(Category.name == body.name)
        .where(Category.parent_id.is_not_distinct_from(new_parent))
    )
    if existing is not None:
        return JSONResponse(
            status_code=409,
            content={"success": False, "message": "分类名已存在"},
        )
    category = Category(
        id=_new_id(),
        name=body.name,
        parent_id=new_parent,
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
    """改名/改备注/移父;404 缺失;上级不存在 404;同级重名 409;成环 409。"""
    category = await session.get(Category, category_id)
    if category is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "分类不存在"},
        )
    updates = body.model_dump(exclude_unset=True)

    # 移父:存在性 + 环检测(新上级不能是自身或自身后代)
    if "parent_id" in updates:
        new_parent = updates["parent_id"] or None
        if new_parent:
            if new_parent == category_id:
                return JSONResponse(
                    status_code=409,
                    content={"success": False, "message": "不能将上级设为自身"},
                )
            parent = await session.get(Category, new_parent)
            if parent is None:
                return JSONResponse(
                    status_code=404,
                    content={"success": False, "message": "上级分类不存在"},
                )
            parent_map = dict(
                (
                    await session.execute(
                        select(Category.id, Category.parent_id)
                    )
                ).all()
            )
            if _is_self_or_descendant(parent_map, category_id, new_parent):
                return JSONResponse(
                    status_code=409,
                    content={
                        "success": False,
                        "message": "不能将上级设为自身或子分类(会成环)",
                    },
                )

    # 改名:同级 (parent_id,name) 唯一;effective_parent 取本次更新值或原值
    if "name" in updates and updates["name"] is not None:
        effective_parent = updates.get("parent_id", category.parent_id) or None
        clash = await session.scalar(
            select(Category.id)
            .where(Category.name == updates["name"])
            .where(Category.parent_id.is_not_distinct_from(effective_parent))
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
    """删除分类:404 缺失;有子分类 409;被引用 409;空则删除成功。"""
    category = await session.get(Category, category_id)
    if category is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "分类不存在"},
        )
    children = await _child_count(session, category_id)
    if children > 0:
        return JSONResponse(
            status_code=409,
            content={
                "success": False,
                "message": f"分类有 {children} 个子分类,请先删除或迁移子分类",
            },
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
