"""数据湖路由：CRUD + 数据入湖 + 快照查询 + 本地文件归档。

数据湖是所有外部数据源的统一入口（ODS 层），原样接入、版本固化、血缘追踪。
"""

from __future__ import annotations

import asyncio
import datetime
import secrets
from pathlib import Path
from typing import Annotated, Any

import duckdb
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_user, require_admin, require_user
from app.core.db import get_session
from app.models.data_lake import DataLake, DataLakeObject, DataLakeSnapshot
from app.models.data_lake_acl import DataLakeAcl
from app.models.user import User
from app.schemas.common import CamelModel, PageResponse
from app.schemas.data_lake import (
    DataLakeCreate,
    DataLakeDetailRead,
    DataLakeObjectRead,
    DataLakeRead,
    DataLakeSnapshotRead,
    DataLakeUpdate,
    ExtractToDatasetRequest,
    LakeAclRead,
    LakeMergeRequest,
    SnapshotRenameRequest,
)
from app.schemas.dataset_acl import AclCreate, AclUpdate
from app.services import data_lake as data_lake_service
from app.services import lake_acl, lake_extract
from app.services.external_store import (
    MAX_MANIFEST_MEMBERS,
    ExternalStoreError,
    client_for,
    head_records,
    parse_s3_uri,
    platform_config,
    s3_settings_for_duckdb,
)
from app.services.landing import (
    BINARY_FORMATS,
    LANDABLE_FORMATS,
    LandingError,
    media_kind,
)

router = APIRouter(tags=["data-lakes"])


def _file_ext(filename: str) -> str:
    """从文件名取小写扩展名(不含点);无扩展返回空串。"""
    return Path(filename).suffix.lstrip(".").lower()


# 可走表格预览的结构化格式(与前端 PREVIEW_STRUCTURAL 对齐)。
# 说明:pdf/doc/docx/ppt/pptx 虽能被 normalize_to_records 文本抽取,但抽取重、
# 非表格语义,预览端一律拒绝(前端对这些走 kkFileView / 下载),避免大文档解析阻塞。
PREVIEW_STRUCTURAL_FORMATS = {
    "csv",
    "tsv",
    "xlsx",
    "xls",
    "json",
    "jsonl",
    "parquet",
    "txt",
    "log",
}


def _duck_safe(v: object) -> object:
    """把 DuckDB 返回值归一为 JSON 可序列化(Decimal/datetime/bytes → str)。"""
    if v is None or isinstance(v, (bool, int, float, str, list, dict)):
        return v
    return str(v)


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
    user: Annotated[User | None, Depends(current_user)],
    page: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, le=100),
    name: str | None = Query(None, description="名称模糊查询"),
) -> PageResponse[DataLakeRead]:
    """分页列出数据湖（多源汇聚容器）。登录用户按 owner+超管+ACL 授权过滤,匿名放行。"""
    query = select(DataLake)

    # 数据湖 ACL 可见性过滤(镜像 datasets 列表)
    query = await lake_acl.visible_lake_filter(query, db, user)

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
    user: Annotated[User | None, Depends(current_user)],
) -> DataLakeDetailRead:
    """获取数据湖详情（含快照列表）。登录用户受 ACL 约束(无 view 级→404),匿名放行。"""
    lake = await data_lake_service.get_lake_by_id(db, lake_id)
    if not lake or not await lake_acl.can_access(db, user, lake_id, "view"):
        raise HTTPException(status_code=404, detail="数据湖不存在")

    # 查询快照列表
    snapshots = await data_lake_service.list_lake_snapshots(db, lake_id)

    return DataLakeDetailRead(
        **DataLakeRead.model_validate(lake).model_dump(),
        snapshots=[DataLakeSnapshotRead.model_validate(s) for s in snapshots],
        my_level=await lake_acl.get_acl_level(db, user, lake_id),
    )


