"""数据集路由:上传落地、列表 /datasets、详情、元数据编辑、S3 托管(#18)、删除。"""

from __future__ import annotations

import io
import json
import secrets
import shutil
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.api.v1.categories import build_category_name_map
from app.core.config import settings
from app.core.db import get_session
from app.models.dataset import Dataset
from app.models.dataset_version import DatasetVersion
from app.models.datasource import DataSource
from app.models.job_input import JobInput
from app.schemas.common import CamelModel, PageResponse
from app.schemas.dataset import (
    DatasetDetailRead,
    DatasetMemberRead,
    DatasetRead,
    DatasetUpdate,
    DatasetVersionRead,
    HostS3Request,
    PlatformHostRequest,
)
from app.services.external_store import (
    MAX_MANIFEST_MEMBERS,
    MAX_MATERIALIZE_BYTES,
    ExternalStoreError,
    download_to_temp,
    head_records,
    parse_s3_uri,
    platform_config,
    presigned_get_url,
    remove_object,
    remove_prefix,
    stat_object,
    upload_object,
)
from app.services.landing import (
    BINARY_FORMATS,
    INGESTABLE_FORMATS,
    LANDABLE_FORMATS,
    MANIFEST_FORMAT,
    LandingError,
    UnsupportedFormatError,
    land_upload,
    land_upload_raw,
)

router = APIRouter(tags=["datasets"])

# 依赖别名(与其他路由同款,规避 ruff B008)
SessionDep = Annotated[AsyncSession, Depends(get_session)]
UploadFileDep = Annotated[UploadFile, File(...)]
NameForm = Annotated[str | None, Form()]
DataTypeForm = Annotated[str | None, Form()]
DescForm = Annotated[str | None, Form()]
CategoryIdForm = Annotated[str | None, Form(alias="categoryId")]
CreatedStartQuery = Annotated[datetime | None, Query(alias="createdStart")]
CreatedEndQuery = Annotated[datetime | None, Query(alias="createdEnd")]


class DatasetResult(CamelModel):
    """单个数据集详情响应:{data:DatasetDetail, success:true}。"""

    data: DatasetDetailRead
    success: bool = True


def _file_ext(filename: str) -> str:
    """取扩展名(小写、不含点);无扩展名返回空串。"""
    suffix = Path(filename).suffix
    return suffix[1:].lower() if suffix else ""


def _to_detail(
    dataset: Dataset, versions: list[DatasetVersion]
) -> DatasetDetailRead:
    """组装数据集详情(元信息 + 版本列表 + hosted 标志)。"""
    detail = DatasetDetailRead.model_validate(dataset)
    detail.versions = [DatasetVersionRead.model_validate(v) for v in versions]
    detail.hosted = any(v.origin == "hosted" for v in versions)
    return detail


@router.post("/datasets/upload")
async def upload_as_dataset(
    file: UploadFileDep,
    session: SessionDep,
    name: NameForm = None,
    data_type: DataTypeForm = None,
    description: DescForm = None,
    category_id: CategoryIdForm = None,
) -> JSONResponse:
    """本地上传连接器:文件 → 规范化 jsonl → 受管 Dataset(v1) + DatasetVersion。"""
    filename = file.filename or ""
    fmt = _file_ext(filename)
    content = await file.read()
    # 二进制类原样存(land_upload_raw),其余规范化落地(land_upload);
    # 两条路径的落地失败都收敛为 LandingError → 400(磁盘写失败/解析失败均不冒 500)
    try:
        if fmt in BINARY_FORMATS:
            dataset, version = await land_upload_raw(
                session,
                content=content,
                filename=filename,
                source_format=fmt,
                dataset_name=name,
                data_type=data_type,
                description=description,
            )
        else:
            dataset, version = await land_upload(
                session,
                content=content,
                filename=filename,
                source_format=fmt,
                dataset_name=name,
                data_type=data_type,
                description=description,
            )
    except UnsupportedFormatError:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": f"格式 .{fmt} 暂不支持落地;当前支持 "
                "jsonl/json/csv/tsv/txt/log/xlsx/xls/html/pdf/doc/docx/ppt/pptx "
                "及常见图像/音频/视频",
            },
        )
    except LandingError as exc:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": f"落地失败:{exc}"},
        )

    # 上传后挂分类(可空):land_upload 已 commit,这里补一次更新
    if category_id:
        dataset.category_id = category_id
        await session.commit()
        await session.refresh(dataset)

    detail = _to_detail(dataset, [version])
    if dataset.category_id:
        names = await build_category_name_map(session, [dataset.category_id])
        detail.category_name = names.get(dataset.category_id)
    payload = DatasetResult(data=detail)
    return JSONResponse(content=payload.model_dump(by_alias=True, mode="json"))


# 媒体批量接入:单文件体积上限(与 uploads.py / 前端 200MB 对齐)
_MAX_MEDIA_FILE_BYTES = 200 * 1024 * 1024

# 媒体批量接入:data_type → manifest 媒体字段 + dj 特殊 token(一个数据集一种模态)
_MEDIA_FIELD = {"image": "images", "audio": "audios", "video": "videos"}
_MEDIA_TOKEN = {
    "image": "<__dj__image>",
    "audio": "<__dj__audio>",
    "video": "<__dj__video>",
}

MediaFilesDep = Annotated[list[UploadFile], File()]


async def _version_storage_cfg(
    version: DatasetVersion, session: SessionDep
) -> dict | None:
    """解析版本访问对象存储凭证;平台未配置返回 None(调用方转 503)。"""
    if version.source_datasource_id:
        ds = await session.get(DataSource, version.source_datasource_id)
        return ds.config if ds is not None else None
    try:
        return platform_config()
    except ExternalStoreError:
        return None


