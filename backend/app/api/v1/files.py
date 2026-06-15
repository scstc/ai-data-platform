"""文件管理路由:管理平台 MinIO 对象存储(收编 BCC 文件管理)。

设计见 docs/plan/10-文件管理设计.md。
- 读(列桶/列目录/下载链接/预览):所有登录用户(require_user)。
- 写(上传/新建文件夹/删除/删目录):仅 admin(require_admin)。
- 未配置 STORAGE_MINIO_* → 统一 503,不报 500。
- 删除软保护:被托管数据集(origin='hosted')引用的对象/目录 → 409 拦截。
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Form, Query, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin, require_user
from app.core.db import get_session
from app.models.dataset_version import DatasetVersion
from app.schemas.file import FolderCreate, FolderDelete
from app.services import external_store
from app.services.external_store import ExternalStoreError
from app.services.landing import LANDABLE_FORMATS, LandingError

router = APIRouter(tags=["files"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _platform_config_or_503() -> tuple[dict[str, Any] | None, JSONResponse | None]:
    """取平台 MinIO config;未配置 → (None, 503 响应)。"""
    try:
        return external_store.platform_config(), None
    except ExternalStoreError as exc:
        return None, JSONResponse(
            status_code=503, content={"success": False, "message": str(exc)}
        )


def _store_error(exc: ExternalStoreError, status_code: int = 400) -> JSONResponse:
    """统一 S3 错误响应:{success:false,message}。"""
    return JSONResponse(
        status_code=status_code, content={"success": False, "message": str(exc)}
    )


def _columns_of(rows: list[dict[str, Any]]) -> list[str]:
    """从记录列表收集列名(保持首次出现顺序)。"""
    columns: list[str] = []
    for rec in rows:
        for key in rec:
            if key not in columns:
                columns.append(key)
    return columns


# ---------------------------------------------------------------------------
# 读(所有登录用户)
# ---------------------------------------------------------------------------
@router.get("/files/buckets", dependencies=[Depends(require_user)])
async def list_buckets() -> JSONResponse:
    """列出平台 MinIO 的全部桶名。"""
    cfg, err = _platform_config_or_503()
    if err is not None:
        return err
    try:
        buckets = await external_store.list_buckets(cfg)
    except ExternalStoreError as exc:
        return _store_error(exc)
    return JSONResponse(content={"data": buckets, "success": True})


@router.get("/files", dependencies=[Depends(require_user)])
async def list_files(
    bucket: Annotated[str, Query()],
    prefix: Annotated[str, Query()] = "",
) -> JSONResponse:
    """列单层目录:{folders:[name], files:[{key,name,size,lastModified}]}。"""
    cfg, err = _platform_config_or_503()
    if err is not None:
        return err
    try:
        result = await external_store.list_dir(cfg, bucket, prefix)
    except ExternalStoreError as exc:
        return _store_error(exc)
    return JSONResponse(content={"data": result, "success": True})


@router.get("/files/download-url", dependencies=[Depends(require_user)])
async def file_download_url(
    bucket: Annotated[str, Query()],
    key: Annotated[str, Query()],
) -> JSONResponse:
    """生成预签名下载 URL(有效期 10 分钟)。"""
    cfg, err = _platform_config_or_503()
    if err is not None:
        return err
    try:
        url = await external_store.presigned_get_url(cfg, bucket, key)
    except ExternalStoreError as exc:
        return _store_error(exc)
    return JSONResponse(content={"data": {"url": url}, "success": True})


@router.get("/files/preview", dependencies=[Depends(require_user)])
async def preview_file(
    bucket: Annotated[str, Query()],
    key: Annotated[str, Query()],
    limit: Annotated[int, Query(ge=1, le=500)] = 50,
) -> JSONResponse:
    """预览对象前 N 条(jsonl/csv 等可解析格式);不可解析格式返回提示。"""
    cfg, err = _platform_config_or_503()
    if err is not None:
        return err

    fmt = Path(key).suffix.lstrip(".").lower()
    if fmt not in LANDABLE_FORMATS:
        return JSONResponse(
            content={
                "data": {
                    "columns": [],
                    "data": [],
                    "message": "该文件类型不支持预览",
                },
                "success": True,
            }
        )
    try:
        rows = await external_store.head_records(cfg, bucket, key, fmt, limit)
    except ExternalStoreError as exc:
        return _store_error(exc)
    except LandingError:
        return JSONResponse(
            content={
                "data": {
                    "columns": [],
                    "data": [],
                    "message": "该文件类型不支持预览",
                },
                "success": True,
            }
        )
    return JSONResponse(
        content={
            "data": {"columns": _columns_of(rows), "data": rows},
            "success": True,
        }
    )


# ---------------------------------------------------------------------------
# 写(require_admin)
# ---------------------------------------------------------------------------
@router.post("/files/upload", dependencies=[Depends(require_admin)])
async def upload_file(
    bucket: Annotated[str, Form()],
    file: UploadFile,
    prefix: Annotated[str, Form()] = "",
) -> JSONResponse:
    """上传文件到 bucket/{prefix}{filename}(prefix 非空时补尾部 '/')。"""
    cfg, err = _platform_config_or_503()
    if err is not None:
        return err
    if prefix and not prefix.endswith("/"):
        prefix = prefix + "/"
    key = f"{prefix}{file.filename}"

    length = file.size
    if length is None:
        # size 不可用时读全量再上传(预览/管理场景对象通常不大)
        data = await file.read()
        stream: Any = io.BytesIO(data)
        length = len(data)
    else:
        stream = file.file
    try:
        await external_store.upload_object(
            cfg,
            bucket,
            key,
            stream,
            length,
            content_type=file.content_type or "application/octet-stream",
        )
    except ExternalStoreError as exc:
        return _store_error(exc)
    return JSONResponse(content={"data": {"key": key}, "success": True})


@router.post("/files/folder", dependencies=[Depends(require_admin)])
async def create_folder(body: FolderCreate) -> JSONResponse:
    """新建文件夹(零字节 prefix/ 标记)。"""
    cfg, err = _platform_config_or_503()
    if err is not None:
        return err
    try:
        await external_store.create_folder(
            cfg, body.bucket, f"{body.prefix}{body.name}"
        )
    except ExternalStoreError as exc:
        return _store_error(exc)
    return JSONResponse(content={"success": True})


async def _count_hosted_refs(
    session: AsyncSession, storage_uri_eq: str | None, storage_uri_like: str | None
) -> int:
    """统计被托管数据集引用的版本数(只读 dataset_versions,不改)。"""
    stmt = select(func.count()).select_from(DatasetVersion).where(
        DatasetVersion.origin == "hosted"
    )
    if storage_uri_eq is not None:
        stmt = stmt.where(DatasetVersion.storage_uri == storage_uri_eq)
    if storage_uri_like is not None:
        stmt = stmt.where(DatasetVersion.storage_uri.like(storage_uri_like))
    return await session.scalar(stmt) or 0


@router.delete("/files/object", dependencies=[Depends(require_admin)])
async def delete_object(
    session: SessionDep,
    bucket: Annotated[str, Query()],
    key: Annotated[str, Query()],
) -> JSONResponse:
    """删单对象:软保护(被托管引用→409)→ remove_object。"""
    cfg, err = _platform_config_or_503()
    if err is not None:
        return err
    n = await _count_hosted_refs(session, f"s3://{bucket}/{key}", None)
    if n > 0:
        return JSONResponse(
            status_code=409,
            content={
                "success": False,
                "message": f"该文件被 {n} 个托管数据集引用,无法删除",
            },
        )
    try:
        await external_store.remove_object(cfg, bucket, key)
    except ExternalStoreError as exc:
        return _store_error(exc)
    return JSONResponse(content={"success": True})


@router.post("/files/delete-folder", dependencies=[Depends(require_admin)])
async def delete_folder(body: FolderDelete, session: SessionDep) -> JSONResponse:
    """递归删文件夹:软保护(目录下任一对象被托管引用→409)→ remove_prefix。"""
    cfg, err = _platform_config_or_503()
    if err is not None:
        return err
    n = await _count_hosted_refs(
        session, None, f"s3://{body.bucket}/{body.prefix}%"
    )
    if n > 0:
        return JSONResponse(
            status_code=409,
            content={
                "success": False,
                "message": f"该目录下 {n} 个文件被托管数据集引用,无法删除",
            },
        )
    try:
        deleted = await external_store.remove_prefix(cfg, body.bucket, body.prefix)
    except ExternalStoreError as exc:
        return _store_error(exc)
    return JSONResponse(content={"data": {"deleted": deleted}, "success": True})