@router.patch("/data-lakes/{lake_id}", response_model=DataLakeRead)
async def update_data_lake(
    lake_id: str,
    body: DataLakeUpdate,
    db: SessionDep,
    user: Annotated[User, Depends(require_user)],
) -> DataLakeRead:
    """更新数据湖元数据:仅 owner/超管/ACL-edit+ 可改。"""
    lake = await data_lake_service.get_lake_by_id(db, lake_id)
    if not lake:
        raise HTTPException(status_code=404, detail="数据湖不存在")
    if not await lake_acl.can_access(db, user, lake_id, "edit"):
        raise HTTPException(
            status_code=403, detail={"success": False, "message": "无权限"}
        )

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
    user: Annotated[User, Depends(require_user)],
) -> dict[str, bool]:
    """删除数据湖（及其所有快照记录，物理文件保留）。

    仅 owner/creator/超管可删(销毁性操作不给 ACL-admin,与 datasets 一致)。
    """
    lake = await data_lake_service.get_lake_by_id(db, lake_id)
    if not lake:
        raise HTTPException(status_code=404, detail="数据湖不存在")
    if user.role != "admin" and user.id not in (lake.owner, lake.creator):
        raise HTTPException(
            status_code=403, detail={"success": False, "message": "无权限"}
        )
    await _purge_lake(db, lake_id)
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


@router.get(
    "/data-lakes/{lake_id}/objects", response_model=PageResponse[DataLakeObjectRead]
)
async def list_objects(
    lake_id: str,
    db: SessionDep,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    name: str | None = Query(None, description="展示名模糊查询"),
) -> PageResponse[DataLakeObjectRead]:
    """分页列出数据湖的文件(★新增三层模型:文件聚合其多个版本快照)。

    version_count/total_size/latest_rows 用一条按 object_id 分组的聚合子查询
    算出,避免列表页逐个文件再查一次快照。
    """
    lake = await data_lake_service.get_lake_by_id(db, lake_id)
    if not lake:
        raise HTTPException(status_code=404, detail="数据湖不存在")

    query = select(DataLakeObject).where(DataLakeObject.lake_id == lake_id)
    if name:
        query = query.where(DataLakeObject.display_name.ilike(f"%{name}%"))

    total_query = select(func.count()).select_from(query.subquery())
    total = (await db.execute(total_query)).scalar_one()

    query = query.order_by(DataLakeObject.updated_at.desc())
    query = query.limit(page_size).offset((page - 1) * page_size)
    result = await db.execute(query)
    objects = list(result.scalars().all())

    agg_query = (
        select(
            DataLakeSnapshot.object_id,
            func.count().label("version_count"),
            func.sum(DataLakeSnapshot.size).label("total_size"),
        )
        .where(DataLakeSnapshot.object_id.in_([o.id for o in objects]))
        .group_by(DataLakeSnapshot.object_id)
    )
    agg_rows = (await db.execute(agg_query)).all() if objects else []
    agg_by_object = {row.object_id: row for row in agg_rows}

    # latest_rows 单独一条查询按 id 批量取(不能并进上面的分组聚合:那条按
    # object_id 分组统计的是全部版本,这里只要最新版本一行的 rows)
    latest_snapshot_ids = [
        o.latest_snapshot_id for o in objects if o.latest_snapshot_id
    ]
    rows_by_snapshot: dict[str, int | None] = {}
    if latest_snapshot_ids:
        latest_query = select(DataLakeSnapshot.id, DataLakeSnapshot.rows).where(
            DataLakeSnapshot.id.in_(latest_snapshot_ids)
        )
        rows_by_snapshot = {
            row.id: row.rows for row in (await db.execute(latest_query)).all()
        }

    data: list[DataLakeObjectRead] = []
    for obj in objects:
        agg = agg_by_object.get(obj.id)
        data.append(
            DataLakeObjectRead(
                id=obj.id,
                lake_id=obj.lake_id,
                identity_key=obj.identity_key,
                display_name=obj.display_name,
                origin=obj.origin,
                data_category=obj.data_category,
                storage_format=obj.storage_format,
                latest_version_no=obj.latest_version_no,
                latest_snapshot_id=obj.latest_snapshot_id,
                merge_config=obj.merge_config,
                created_at=obj.created_at,
                updated_at=obj.updated_at,
                version_count=agg.version_count if agg else 0,
                total_size=int(agg.total_size) if agg and agg.total_size else None,
                latest_rows=rows_by_snapshot.get(obj.latest_snapshot_id),
            )
        )

    return PageResponse(data=data, total=total, success=True)