async def _read_manifest_rows(cfg: dict, storage_uri: str) -> list[dict]:
    """下载 manifest 对象并解析为行列表(供成员列表/预览)。"""
    bucket, key = parse_s3_uri(storage_uri)
    raw = await download_to_temp(cfg, bucket, key)
    try:
        rows: list[dict] = []
        for ln in raw.read_text(encoding="utf-8").splitlines():
            if not ln.strip():
                continue
            try:
                rows.append(json.loads(ln))
            except json.JSONDecodeError as exc:
                # 清单损坏 → 转 ExternalStoreError(调用方 4xx),不冒 500
                raise ExternalStoreError(f"清单格式损坏:{exc}") from exc
        return rows
    finally:
        raw.unlink(missing_ok=True)


def _manifest_bytes(rows: list[dict]) -> bytes:
    """把 manifest 行序列化为 jsonl 字节(写回平台对象)。"""
    return (
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n"
    ).encode("utf-8")


async def _manifest_version_of(
    session: SessionDep, dataset_id: str
) -> DatasetVersion | None:
    """取数据集的可编辑 manifest 版本(媒体集只有一个 format=manifest 的源版本)。"""
    return (
        await session.scalars(
            select(DatasetVersion)
            .where(
                DatasetVersion.dataset_id == dataset_id,
                DatasetVersion.format == MANIFEST_FORMAT,
            )
            .order_by(DatasetVersion.version_no.desc())
        )
    ).first()


@router.post("/datasets/upload-media")
async def upload_media_as_dataset(
    files: MediaFilesDep,
    session: SessionDep,
    name: NameForm = None,
    data_type: DataTypeForm = None,
    category_id: CategoryIdForm = None,
) -> JSONResponse:
    """媒体批量接入:一批文件 → 传平台 MinIO → 生成**一个** manifest 数据集(一文件一行)。

    与 /datasets/upload(一文件一集)不同:整批只建一个数据集,版本是 manifest jsonl,
    可进 dj-process(物化时下载成员)。仅图/音/视频(同模态)。
    """
    field = _MEDIA_FIELD.get(data_type or "")
    token = _MEDIA_TOKEN.get(data_type or "")
    if field is None or token is None:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": "媒体批量接入仅支持 image / audio / video 类型",
            },
        )
    if not files:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "请至少选择一个文件"},
        )
    for f in files:
        if _file_ext(f.filename or "") not in BINARY_FORMATS:
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "message": f"文件 {f.filename} 不是支持的媒体格式",
                },
            )
    # 接入上限与物化上限对齐:超限的数据集永远无法加工,故在接入处就拦截
    if len(files) > MAX_MANIFEST_MEMBERS:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": f"一次最多接入 {MAX_MANIFEST_MEMBERS} 个文件",
            },
        )
    try:
        cfg = platform_config()
    except ExternalStoreError as exc:
        return JSONResponse(
            status_code=503, content={"success": False, "message": str(exc)}
        )

    bucket = settings.storage_minio_upload_bucket
    dataset_id = _new_dataset_id()
    # 任一步失败(对象写入/落库)都回收本数据集前缀,绝不留孤儿对象
    try:
        manifest_rows: list[dict] = []
        total_size = 0
        for idx, f in enumerate(files):
            content = await f.read()
            if len(content) > _MAX_MEDIA_FILE_BYTES:
                raise ValueError(f"文件 {f.filename} 超过单文件 200MB 上限")
            total_size += len(content)
            if total_size > MAX_MATERIALIZE_BYTES:
                raise ValueError("本批文件总体积超过上限,无法加工")
            fmt = _file_ext(f.filename or "")
            base = Path(f.filename or f"file{idx}").name  # 去路径,防 key 注入
            member_key = f"{dataset_id}/{idx:06d}-{base}"
            await upload_object(
                cfg,
                bucket,
                member_key,
                io.BytesIO(content),
                len(content),
                content_type=f.content_type or "application/octet-stream",
            )
            manifest_rows.append(
                {
                    field: [member_key],
                    "text": token,
                    "__member": {
                        "bucket": bucket,
                        "key": member_key,
                        "name": base,
                        "size": len(content),
                        "format": fmt,
                    },
                }
            )
        manifest_bytes = _manifest_bytes(manifest_rows)
        manifest_key = f"{dataset_id}/manifest.jsonl"
        await upload_object(
            cfg,
            bucket,
            manifest_key,
            io.BytesIO(manifest_bytes),
            len(manifest_bytes),
            content_type="application/x-ndjson",
        )

        dataset = Dataset(
            id=dataset_id,
            name=name or (Path(files[0].filename or "媒体数据集").name),
            data_type=data_type,
            category_id=category_id,
            owner="admin",
            creator="admin",
        )
        session.add(dataset)
        version = DatasetVersion(
            id=_new_version_id(),
            dataset_id=dataset_id,
            version_no=1,
            storage_uri=f"s3://{bucket}/{manifest_key}",
            format=MANIFEST_FORMAT,
            rows=len(files),
            size=total_size,
            origin="managed",
            source_datasource_id=None,
            note=f"媒体批量接入:{len(files)} 个文件",
        )
        session.add(version)
        await session.commit()
        await session.refresh(dataset)
        await session.refresh(version)
    except ValueError as exc:
        await _gc_manifest_objects((bucket, f"{dataset_id}/"))
        return JSONResponse(
            status_code=400, content={"success": False, "message": str(exc)}
        )
    except ExternalStoreError as exc:
        await _gc_manifest_objects((bucket, f"{dataset_id}/"))
        return JSONResponse(
            status_code=503,
            content={"success": False, "message": f"对象写入失败:{exc}"},
        )
    except Exception:
        await session.rollback()
        await _gc_manifest_objects((bucket, f"{dataset_id}/"))
        raise

    detail = _to_detail(dataset, [version])
    if dataset.category_id:
        names = await build_category_name_map(session, [dataset.category_id])
        detail.category_name = names.get(dataset.category_id)
    payload = DatasetResult(data=detail)
    return JSONResponse(content=payload.model_dump(by_alias=True, mode="json"))


