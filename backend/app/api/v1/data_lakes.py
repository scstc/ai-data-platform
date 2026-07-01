"""数据湖路由：CRUD + 数据入湖 + 快照查询。

数据湖是所有外部数据源的统一入口（ODS 层），原样接入、版本固化、血缘追踪。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.core.db import get_session
from app.models.data_lake import DataLake, DataLakeSnapshot
from app.schemas.common import PageResponse
from app.schemas.data_lake import (
    DataLakeCreate,
    DataLakeDetailRead,
    DataLakeRead,
    DataLakeSnapshotRead,
    DataLakeUpdate,
)
from app.services import data_lake as data_lake_service

router = APIRouter(tags=["data-lakes"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@router.post("/data-lakes", response_model=DataLakeRead)
async def create_data_lake(
    body: DataLakeCreate,
    db: SessionDep,
    _admin: Annotated[None, Depends(require_admin)],
) -> DataLakeRead:
    """创建数据湖容器。"""
    lake = await data_lake_service.create_data_lake(
        db,
        name=body.name,
        description=body.description,
    )
    return DataLakeRead.model_validate(lake)


@router.get("/data-lakes", response_model=PageResponse[DataLakeRead])
async def list_data_lakes(
    db: SessionDep,
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    name: str | None = Query(None, description="名称模糊查询"),
) -> PageResponse[DataLakeRead]:
    """分页列出数据湖（多源汇聚容器）。"""
    query = select(DataLake)

    # 名称模糊查询
    if name:
        query = query.where(DataLake.name.ilike(f"%{name}%"))

    # 总数
    total_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(total_query)).scalar_one()

    # 分页
    query = query.order_by(DataLake.created_at.desc())
    query = query.limit(page_size).offset((page - 1) * page_size)
    result = await db.execute(query)
    lakes = list(result.scalars().all())

    return PageResponse(
        data=[DataLakeRead.model_validate(lake) for lake in lakes],
        total=total,
        success=True,
    )


@router.get("/data-lakes/{lake_id}", response_model=DataLakeDetailRead)
async def get_data_lake(
    lake_id: str,
    db: SessionDep,
) -> DataLakeDetailRead:
    """获取数据湖详情（含快照列表）。"""
    lake = await data_lake_service.get_lake_by_id(db, lake_id)
    if not lake:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="数据湖不存在")

    # 查询快照列表
    snapshots = await data_lake_service.list_lake_snapshots(db, lake_id)

    return DataLakeDetailRead(
        **DataLakeRead.model_validate(lake).model_dump(),
        snapshots=[DataLakeSnapshotRead.model_validate(s) for s in snapshots],
    )


@router.patch("/data-lakes/{lake_id}", response_model=DataLakeRead)
async def update_data_lake(
    lake_id: str,
    body: DataLakeUpdate,
    db: SessionDep,
    _admin: Annotated[None, Depends(require_admin)],
) -> DataLakeRead:
    """更新数据湖元数据。"""
    lake = await data_lake_service.get_lake_by_id(db, lake_id)
    if not lake:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="数据湖不存在")

    # 更新字段
    if body.name is not None:
        lake.name = body.name
    if body.description is not None:
        lake.description = body.description

    await db.commit()
    await db.refresh(lake)

    return DataLakeRead.model_validate(lake)


@router.delete("/data-lakes/{lake_id}")
async def delete_data_lake(
    lake_id: str,
    db: SessionDep,
    _admin: Annotated[None, Depends(require_admin)],
) -> dict[str, bool]:
    """删除数据湖（及其所有快照记录，物理文件保留）。"""
    lake = await data_lake_service.get_lake_by_id(db, lake_id)
    if not lake:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="数据湖不存在")

    # 删除所有快照记录
    await db.execute(
        select(DataLakeSnapshot).where(DataLakeSnapshot.lake_id == lake_id)
    )
    snapshots = await data_lake_service.list_lake_snapshots(db, lake_id)
    for snapshot in snapshots:
        await db.delete(snapshot)

    # 删除数据湖
    await db.delete(lake)
    await db.commit()

    return {"success": True}


@router.get(
    "/data-lakes/{lake_id}/snapshots", response_model=PageResponse[DataLakeSnapshotRead]
)
async def list_snapshots(
    lake_id: str,
    db: SessionDep,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> PageResponse[DataLakeSnapshotRead]:
    """分页列出数据湖的快照（按创建时间倒序）。"""
    # 检查数据湖是否存在
    lake = await data_lake_service.get_lake_by_id(db, lake_id)
    if not lake:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="数据湖不存在")

    # 总数
    total_query = (
        select(func.count())
        .select_from(DataLakeSnapshot)
        .where(DataLakeSnapshot.lake_id == lake_id)
    )
    total = (await db.execute(total_query)).scalar_one()

    # 分页查询
    query = (
        select(DataLakeSnapshot)
        .where(DataLakeSnapshot.lake_id == lake_id)
        .order_by(DataLakeSnapshot.created_at.desc())
        .limit(page_size)
        .offset((page - 1) * page_size)
    )
    result = await db.execute(query)
    snapshots = list(result.scalars().all())

    return PageResponse(
        data=[DataLakeSnapshotRead.model_validate(s) for s in snapshots],
        total=total,
        success=True,
    )


@router.get("/data-lake-snapshots/{snapshot_id}", response_model=DataLakeSnapshotRead)
async def get_snapshot(
    snapshot_id: str,
    db: SessionDep,
) -> DataLakeSnapshotRead:
    """获取单个快照详情。"""
    result = await db.execute(
        select(DataLakeSnapshot).where(DataLakeSnapshot.id == snapshot_id)
    )
    snapshot = result.scalar_one_or_none()
    if not snapshot:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="快照不存在")

    return DataLakeSnapshotRead.model_validate(snapshot)