@router.get(
    "/data-lake-objects/{object_id}/versions",
    response_model=PageResponse[DataLakeSnapshotRead],
)
async def list_object_versions(
    object_id: str,
    db: SessionDep,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> PageResponse[DataLakeSnapshotRead]:
    """分页列出某文件的所有版本(按 version_no 倒序,最新版本在前)。"""
    total_query = (
        select(func.count())
        .select_from(DataLakeSnapshot)
        .where(DataLakeSnapshot.object_id == object_id)
    )
    total = (await db.execute(total_query)).scalar_one()

    query = (
        select(DataLakeSnapshot)
        .where(DataLakeSnapshot.object_id == object_id)
        .order_by(DataLakeSnapshot.version_no.desc())
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


@router.post("/data-lakes/{lake_id}/merge")
async def merge_lake_objects(
    lake_id: str,
    body: LakeMergeRequest,
    db: SessionDep,
    user: Annotated[User, Depends(require_user)],
) -> dict[str, Any]:
    """湖内合并:多个结构化文件的某个版本 union/join 成一个宽表文件的新版本。

    需 owner/超管/ACL-edit+。
    """
    if not await lake_acl.can_access(db, user, lake_id, "edit"):
        raise HTTPException(
            status_code=403, detail={"success": False, "message": "无权限"}
        )
    try:
        snapshot = await data_lake_service.merge_lake_objects(
            db,
            lake_id=lake_id,
            mode=body.mode,
            inputs=[item.model_dump() for item in body.inputs],
            join_keys=body.join_keys,
            name=body.name,
            target_object_id=body.target_object_id,
        )
    except ExternalStoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "data": {
            "objectId": snapshot.object_id,
            "snapshotId": snapshot.id,
            "versionNo": snapshot.version_no,
        },
        "success": True,
    }


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


@router.patch(
    "/data-lake-snapshots/{snapshot_id}", response_model=DataLakeSnapshotRead
)
async def rename_snapshot(
    snapshot_id: str,
    body: SnapshotRenameRequest,
    db: SessionDep,
    user: Annotated[User, Depends(require_user)],
) -> DataLakeSnapshotRead:
    """快照改名:更新 source_metadata.original_filename(展示名)。

    只改元数据,不动物理对象(storage_uri 不变);血缘字段(db_table 等)保留。
    展示/抽取取名优先级为 original_filename > db_table,故 DB 采集快照改名后
    也以新名字展示,而 db_table 血缘不受影响。
    """
    result = await db.execute(
        select(DataLakeSnapshot).where(DataLakeSnapshot.id == snapshot_id)
    )
    snapshot = result.scalar_one_or_none()
    if not snapshot:
        raise HTTPException(status_code=404, detail="快照不存在")
    # 快照挂在湖下,改名按所属湖的 ACL-edit 门控
    if not await lake_acl.can_access(db, user, snapshot.lake_id, "edit"):
        raise HTTPException(
            status_code=403, detail={"success": False, "message": "无权限"}
        )

    filename = body.filename.strip()
    if not filename:
        raise HTTPException(status_code=400, detail="文件名不能为空")
    if "/" in filename or "\\" in filename:
        raise HTTPException(status_code=400, detail="文件名不能包含路径分隔符")

    # JSONB 整体重新赋值以触发 SQLAlchemy 变更检测(原地改 dict 不会 flush)
    snapshot.source_metadata = {
        **(snapshot.source_metadata or {}),
        "original_filename": filename,
    }
    await db.commit()
    await db.refresh(snapshot)

    return DataLakeSnapshotRead.model_validate(snapshot)


@router.get("/data-lake-snapshots/{snapshot_id}/presigned-url")
async def get_snapshot_presigned_url(
    snapshot_id: str,
    db: SessionDep,
    expires: int = Query(default=3600, ge=60, le=86400),
) -> dict[str, Any]:
    """生成快照文件的 presigned URL(用于预览/下载)。

    - 有效期默认 1 小时(3600s),可指定 60s~24h。
    - 返回 `{url: str, filename: str, storageFormat: str}`,前端用于 kkFileView 预览
      或直接下载。
    """
    from fastapi import HTTPException

    result = await db.execute(
        select(DataLakeSnapshot).where(DataLakeSnapshot.id == snapshot_id)
    )
    snapshot = result.scalar_one_or_none()
    if not snapshot:
        raise HTTPException(status_code=404, detail="快照不存在")

    try:
        cfg = platform_config()
    except ExternalStoreError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    # 解析 S3 URI
    bucket, key = parse_s3_uri(snapshot.storage_uri)
    client = client_for(cfg)

    # 生成 presigned URL
    try:
        url = client.presigned_get_object(
            bucket, key, expires=datetime.timedelta(seconds=expires)
        )  # noqa: DTZ011 presigned 无需时区
    except Exception as exc:  # noqa: BLE001 MinIO 客户端异常统一上报
        raise HTTPException(
            status_code=500, detail=f"生成 presigned URL 失败: {exc}"
        ) from exc

    # DB 采集快照的 metadata 只有 db_table(无 original_filename),
    # 同样落到 source_version 兜底名,避免返回 filename=null
    filename = (
        snapshot.source_metadata.get("original_filename")
        if snapshot.source_metadata
        else None
    ) or f"{snapshot.source_version}.{snapshot.storage_format}"

    return {
        "url": url,
        "filename": filename,
        "storageFormat": snapshot.storage_format,
    }


@router.get("/data-lake-snapshots/{snapshot_id}/preview")
async def preview_snapshot(
    snapshot_id: str,
    db: SessionDep,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> JSONResponse:
    """预览快照数据(结构化文件走表格,二进制文件不支持预览)。

    - jsonl/csv/parquet/xlsx 等结构化文件:返回 {data:行,columns:列,total:总行数}
    - 二进制文件(mp4/jpg/pdf 等):返回空数据 + message 提示下载
    """
    result = await db.execute(
        select(DataLakeSnapshot).where(DataLakeSnapshot.id == snapshot_id)
    )
    snapshot = result.scalar_one_or_none()
    if not snapshot:
        raise HTTPException(status_code=404, detail="快照不存在")

    fmt = (snapshot.storage_format or "").lower()

    # 非结构化(图片/音视频/pdf/office 文档等):不走表格预览,前端用 presigned URL
    # 经浏览器 / kkFileView 渲染;这里直接返回空数据 + 提示,避免大文件解析阻塞。
    if fmt not in PREVIEW_STRUCTURAL_FORMATS:
        return JSONResponse(
            content={
                "data": [],
                "columns": [],
                "total": snapshot.rows or 0,
                "success": True,
                "message": "该格式不支持表格预览,请下载或使用文件预览查看",
            }
        )

    # 解析 S3 URI
    try:
        cfg = platform_config()
        bucket, key = parse_s3_uri(snapshot.storage_uri)
    except ExternalStoreError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    # parquet:走 DuckDB(类型保真),httpfs 直查 MinIO(免整对象下载)
    if fmt == "parquet":
        storage_uri = snapshot.storage_uri
        try:
            s3 = s3_settings_for_duckdb(cfg)
        except ExternalStoreError as exc:
            raise HTTPException(
                status_code=400, detail=f"读取 parquet 失败:{exc}"
            ) from exc

        def _duck_query() -> tuple[list[dict], list[str], int]:
            """DuckDB read_parquet + limit/offset 查询(httpfs 直查 s3://)。"""
            endpoint, use_ssl, ak, sk = s3
            conn = duckdb.connect()
            try:
                conn.execute("INSTALL httpfs; LOAD httpfs;")
                conn.execute(f"SET s3_endpoint='{endpoint}';")
                conn.execute("SET s3_url_style='path';")
                conn.execute(f"SET s3_use_ssl={'true' if use_ssl else 'false'};")
                conn.execute(f"SET s3_access_key_id='{ak}';")
                conn.execute(f"SET s3_secret_access_key='{sk}';")
                conn.execute(
                    f"CREATE VIEW t AS SELECT * FROM read_parquet('{storage_uri}')"
                )
                cur = conn.execute(
                    f"SELECT * FROM t LIMIT {limit} OFFSET {offset}"
                )
                columns = [desc[0] for desc in cur.description or []]
                fetched = cur.fetchall()
                rows = [
                    {columns[i]: _duck_safe(r[i]) for i in range(len(columns))}
                    for r in fetched
                ]
                return rows, columns, len(rows)
            finally:
                conn.close()

        try:
            rows, columns, count = await asyncio.to_thread(_duck_query)
        except Exception as exc:  # noqa: BLE001 DuckDB/httpfs 异常统一转 400
            raise HTTPException(
                status_code=400, detail=f"读取 parquet 失败:{exc}"
            ) from exc

        return JSONResponse(
            content={
                "data": rows,
                "columns": columns,
                "total": snapshot.rows or (offset + count),
                "success": True,
            }
        )

    # 文本/表格类(jsonl/csv/tsv/txt/log/xlsx/xls):走 head_records
    try:
        rows = await head_records(cfg, bucket, key, fmt, offset + limit)
        rows = rows[offset : offset + limit]

        # 提取列名
        columns = list(rows[0].keys()) if rows else []

        return JSONResponse(
            content={
                "data": rows,
                "columns": columns,
                "total": snapshot.rows or len(rows),
                "success": True,
            }
        )
    except (ExternalStoreError, LandingError) as exc:
        raise HTTPException(
            status_code=400, detail=f"读取快照失败:{exc}"
        ) from exc


@router.post("/data-lakes/{lake_id}/extract-to-dataset")
async def extract_to_dataset(
    lake_id: str,
    body: ExtractToDatasetRequest,
    db: SessionDep,
    user: Annotated[User, Depends(require_user)],
) -> dict[str, Any]:
    """从湖快照抽取生成数据集(治理改造契约地基,见 docs/数据治理.md §5)。

    数据湖 → 数据集的标准链路:
    - 目标数据集二选一:``dataset_name``(新建)或 ``dataset_id``(追加到已有,
      见 ExtractToDatasetRequest 校验)
    - 每个选中的快照作为一个表成员落进目标数据集(文件名 = 数据湖原始文件名)
    - 血缘字段(source_version/db_schema/db_table 等)自动注入到记录中
    - 语义类型:新建时自动推断(全 database → structured,含非 database →
      unstructured);追加到已有数据集时复用其既有 semantic_type
    """
    from app.services.external_store import ExternalStoreError
    from app.services.landing import DocSegmentOptions

    # 抽取会读湖数据并产出数据集,按源湖的 ACL-edit 门控
    if not await lake_acl.can_access(db, user, lake_id, "edit"):
        raise HTTPException(
            status_code=403, detail={"success": False, "message": "无权限"}
        )

    doc_segment = (
        DocSegmentOptions(**body.doc_segment.model_dump())
        if body.doc_segment
        else None
    )
    try:
        dataset = await lake_extract.extract_to_new_dataset(
            db,
            lake_id=lake_id,
            snapshot_ids=body.snapshot_ids,
            dataset_name=body.dataset_name,
            dataset_id=body.dataset_id,
            description=body.description,
            field_mapping=body.field_mapping,
            doc_segment=doc_segment,
        )
    except ExternalStoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "data": {"datasetId": dataset.id, "datasetName": dataset.name},
        "success": True,
    }


@router.post("/data-lakes/{lake_id}/local-upload")
async def local_upload_to_lake(
    lake_id: str,
    db: SessionDep,
    user: Annotated[User, Depends(require_user)],
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
    lake = await data_lake_service.get_lake_by_id(db, lake_id)
    if not lake:
        raise HTTPException(status_code=404, detail="数据湖不存在")
    if not await lake_acl.can_access(db, user, lake_id, "edit"):
        raise HTTPException(
            status_code=403, detail={"success": False, "message": "无权限"}
        )

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


# ---- 数据湖级 ACL(共享/成员权限)---------------------------------------------
# 镜像 datasets 的 ACL 端点:管理 (lake × subject × level) 授权条目;
# 需 admin 级(owner/creator/超管/ACL-admin)。主体仅 user/all(角色授权已取消)。


def _new_lake_acl_id() -> str:
    """生成形如 lac-<6位hex> 的 ACL 行主键。"""
    return f"lac-{secrets.token_hex(3)}"


def _lake_acl_payload(row: DataLakeAcl) -> dict:
    return LakeAclRead.model_validate(row).model_dump(by_alias=True, mode="json")


def _like_q(q: str) -> str:
    r"""转义 ILIKE 通配符(%/_/\),防止 q 被当成通配符导致全员目录枚举。"""
    return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


async def _require_lake_acl_admin(
    db: SessionDep, lake_id: str, user: User
) -> JSONResponse | None:
    """校验当前用户对该数据湖有 admin 级;返回 None 表示放行,否则返回 403/404 响应。"""
    if await db.get(DataLake, lake_id) is None:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "数据湖不存在"}
        )
    if not await lake_acl.can_access(db, user, lake_id, "admin"):
        return JSONResponse(
            status_code=403, content={"success": False, "message": "无权限"}
        )
    return None