@router.get("/dataset-versions/{version_id}/members")
async def list_version_members(
    version_id: str, session: SessionDep
) -> JSONResponse:
    """列出版本的成员文件:manifest 版本从 __member 取;其余版本回退为单一成员。"""
    version = await session.get(DatasetVersion, version_id)
    if version is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "版本不存在"},
        )

    if version.format != MANIFEST_FORMAT:
        bucket = ""
        key = version.storage_uri
        if version.origin == "hosted":
            try:
                bucket, key = parse_s3_uri(version.storage_uri)
            except ExternalStoreError:
                bucket, key = "", version.storage_uri
        member = DatasetMemberRead(
            name=Path(key).name,
            key=key,
            bucket=bucket,
            format=version.format,
            size=version.size,
        )
        return JSONResponse(
            content={
                "data": [member.model_dump(by_alias=True)],
                "success": True,
            }
        )

    cfg = await _version_storage_cfg(version, session)
    if cfg is None:
        return JSONResponse(
            status_code=503,
            content={"success": False, "message": "平台存储(MinIO)未配置"},
        )
    try:
        rows = await _read_manifest_rows(cfg, version.storage_uri)
    except ExternalStoreError as exc:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": f"读取清单失败:{exc}"},
        )
    members = [
        DatasetMemberRead(**m).model_dump(by_alias=True)
        for r in rows
        if isinstance((m := r.get("__member")), dict)
    ]
    return JSONResponse(content={"data": members, "success": True})


@router.get("/dataset-versions/{version_id}/member-url")
async def get_member_url(
    version_id: str,
    session: SessionDep,
    key: Annotated[str, Query()],
) -> JSONResponse:
    """生成成员对象的预签名 URL(供浏览器直连预览 图/音/视频/文本)。"""
    version = await session.get(DatasetVersion, version_id)
    if version is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "版本不存在"},
        )
    # 解析该版本自身对象的桶/键(manifest→uploads桶;单文件 hosted→其源桶;
    # managed 本地版本无 s3 位置)。
    own_bucket = ""
    own_key = ""
    try:
        own_bucket, own_key = parse_s3_uri(version.storage_uri)
    except ExternalStoreError:
        own_bucket, own_key = "", ""
    # 安全:只签名属于本数据集前缀的对象(manifest 成员)或该版本自身托管对象,
    # 避免签出任意桶内对象。
    allowed = key.startswith(f"{version.dataset_id}/") or (
        own_key != "" and key == own_key
    )
    if not allowed:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "非法的成员 key"},
        )
    if not own_bucket:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "该版本无对象存储位置,无法预览"},
        )
    cfg = await _version_storage_cfg(version, session)
    if cfg is None:
        return JSONResponse(
            status_code=503,
            content={"success": False, "message": "平台存储(MinIO)未配置"},
        )
    try:
        url = await presigned_get_url(cfg, own_bucket, key)
    except ExternalStoreError as exc:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": f"生成链接失败:{exc}"},
        )
    return JSONResponse(content={"data": {"url": url}, "success": True})


@router.post("/datasets/{dataset_id}/members")
async def add_dataset_members(
    dataset_id: str,
    files: MediaFilesDep,
    session: SessionDep,
) -> JSONResponse:
    """向 manifest 媒体集追加成员(原地编辑:写对象 + 追加清单行 + 更新版本计数)。"""
    dataset = await session.get(Dataset, dataset_id)
    version = await _manifest_version_of(session, dataset_id)
    if dataset is None or version is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "数据集不存在或不是可编辑的媒体集"},
        )
    field = _MEDIA_FIELD.get(dataset.data_type or "")
    token = _MEDIA_TOKEN.get(dataset.data_type or "")
    if field is None or token is None:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "该数据集不支持追加媒体文件"},
        )
    if not files:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "请至少选择一个文件"},
        )
    for f in files:
        if _file_ext(f.filename or "") not in BINARY_FORMATS:
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "message": f"文件 {f.filename} 不是支持的媒体格式",
                },
            )
    try:
        cfg = platform_config()
    except ExternalStoreError as exc:
        return JSONResponse(
            status_code=503, content={"success": False, "message": str(exc)}
        )
    bucket, manifest_key = parse_s3_uri(version.storage_uri)
    try:
        rows = await _read_manifest_rows(cfg, version.storage_uri)
    except ExternalStoreError as exc:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": f"读取清单失败:{exc}"},
        )
    if len(rows) + len(files) > MAX_MANIFEST_MEMBERS:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": f"成员数将超过上限 {MAX_MANIFEST_MEMBERS}",
            },
        )

    added_keys: list[str] = []
    added_size = 0
    try:
        for f in files:
            content = await f.read()
            if len(content) > _MAX_MEDIA_FILE_BYTES:
                raise ValueError(f"文件 {f.filename} 超过单文件 200MB 上限")
            if (version.size or 0) + added_size + len(content) > MAX_MATERIALIZE_BYTES:
                raise ValueError("数据集总体积将超过上限")
            fmt = _file_ext(f.filename or "")
            base = Path(f.filename or "file").name
            # 追加成员用随机前缀,避免与现有(可能已删出空档的)序号键冲突
            member_key = f"{dataset_id}/{secrets.token_hex(4)}-{base}"
            await upload_object(
                cfg,
                bucket,
                member_key,
                io.BytesIO(content),
                len(content),
                content_type=f.content_type or "application/octet-stream",
            )
            added_keys.append(member_key)
            added_size += len(content)
            rows.append(
                {
                    field: [member_key],
                    "text": token,
                    "__member": {
                        "bucket": bucket,
                        "key": member_key,
                        "name": base,
                        "size": len(content),
                        "format": fmt,
                    },
                }
            )
        body = _manifest_bytes(rows)
        await upload_object(
            cfg,
            bucket,
            manifest_key,
            io.BytesIO(body),
            len(body),
            content_type="application/x-ndjson",
        )
    except (ValueError, ExternalStoreError) as exc:
        for k in added_keys:  # 回收本次新写对象,清单未改、保持一致
            try:
                await remove_object(cfg, bucket, k)
            except ExternalStoreError:
                pass
        code = 400 if isinstance(exc, ValueError) else 503
        return JSONResponse(
            status_code=code, content={"success": False, "message": str(exc)}
        )

    version.rows = len(rows)
    version.size = (version.size or 0) + added_size
    await session.commit()
    return JSONResponse(content={"data": {"rows": len(rows)}, "success": True})


