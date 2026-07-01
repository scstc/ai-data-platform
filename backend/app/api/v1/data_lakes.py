"""数据湖路由：CRUD + 数据入湖 + 快照查询 + 本地文件归档。

数据湖是所有外部数据源的统一入口（ODS 层），原样接入、版本固化、血缘追踪。
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.core.db import get_session
from app.models.data_lake import DataLake, DataLakeSnapshot
from app.schemas.common import CamelModel, PageResponse
from app.schemas.data_lake import (
    DataLakeCreate,
    DataLakeDetailRead,
    DataLakeRead,
    DataLakeSnapshotRead,
    DataLakeUpdate,
    ExtractToDatasetRequest,
)
from app.services import data_lake as data_lake_service
from app.services import lake_extract
from app.services.external_store import MAX_MANIFEST_MEMBERS, ExternalStoreError
from app.services.landing import (
    BINARY_FORMATS,
    LANDABLE_FORMATS,
    media_kind,
)

router = APIRouter(tags=["data-lakes"])


def _file_ext(filename: str) -> str:
    """从文件名取小写扩展名(不含点);无扩展返回空串。"""
    return Path(filename).suffix.lstrip(".").lower()

SessionDep = Annotated[AsyncSession, Depends(get_session)]


class BatchDeleteRequest(CamelModel):
    """批量删除入参。"""

    ids: list[str]


async def _purge_lake(db: AsyncSession, lake_id: str) -> bool:
    """删除一个数据湖 + 所有快照记录(物理文件保留);不存在返回 False。

    不 commit——由调用方在批量场景下统一提交,减少多次事务开销。
    """
    lake = await data_lake_service.get_lake_by_id(db, lake_id)
    if not lake:
        return False
    for snapshot in await data_lake_service.list_lake_snapshots(db, lake_id):
        await db.delete(snapshot)
    await db.delete(lake)
    return True


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
    if not await _purge_lake(db, lake_id):
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="数据湖不存在")
    await db.commit()
    return {"success": True}


@router.post(
    "/data-lakes/batch-delete", dependencies=[Depends(require_admin)]
)
async def batch_delete_data_lakes(
    body: BatchDeleteRequest,
    db: SessionDep,
) -> dict[str, Any]:
    """批量删除数据湖（及其所有快照记录，物理文件保留）。

    不存在的 id 静默跳过,返回实际删除数量。契约与 datasets/batch-delete 一致:
    {data:{deleted:N}, success:true}。
    """
    deleted = 0
    for lake_id in body.ids:
        if await _purge_lake(db, lake_id):
            deleted += 1
    await db.commit()
    return {"data": {"deleted": deleted}, "success": True}


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


@router.post("/data-lakes/{lake_id}/extract-to-dataset")
async def extract_to_dataset(
    lake_id: str,
    body: ExtractToDatasetRequest,
    db: SessionDep,
    _admin: Annotated[None, Depends(require_admin)],
) -> dict[str, Any]:
    """从湖快照抽取生成新数据集(治理改造契约地基,见 docs/数据治理.md §5)。

    数据湖 → 数据集的标准链路:
    - 每个选中的快照作为一个表成员落进新数据集(表名 = source_version)
    - 血缘字段(source_version/db_schema/db_table 等)自动注入到记录中
    - 语义类型自动推断:全 database → structured,含非 database → unstructured
    """
    from app.services.external_store import ExternalStoreError

    try:
        dataset = await lake_extract.extract_to_new_dataset(
            db,
            lake_id=lake_id,
            snapshot_ids=body.snapshot_ids,
            dataset_name=body.dataset_name,
            description=body.description,
        )
    except ExternalStoreError as exc:
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "data": {"datasetId": dataset.id, "datasetName": dataset.name},
        "success": True,
    }


@router.post("/data-lakes/{lake_id}/local-upload")
async def local_upload_to_lake(
    lake_id: str,
    db: SessionDep,
    _admin: Annotated[None, Depends(require_admin)],
    files: Annotated[list[UploadFile], File()],
) -> dict[str, Any]:
    """本地文件归档到数据湖:一批文件 → 一批快照(一文件一快照,原格式存储)。

    **数据湖职责**:原样归档,不做解析。所有文件(csv/xlsx/mp4/jpg 等)都走
    `ingest_to_lake_raw` 按原格式存入 MinIO;`data_category` 根据扩展名映射:
    - csv/xlsx/jsonl/txt → "tabular"
    - pdf/docx/pptx/md → "document"
    - png/jpg/gif/bmp → "image"
    - mp3/wav/flac/m4a → "audio"
    - mp4/avi/mov/mkv → "video"
    - 其他 → "text"(兜底)

    上传后**不生成数据集**;用户后续到数据湖详情页勾快照走
    `POST /data-lakes/{lake_id}/extract-to-dataset` 生成数据集(抽取时才解析)。

    并发/批次冲突:`ingest_to_lake_raw` 内部依赖 `_next_batch_no + IntegrityError
    重试`,同湖同天同扩展名多次入湖批次自增。

    错误策略(MVP):任一文件失败即抛 400,已入库快照留在湖里,前端提示
    "部分文件已入湖,请到数据湖详情页手动清理"——比复杂的补偿事务更清晰。
    """
    from fastapi import HTTPException

    lake = await data_lake_service.get_lake_by_id(db, lake_id)
    if not lake:
        raise HTTPException(status_code=404, detail="数据湖不存在")

    if not files:
        raise HTTPException(status_code=400, detail="请至少选择一个文件")
    if len(files) > MAX_MANIFEST_MEMBERS:
        raise HTTPException(
            status_code=400,
            detail=f"一次最多上传 {MAX_MANIFEST_MEMBERS} 个文件",
        )

    # 先扫一遍扩展名,任一不支持就拒(避免半个批次已经入库)
    for f in files:
        ext = _file_ext(f.filename or "")
        if ext not in LANDABLE_FORMATS and ext not in BINARY_FORMATS:
            raise HTTPException(
                status_code=400,
                detail=f"文件 {f.filename} 的格式 .{ext} 不支持归档到数据湖",
            )

    snapshots: list[DataLakeSnapshot] = []
    for f in files:
        original_filename = f.filename or "unnamed"
        ext = _file_ext(original_filename)
        content = await f.read()

        # 根据扩展名映射 data_category(数据湖原样归档,不解析)
        if ext in BINARY_FORMATS:
            kind = media_kind(ext) or "text"
        elif ext in {"csv", "tsv", "xlsx", "xls", "jsonl", "json"}:
            kind = "tabular"
        elif ext in {"pdf", "docx", "pptx", "doc", "md", "txt"}:
            kind = "document"
        else:
            kind = "text"  # 兜底

        try:
            snapshot = await data_lake_service.ingest_to_lake_raw(
                db,
                lake_id=lake_id,
                file_content=content,
                original_filename=original_filename,
                data_category=kind,
                upload_channel="local",
            )
        except ExternalStoreError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"文件 {original_filename} 归档失败:{exc}",
            ) from exc

        snapshots.append(snapshot)

    return {
        "data": {
            "snapshots": [
                DataLakeSnapshotRead.model_validate(s).model_dump(by_alias=True)
                for s in snapshots
            ],
        },
        "success": True,
    }