@router.get("/data-lakes/{lake_id}/acl")
async def list_lake_acl(
    lake_id: str,
    db: SessionDep,
    user: Annotated[User, Depends(require_user)],
) -> JSONResponse:
    """列出数据湖的授权条目(需 admin 级);subjectName 批量解析显示名。"""
    denied = await _require_lake_acl_admin(db, lake_id, user)
    if denied is not None:
        return denied
    rows = (
        await db.scalars(
            select(DataLakeAcl)
            .where(DataLakeAcl.lake_id == lake_id)
            .order_by(DataLakeAcl.created_at)
        )
    ).all()
    # 批量解析主体显示名:user→display_name/username,all→固定文案
    name_map: dict[str, str] = {}
    user_ids = [r.subject_id for r in rows if r.subject_type == "user"]
    if user_ids:
        users = (await db.scalars(select(User).where(User.id.in_(user_ids)))).all()
        for u in users:
            name_map[u.id] = u.display_name or u.username

    def name_of(r: DataLakeAcl) -> str:
        if r.subject_type == "all":
            return "组织内所有人"
        return name_map.get(r.subject_id, r.subject_id)

    return JSONResponse(
        {
            "data": [
                {**_lake_acl_payload(r), "subjectName": name_of(r)} for r in rows
            ],
            "success": True,
        }
    )