@router.delete("/datasets/{dataset_id}/members")
async def delete_dataset_member(
    dataset_id: str,
    session: SessionDep,
    key: Annotated[str, Query()],
) -> JSONResponse:
    """从 manifest 媒体集移除一个成员(删对象 + 去清单行 + 更新版本计数)。"""
    version = await _manifest_version_of(session, dataset_id)
    if version is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "数据集不存在或不是可编辑的媒体集"},
        )
    if not key.startswith(f"{dataset_id}/"):
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "非法的成员 key"},
        )
    try:
        cfg = platform_config()
    except ExternalStoreError as exc:
        return JSONResponse(
            status_code=503, content={"success": False, "message": str(exc)}
        )
    bucket, manifest_key = parse_s3_uri(version.storage_uri)
    try:
        rows = await _read_manifest_rows(cfg, version.storage_uri)
    except ExternalStoreError as exc:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": f"读取清单失败:{exc}"},
        )
    kept = [r for r in rows if (r.get("__member") or {}).get("key") != key]
    if len(kept) == len(rows):
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "成员不存在"},
        )
    removed_size = sum(
        (m.get("size") or 0)
        for r in rows
        if (m := r.get("__member")) and m.get("key") == key
    )
    # 先删对象(失败也继续从清单移除,避免清单与对象长期不一致)
    try:
        await remove_object(cfg, bucket, key)
    except ExternalStoreError:
        pass
    try:
        body = _manifest_bytes(kept)
        await upload_object(
            cfg,
            bucket,
            manifest_key,
            io.BytesIO(body),
            len(body),
            content_type="application/x-ndjson",
        )
    except ExternalStoreError as exc:
        return JSONResponse(
            status_code=503,
            content={"success": False, "message": f"更新清单失败:{exc}"},
        )
    version.rows = len(kept)
    version.size = max(0, (version.size or 0) - removed_size)
    await session.commit()
    return JSONResponse(content={"data": {"rows": len(kept)}, "success": True})


@router.get("/datasets", response_model=PageResponse[DatasetRead])
async def list_datasets(
    session: SessionDep,
    current: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, alias="pageSize"),
    name: str | None = Query(None),
    data_type: str | None = Query(None, alias="dataType"),
    category_id: str | None = Query(None, alias="categoryId"),
    creator: str | None = Query(None),
    created_start: CreatedStartQuery = None,
    created_end: CreatedEndQuery = None,
    publish_status: str | None = Query(None, alias="publishStatus"),
) -> PageResponse[DatasetRead]:
    """分页查询数据集,按创建时间倒序;按元数据条件过滤(向后兼容)。

    publishStatus=published 时只返回含已发布版本的数据集(算法工程师消费视图)。
    """
    conds = []
    if name:
        conds.append(Dataset.name.ilike(f"%{name}%"))
    if data_type:
        conds.append(Dataset.data_type == data_type)
    if category_id:
        conds.append(Dataset.category_id == category_id)
    if creator:
        conds.append(Dataset.creator.ilike(f"%{creator}%"))
    if created_start is not None:
        conds.append(Dataset.created_at >= created_start)
    if created_end is not None:
        conds.append(Dataset.created_at <= created_end)
    # 算法工程师消费视图:publishStatus=published → 只返回含已发布版本的数据集
    if publish_status == "published":
        published_ds_ids = (
            await session.scalars(
                select(DatasetVersion.dataset_id)
                .where(DatasetVersion.publish_status == "published")
                .distinct()
            )
        ).all()
        conds.append(Dataset.id.in_(published_ds_ids))
    total = await session.scalar(
        select(func.count()).select_from(Dataset).where(*conds)
    )
    offset = (current - 1) * page_size
    rows = (
        await session.scalars(
            select(Dataset)
            .where(*conds)
            .order_by(Dataset.created_at.desc())
            .offset(offset)
            .limit(page_size)
        )
    ).all()
    # 一次查出本页中含 hosted 版本的数据集 id,供前端徽标/删除门控(#18)
    page_ids = [r.id for r in rows]
    hosted_ids: set[str] = set()
    if page_ids:
        hosted_ids = set(
            (
                await session.scalars(
                    select(DatasetVersion.dataset_id)
                    .where(DatasetVersion.dataset_id.in_(page_ids))
                    .where(DatasetVersion.origin == "hosted")
                    .distinct()
                )
            ).all()
        )
    # 批量取本页分类名(避免 N+1),回填 categoryName
    cat_names = await build_category_name_map(
        session, [r.category_id for r in rows]
    )
    data = []
    for r in rows:
        item = DatasetRead.model_validate(r)
        item.hosted = r.id in hosted_ids
        if r.category_id:
            item.category_name = cat_names.get(r.category_id)
        data.append(item)
    return PageResponse[DatasetRead](data=data, total=total or 0)


@router.get("/datasets/{dataset_id}")
async def get_dataset(dataset_id: str, session: SessionDep) -> JSONResponse:
    """数据集详情:元信息 + 版本列表(按版本号升序)。"""
    dataset = await session.get(Dataset, dataset_id)
    if dataset is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "数据集不存在"},
        )
    versions = (
        await session.scalars(
            select(DatasetVersion)
            .where(DatasetVersion.dataset_id == dataset_id)
            .order_by(DatasetVersion.version_no)
        )
    ).all()
    detail = _to_detail(dataset, list(versions))
    if dataset.category_id:
        names = await build_category_name_map(session, [dataset.category_id])
        detail.category_name = names.get(dataset.category_id)
    payload = DatasetResult(data=detail)
    return JSONResponse(content=payload.model_dump(by_alias=True, mode="json"))


@router.patch("/datasets/{dataset_id}", dependencies=[Depends(require_admin)])
async def update_dataset(
    dataset_id: str, body: DatasetUpdate, session: SessionDep
) -> JSONResponse:
    """编辑数据集可变元数据:只更新传入字段,记录变更人。"""
    dataset = await session.get(Dataset, dataset_id)
    if dataset is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "数据集不存在"},
        )
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(dataset, field, value)
    dataset.last_modifier = "admin"
    await session.commit()
    await session.refresh(dataset)
    versions = (
        await session.scalars(
            select(DatasetVersion)
            .where(DatasetVersion.dataset_id == dataset_id)
            .order_by(DatasetVersion.version_no)
        )
    ).all()
    detail = _to_detail(dataset, list(versions))
    if dataset.category_id:
        names = await build_category_name_map(session, [dataset.category_id])
        detail.category_name = names.get(dataset.category_id)
    payload = DatasetResult(data=detail)
    return JSONResponse(content=payload.model_dump(by_alias=True, mode="json"))


async def _has_hosted_version(session: AsyncSession, dataset_id: str) -> bool:
    """数据集是否含 hosted 版本(#18 删除门控:含 hosted 禁止删除)。"""
    hit = await session.scalar(
        select(DatasetVersion.id)
        .where(DatasetVersion.dataset_id == dataset_id)
        .where(DatasetVersion.origin == "hosted")
        .limit(1)
    )
    return hit is not None


async def _purge_dataset(session: AsyncSession, dataset_id: str) -> bool:
    """暂存删除一个数据集(版本 + 血缘边 + 元数据),不 commit;返回是否命中。

    注意:本函数只删平台记录,**绝不调用任何 S3 删除**(部署红线,#18)。
    含 hosted 版本时由调用方先行拦截(delete/batch-delete 返回 403),unhost 才允许。
    """
    dataset = await session.get(Dataset, dataset_id)
    if dataset is None:
        return False
    version_ids = (
        await session.scalars(
            select(DatasetVersion.id).where(
                DatasetVersion.dataset_id == dataset_id
            )
        )
    ).all()
    if version_ids:
        await session.execute(
            delete(JobInput).where(JobInput.dataset_version_id.in_(version_ids))
        )
    await session.execute(
        delete(DatasetVersion).where(DatasetVersion.dataset_id == dataset_id)
    )
    await session.delete(dataset)
    return True


def _rmdir(dataset_id: str) -> None:
    """删磁盘产物目录(不存在则忽略)。"""
    shutil.rmtree(Path(settings.datasets_dir) / dataset_id, ignore_errors=True)


async def _manifest_gc_target(
    session: SessionDep, dataset_id: str
) -> tuple[str, str] | None:
    """数据集含 manifest 版本(平台自有媒体)→ 返回 (bucket, prefix) 供删除时回收对象。"""
    v = (
        await session.scalars(
            select(DatasetVersion).where(
                DatasetVersion.dataset_id == dataset_id,
                DatasetVersion.format == MANIFEST_FORMAT,
            )
        )
    ).first()
    if v is None:
        return None
    try:
        bucket, _ = parse_s3_uri(v.storage_uri)
    except ExternalStoreError:
        return None
    return bucket, f"{dataset_id}/"


async def _gc_manifest_objects(target: tuple[str, str] | None) -> None:
    """尽力回收 manifest 数据集在平台 MinIO 的对象;平台未配/不可达时不阻断删除。"""
    if target is None:
        return
    try:
        await remove_prefix(platform_config(), target[0], target[1])
    except ExternalStoreError:
        pass  # DB 行已删;残留对象由后续清理,不让 GC 失败阻断删除


class BatchDeleteRequest(CamelModel):
    """批量删除入参。"""

    ids: list[str]


# 外部托管数据禁止删除的统一文案(#18:删源是部署红线)
_HOSTED_DELETE_MSG = "外部托管数据不支持删除,请用取消托管"