@router.get("/data-lakes/{lake_id}/acl/candidates")
async def search_lake_acl_candidates(
    lake_id: str,
    db: SessionDep,
    user: Annotated[User, Depends(require_user)],
    q: str = "",
    type: str = "user",  # noqa: A002 - 与查询参数名一致
) -> JSONResponse:
    """模糊搜索可授权主体(仅用户);需 admin 级,避免泄露全员目录。"""
    denied = await _require_lake_acl_admin(db, lake_id, user)
    if denied is not None:
        return denied
    if type != "user":
        return JSONResponse(
            status_code=400, content={"success": False, "message": "type 非法"}
        )
    rows = (
        await db.scalars(
            select(User)
            .where(
                or_(
                    User.username.ilike(f"%{_like_q(q)}%", escape="\\"),
                    User.display_name.ilike(f"%{_like_q(q)}%", escape="\\"),
                )
            )
            .order_by(User.username)
            .limit(20)
        )
    ).all()
    data = [
        {"id": r.id, "name": r.display_name or r.username, "type": "user"}
        for r in rows
    ]
    return JSONResponse({"data": data, "success": True})


@router.post("/data-lakes/{lake_id}/acl")
async def add_lake_acl(
    lake_id: str,
    body: AclCreate,
    db: SessionDep,
    user: Annotated[User, Depends(require_user)],
) -> JSONResponse:
    """新增授权条目;重复授权 (lake,subject) → 409。"""
    denied = await _require_lake_acl_admin(db, lake_id, user)
    if denied is not None:
        return denied
    if body.subject_type not in ("user", "all") or body.level not in (
        "view",
        "edit",
        "admin",
    ):
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "subject_type/level 非法"},
        )
    if body.subject_type == "user" and not body.subject_id.strip():
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "subject_id 不能为空"},
        )
    # "组织内所有人" 每湖只此一行,subject_id 固定,忽略传入值
    subject_id = (
        lake_acl.ALL_SUBJECT_ID if body.subject_type == "all" else body.subject_id
    )
    exists = await db.scalar(
        select(DataLakeAcl.id).where(
            DataLakeAcl.lake_id == lake_id,
            DataLakeAcl.subject_type == body.subject_type,
            DataLakeAcl.subject_id == subject_id,
        )
    )
    if exists is not None:
        return JSONResponse(
            status_code=409,
            content={"success": False, "message": "该主体已授权,请用修改"},
        )
    row = DataLakeAcl(
        id=_new_lake_acl_id(),
        lake_id=lake_id,
        subject_type=body.subject_type,
        subject_id=subject_id,
        level=body.level,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return JSONResponse({"data": _lake_acl_payload(row), "success": True})


@router.put("/data-lakes/{lake_id}/acl/{acl_id}")
async def update_lake_acl(
    lake_id: str,
    acl_id: str,
    body: AclUpdate,
    db: SessionDep,
    user: Annotated[User, Depends(require_user)],
) -> JSONResponse:
    """修改授权级别。"""
    denied = await _require_lake_acl_admin(db, lake_id, user)
    if denied is not None:
        return denied
    if body.level not in ("view", "edit", "admin"):
        return JSONResponse(
            status_code=400, content={"success": False, "message": "level 非法"}
        )
    row = await db.get(DataLakeAcl, acl_id)
    if row is None or row.lake_id != lake_id:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "授权条目不存在"}
        )
    row.level = body.level
    await db.commit()
    await db.refresh(row)
    return JSONResponse({"data": _lake_acl_payload(row), "success": True})


@router.delete("/data-lakes/{lake_id}/acl/{acl_id}")
async def delete_lake_acl(
    lake_id: str,
    acl_id: str,
    db: SessionDep,
    user: Annotated[User, Depends(require_user)],
) -> JSONResponse:
    """删除授权条目。"""
    denied = await _require_lake_acl_admin(db, lake_id, user)
    if denied is not None:
        return denied
    row = await db.get(DataLakeAcl, acl_id)
    if row is None or row.lake_id != lake_id:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "授权条目不存在"}
        )
    await db.delete(row)
    await db.commit()
    return JSONResponse({"success": True})