@router.delete("/datasets/{dataset_id}", dependencies=[Depends(require_admin)])
async def delete_dataset(dataset_id: str, session: SessionDep) -> JSONResponse:
    """删除数据集:级联删版本 + 清血缘边(job_inputs)+ 删磁盘产物。

    含 hosted 版本 → 403 拒绝(#18:删源禁止,请走取消托管),绝不动 S3。
    """
    if await _has_hosted_version(session, dataset_id):
        return JSONResponse(
            status_code=403,
            content={"success": False, "message": _HOSTED_DELETE_MSG},
        )
    gc = await _manifest_gc_target(session, dataset_id)
    if not await _purge_dataset(session, dataset_id):
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "数据集不存在"},
        )
    await session.commit()
    _rmdir(dataset_id)
    await _gc_manifest_objects(gc)
    return JSONResponse(content={"success": True})


@router.post(
    "/datasets/batch-delete", dependencies=[Depends(require_admin)]
)
async def batch_delete_datasets(
    body: BatchDeleteRequest, session: SessionDep
) -> JSONResponse:
    """批量删除数据集,返回实际删除数量。

    若任一目标含 hosted 版本 → 整批 403 拒绝(#18:删源禁止),绝不动 S3、不部分删。
    """
    for ds_id in body.ids:
        if await _has_hosted_version(session, ds_id):
            return JSONResponse(
                status_code=403,
                content={"success": False, "message": _HOSTED_DELETE_MSG},
            )
    deleted: list[str] = []
    gc_targets: list[tuple[str, str]] = []
    for ds_id in body.ids:
        gc = await _manifest_gc_target(session, ds_id)
        if await _purge_dataset(session, ds_id):
            deleted.append(ds_id)
            if gc is not None:
                gc_targets.append(gc)
    await session.commit()
    for ds_id in deleted:
        _rmdir(ds_id)
    for target in gc_targets:
        await _gc_manifest_objects(target)
    return JSONResponse(
        content={"data": {"deleted": len(deleted)}, "success": True}
    )


def _columns_of(rows: list[dict]) -> list[str]:
    """按出现顺序求多行的键并集(首行键序优先,再补后续行新键)。"""
    columns: list[str] = []
    for rec in rows:
        for key in rec:
            if key not in columns:
                columns.append(key)
    return columns


@router.get("/dataset-versions/{version_id}/preview")
async def preview_version(
    version_id: str,
    session: SessionDep,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> JSONResponse:
    """预览某版本的数据(读 jsonl,分页返回若干行 + 列名 + 总行数)。

    hosted 版本走 head_records(按需从 S3 取前 N,避免整对象下载缓存);
    受管版本读本地 jsonl。
    """
    version = await session.get(DatasetVersion, version_id)
    if version is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "版本不存在"},
        )

    # manifest(媒体集):返回成员清单表(name/format/size),不走 normalize_to_records
    if version.format == MANIFEST_FORMAT:
        cfg = await _version_storage_cfg(version, session)
        if cfg is None:
            return JSONResponse(
                status_code=503,
                content={"success": False, "message": "平台存储(MinIO)未配置"},
            )
        try:
            rows = await _read_manifest_rows(cfg, version.storage_uri)
        except ExternalStoreError as exc:
            return JSONResponse(
                status_code=400,
                content={"success": False, "message": f"读取清单失败:{exc}"},
            )
        members = [
            {"name": m.get("name"), "format": m.get("format"), "size": m.get("size")}
            for r in rows
            if isinstance((m := r.get("__member")), dict)
        ]
        return JSONResponse(
            content={
                "data": members[offset : offset + limit],
                "columns": ["name", "format", "size"],
                "total": version.rows or len(members),
                "success": True,
            }
        )

    # 二进制类(图像/音视频)原样存储,不解析:预览置灰,仅下载
    if version.format in BINARY_FORMATS:
        return JSONResponse(
            content={
                "data": [],
                "columns": [],
                "total": version.rows or 0,
                "success": True,
                "message": "二进制文件不支持预览,请下载查看",
            }
        )

    # hosted:按需从 S3 取前 offset+limit 条再切片(预览成本由取前 N 缓解)
    if version.origin == "hosted":
        if version.source_datasource_id:
            ds = await session.get(DataSource, version.source_datasource_id)
            if ds is None:
                return JSONResponse(
                    status_code=400,
                    content={
                        "success": False,
                        "message": "托管版本对应的数据源已不存在",
                    },
                )
            cfg = ds.config
        else:
            # 平台对象零拷贝接入:用平台 MinIO 凭证回退
            try:
                cfg = platform_config()
            except ExternalStoreError as exc:
                return JSONResponse(
                    status_code=503,
                    content={"success": False, "message": str(exc)},
                )
        try:
            bucket, key = parse_s3_uri(version.storage_uri)
            head = await head_records(
                cfg, bucket, key, version.format, offset + limit
            )
        except ExternalStoreError as exc:
            return JSONResponse(
                status_code=400,
                content={"success": False, "message": f"读取 S3 对象失败:{exc}"},
            )
        rows = head[offset : offset + limit]
        return JSONResponse(
            content={
                "data": rows,
                "columns": _columns_of(rows),
                "total": version.rows or 0,
                "success": True,
            }
        )

    path = Path(version.storage_uri)
    if not path.exists():
        return JSONResponse(
            content={
                "data": [],
                "columns": [],
                "total": version.rows or 0,
                "success": True,
                "message": "产物文件缺失",
            }
        )
    rows: list[dict] = []
    with path.open(encoding="utf-8") as fp:
        for idx, line in enumerate(fp):
            if idx < offset:
                continue
            if len(rows) >= limit:
                break
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return JSONResponse(
        content={
            "data": rows,
            "columns": _columns_of(rows),
            "total": version.rows or 0,
            "success": True,
        }
    )


# ---------------------------------------------------------------------------
# 外部 S3 数据托管:登记 / 取消(#18)
# ---------------------------------------------------------------------------
def _new_dataset_id() -> str:
    """形如 ``dset-`` + 6 位 hex(与 landing 同款)。"""
    return f"dset-{secrets.token_hex(3)}"


def _new_version_id() -> str:
    """形如 ``dsv-`` + 6 位 hex(与 landing 同款)。"""
    return f"dsv-{secrets.token_hex(3)}"


@router.post("/datasets/host-s3")
async def host_s3(body: HostS3Request, session: SessionDep) -> JSONResponse:
    """外部 S3 数据托管登记(#18):把若干 S3 对象登记为受管数据集版本,**不下载**。

    逐 key:stat_object 取 size、由扩展名定 format → 建 Dataset + DatasetVersion
    (origin='hosted',storage_uri='s3://bucket/key',source_datasource_id,
    rows=null)。不加 require_admin(类比 upload,登记不破坏数据)。返回创建的数据集列表。
    """
    if not body.keys:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "请至少选择一个对象"},
        )
    ds = await session.get(DataSource, body.datasource_id)
    if ds is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "数据源不存在"},
        )
    if ds.type != "s3":
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "仅支持 s3 类型数据源托管"},
        )

    pairs: list[tuple[Dataset, DatasetVersion]] = []
    # 单 key 命名取对象文件名;多 key 时按对象名分别命名(name 作前缀)
    multiple = len(body.keys) > 1
    for key in body.keys:
        fmt = _file_ext(key)
        if fmt not in LANDABLE_FORMATS:
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "message": f"对象 {key} 的格式 .{fmt} 暂不支持托管;当前支持 "
                    "jsonl/json/csv/tsv/txt/xlsx/xls/html/pdf/doc/docx/ppt/pptx",
                },
            )
        try:
            meta = await stat_object(ds.config, body.bucket, key)
        except ExternalStoreError as exc:
            return JSONResponse(
                status_code=400,
                content={"success": False, "message": f"读取 S3 对象失败:{exc}"},
            )

        base_name = body.name or Path(key).stem or "未命名托管数据集"
        ds_name = f"{base_name}/{Path(key).name}" if multiple else base_name
        dataset = Dataset(
            id=_new_dataset_id(),
            name=ds_name,
            data_type=body.data_type,
            category_id=body.category_id,
            owner="admin",
            creator="admin",
        )
        session.add(dataset)
        version = DatasetVersion(
            id=_new_version_id(),
            dataset_id=dataset.id,
            version_no=1,
            storage_uri=f"s3://{body.bucket}/{key}",
            format=fmt,
            rows=None,
            size=meta.get("size"),
            origin="hosted",
            source_datasource_id=ds.id,
            note=f"外部 S3 托管登记:s3://{body.bucket}/{key}",
        )
        session.add(version)
        pairs.append((dataset, version))

    # 先提交 + refresh,再组装详情(server_default 的 created_at/updated_at 才有值)
    await session.commit()
    cat_name = None
    if body.category_id:
        names = await build_category_name_map(session, [body.category_id])
        cat_name = names.get(body.category_id)
    created: list[DatasetDetailRead] = []
    for dataset, version in pairs:
        await session.refresh(dataset)
        await session.refresh(version)
        detail = _to_detail(dataset, [version])
        if dataset.category_id:
            detail.category_name = cat_name
        created.append(detail)
    return JSONResponse(
        content={
            "data": [d.model_dump(by_alias=True, mode="json") for d in created],
            "success": True,
        }
    )


@router.post("/datasets/host-platform")
async def host_platform(
    body: PlatformHostRequest, session: SessionDep
) -> JSONResponse:
    """文件管理零拷贝接入:把平台 MinIO 若干对象登记为受管数据集版本,**不下载**。

    用 platform_config() 取平台存储凭证(而非数据源);source_datasource_id 留空,
    预览/物化时由 platform_config 回退定位(见 preview / materialized_version)。
    """
    if not body.keys:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "请至少选择一个对象"},
        )
    if not body.bucket:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "缺少存储桶"},
        )
    # 先整批校验所有 key 的格式;任一非法即整批 400(此时尚未 stat、未 add session,无脏状态)
    for key in body.keys:
        fmt = _file_ext(key)
        if fmt not in INGESTABLE_FORMATS:
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "message": f"对象 {key} 的格式 .{fmt} 暂不支持接入",
                },
            )
    try:
        cfg = platform_config()
    except ExternalStoreError as exc:
        return JSONResponse(
            status_code=503, content={"success": False, "message": str(exc)}
        )

    pairs: list[tuple[Dataset, DatasetVersion]] = []
    multiple = len(body.keys) > 1
    for key in body.keys:
        fmt = _file_ext(key)
        try:
            meta = await stat_object(cfg, body.bucket, key)
        except ExternalStoreError as exc:
            # 中途某对象 stat 失败:回滚本批已 add 的 pending 对象,整批 400(绝不部分登记)
            await session.rollback()
            return JSONResponse(
                status_code=400,
                content={"success": False, "message": f"读取平台对象失败:{exc}"},
            )

        base_name = body.name or Path(key).stem or "未命名接入数据集"
        ds_name = f"{base_name}/{Path(key).name}" if multiple else base_name
        dataset = Dataset(
            id=_new_dataset_id(),
            name=ds_name,
            data_type=body.data_type,
            category_id=body.category_id,
            owner="admin",
            creator="admin",
        )
        session.add(dataset)
        version = DatasetVersion(
            id=_new_version_id(),
            dataset_id=dataset.id,
            version_no=1,
            storage_uri=f"s3://{body.bucket}/{key}",
            format=fmt,
            rows=None,
            size=meta.get("size"),
            origin="hosted",
            source_datasource_id=None,
            note=f"文件管理接入(零拷贝):s3://{body.bucket}/{key}",
        )
        session.add(version)
        pairs.append((dataset, version))

    await session.commit()
    cat_name = None
    if body.category_id:
        names = await build_category_name_map(session, [body.category_id])
        cat_name = names.get(body.category_id)
    created: list[DatasetDetailRead] = []
    for dataset, version in pairs:
        await session.refresh(dataset)
        await session.refresh(version)
        detail = _to_detail(dataset, [version])
        if dataset.category_id:
            detail.category_name = cat_name
        created.append(detail)
    return JSONResponse(
        content={
            "data": [d.model_dump(by_alias=True, mode="json") for d in created],
            "success": True,
        }
    )


@router.post(
    "/datasets/{dataset_id}/unhost", dependencies=[Depends(require_admin)]
)
async def unhost_dataset(dataset_id: str, session: SessionDep) -> JSONResponse:
    """取消托管(#18,admin):仅删平台记录(Dataset + 其版本 + JobInput 血缘边)。

    **绝不调用任何 S3 删除**——S3 源对象原样保留(部署红线)。仅对含 hosted
    版本的数据集生效;非托管数据集返回 400(请走常规删除)。
    """
    if not await _has_hosted_version(session, dataset_id):
        dataset = await session.get(Dataset, dataset_id)
        if dataset is None:
            return JSONResponse(
                status_code=404,
                content={"success": False, "message": "数据集不存在"},
            )
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": "该数据集不含外部托管版本,请用常规删除",
            },
        )
    # 只删平台引用(版本 + 血缘边 + 元数据);不动 S3 源对象
    await _purge_dataset(session, dataset_id)
    await session.commit()
    _rmdir(dataset_id)
    return JSONResponse(content={"success": True})


# ===== 版本级发布门(#4,设计见 docs/plan/11-数据安全扫描发布门设计.md)=====
# 只卡发布:质量/加工/标注在草稿版本上不受限;draft→published 需 scan_verdict=passed。
# 三个写端点均 require_admin,经 #5 审计中间件留痕(资源段 dataset-versions)。

_VERDICTS = {"passed", "failed"}


class VerdictUpdate(CamelModel):
    """人工覆盖安全扫描结论入参:verdict ∈ {passed, failed},note 为理由。"""

    verdict: str
    note: str | None = None


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _version_item(version: DatasetVersion) -> dict:
    return {
        "data": DatasetVersionRead.model_validate(version).model_dump(
            by_alias=True, mode="json"
        ),
        "success": True,
    }


@router.post(
    "/dataset-versions/{version_id}/publish",
    dependencies=[Depends(require_admin)],
)
async def publish_version(version_id: str, session: SessionDep) -> JSONResponse:
    """发布一个版本为"已发布·可训练"(admin)。门:scan_verdict 必须为 passed。"""
    version = await session.get(DatasetVersion, version_id)
    if version is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "数据集版本不存在"},
        )
    if version.scan_verdict != "passed":
        return JSONResponse(
            status_code=409,
            content={
                "success": False,
                "message": (
                    "该版本未通过安全扫描,不能发布。请先对其全量跑内容安全扫描;"
                    "若命中敏感内容,可在数据加工中挂隐私脱敏算子产出新版本后重扫,"
                    "或由管理员知情后人工接受风险。"
                ),
            },
        )
    # 幂等:已发布则不改写 published_at(保留首次发布时间,见模型注释"可追溯")
    if version.publish_status != "published":
        version.publish_status = "published"
        version.published_at = _now()
        await session.commit()
        await session.refresh(version)
    return JSONResponse(content=_version_item(version))


@router.post(
    "/dataset-versions/{version_id}/unpublish",
    dependencies=[Depends(require_admin)],
)
async def unpublish_version(version_id: str, session: SessionDep) -> JSONResponse:
    """下架一个已发布版本(admin):published → unpublished,不删数据。"""
    version = await session.get(DatasetVersion, version_id)
    if version is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "数据集版本不存在"},
        )
    if version.publish_status != "published":
        return JSONResponse(
            status_code=409,
            content={"success": False, "message": "只能下架已发布的版本"},
        )
    version.publish_status = "unpublished"
    version.published_at = None
    await session.commit()
    await session.refresh(version)
    return JSONResponse(content=_version_item(version))


@router.post(
    "/dataset-versions/{version_id}/verdict",
    dependencies=[Depends(require_admin)],
)
async def override_verdict(
    version_id: str, body: VerdictUpdate, session: SessionDep
) -> JSONResponse:
    """人工覆盖安全扫描结论(admin):接受风险=passed / 驳回=failed,附理由留审计。"""
    if body.verdict not in _VERDICTS:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "verdict 只能是 passed 或 failed"},
        )
    version = await session.get(DatasetVersion, version_id)
    if version is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "数据集版本不存在"},
        )
    version.scan_verdict = body.verdict
    version.verdict_source = "manual"
    version.verdict_note = body.note
    # 维持不变量"已发布 ⟹ 通过":驳回一个已发布版本时同时下架它,
    # 避免出现 published + failed 这种被算法侧消费的脏状态。
    if body.verdict == "failed" and version.publish_status == "published":
        version.publish_status = "unpublished"
        version.published_at = None
    await session.commit()
    await session.refresh(version)
    return JSONResponse(content=_version_item(version))
