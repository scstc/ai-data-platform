"""数据集路由:上传落地、列表 /datasets、详情、元数据编辑、S3 托管(#18)、删除。"""

from __future__ import annotations

import asyncio
import io
import json
import re
import secrets
import shutil
import tempfile
import zipfile
from collections import deque
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Annotated

import duckdb
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.background import BackgroundTask

from app.api.deps import current_user, require_admin, require_perm, require_user
from app.api.v1.categories import build_category_name_map
from app.core.config import settings
from app.core.db import get_session
from app.core.ids import uuid7_hex
from app.models.dataset import Dataset
from app.models.dataset_acl import DatasetAcl
from app.models.dataset_version import DatasetVersion
from app.models.dataset_version_table import DatasetVersionTable
from app.models.datasource import DataSource
from app.models.job import Job
from app.models.job_input import JobInput
from app.models.review_rule import ReviewRule
from app.models.role import Role
from app.models.tag import DatasetTag, Tag
from app.models.user import User
from app.schemas.common import CamelModel, PageResponse, format_version_label
from app.schemas.dataset import (
    DatasetCreate,
    DatasetDetailRead,
    DatasetMemberRead,
    DatasetRead,
    DatasetTableRead,
    DatasetUpdate,
    DatasetVersionRead,
    ExpiringDatasetOut,
    ExportS3Request,
    HostS3Request,
    PlatformHostRequest,
)
from app.schemas.dataset_acl import AclCreate, AclRead, AclUpdate
from app.services import dataset_acl
from app.services.ai import get_ai_provider
from app.services.engine import _semaphore
from app.services.external_store import (
    MAX_MANIFEST_MEMBERS,
    MAX_MATERIALIZE_BYTES,
    ExternalStoreError,
    cached_bytes,
    download_to_temp,
    head_records,
    list_objects,
    parse_s3_uri,
    platform_config,
    presigned_get_url,
    remove_object,
    remove_prefix,
    s3_settings_for_duckdb,
    stat_object,
    upload_object,
)
from app.services.landing import (
    BINARY_FORMATS,
    INGESTABLE_FORMATS,
    LANDABLE_FORMATS,
    MANIFEST_FORMAT,
    MANIFEST_MEMBER_NAME,
    LandingError,
    ParseError,
    UnsupportedFormatError,
    _safe_table_name,
    add_raw_batch,
    add_table_member,
    create_dataset,
    land_media_manifest,
    land_upload,
    land_upload_raw,
    normalize_to_records,
)
from app.services.review import precheck_records, rules_to_config
from app.services.semantic_registry import (
    SemanticValidationError,
    apply_semantic_spec,
    classify_modalities,
    coerce_semantic_type,
    modalities_for_subtype,
    parse_semantic_type,
    semantic_type_catalog,
)

router = APIRouter(tags=["datasets"])


@router.get("/semantic-types")
async def list_semantic_types() -> JSONResponse:
    """语义类型目录(#1/#2/#8):每类型 key/label/required/mediaFields,供前端下拉/校验。"""
    return JSONResponse(
        content={"data": semantic_type_catalog(), "success": True}
    )

# 依赖别名(与其他路由同款,规避 ruff B008)
SessionDep = Annotated[AsyncSession, Depends(get_session)]
UploadFileDep = Annotated[UploadFile, File(...)]
NameForm = Annotated[str | None, Form()]
DataTypeForm = Annotated[str | None, Form()]
SemanticTypeForm = Annotated[str | None, Form(alias="semanticType")]
StrictQuery = Annotated[bool, Query(alias="strict")]
DescForm = Annotated[str | None, Form()]
CategoryIdForm = Annotated[str | None, Form(alias="categoryId")]
SafetyCheckForm = Annotated[bool, Form(alias="safety_check")]
SafetyUseLlmForm = Annotated[bool, Form(alias="safety_use_llm")]
RawStoreForm = Annotated[bool, Form(alias="raw")]
SafetySampleLimitForm = Annotated[int | None, Form(alias="safety_sample_limit", ge=1)]
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


async def _attach_tables(
    session: AsyncSession, detail: DatasetDetailRead
) -> DatasetDetailRead:
    """按 dataset_version_tables 批量回填各版本的 tables 数组(多表/多 parquet)。

    单表数据集 = 恰好一个成员(回填后存量版本亦然);多表 = 各表一个成员。
    manifest 媒体集不落 dataset_version_tables(见 landing.land_media_manifest),
    合成一个同名(MANIFEST_MEMBER_NAME)伪成员,好让清洗任务编辑器等按"成员级配置"
    统一交互的界面能识别出它——一个 manifest 版本本就是一份清单,天然只有一个成员
    (整版本一套算子,见 jobs._start_job / engine.run_process_job 对该名字的呼应处理)。
    """
    vids = [v.id for v in detail.versions]
    if not vids:
        return detail
    rows = (
        await session.execute(
            select(DatasetVersionTable)
            .where(DatasetVersionTable.dataset_version_id.in_(vids))
            .order_by(DatasetVersionTable.table_name)
        )
    ).scalars().all()
    by_ver: dict[str, list[DatasetTableRead]] = {}
    for m in rows:
        by_ver.setdefault(m.dataset_version_id, []).append(
            DatasetTableRead(
                table_name=m.table_name,
                storage_uri=m.storage_uri,
                format=m.format,
                rows=m.rows,
                size=m.size,
                schema_variant=m.schema_variant,
            )
        )
    for v in detail.versions:
        if v.id not in by_ver and v.format == MANIFEST_FORMAT:
            by_ver[v.id] = [
                DatasetTableRead(
                    table_name=MANIFEST_MEMBER_NAME,
                    storage_uri=v.storage_uri,
                    format=MANIFEST_FORMAT,
                    rows=v.rows,
                    size=v.size,
                )
            ]
        v.tables = by_ver.get(v.id, [])
    return detail


def _new_tag_id() -> str:
    """生成形如 tag-<6位hex> 的标签主键。"""
    return f"tag-{secrets.token_hex(3)}"


async def _dataset_tags_map(
    session: AsyncSession, dataset_ids: list[str]
) -> dict[str, list[str]]:
    """批量取 {dataset_id: [tag_name,...]}(按名排序),供列表/详情回填 tags(避免 N+1)。"""
    if not dataset_ids:
        return {}
    rows = (
        await session.execute(
            select(DatasetTag.dataset_id, Tag.name)
            .join(Tag, Tag.id == DatasetTag.tag_id)
            .where(DatasetTag.dataset_id.in_(dataset_ids))
            .order_by(DatasetTag.dataset_id, Tag.name)
        )
    ).all()
    mp: dict[str, list[str]] = {}
    for ds_id, name in rows:
        mp.setdefault(ds_id, []).append(name)
    return mp


async def _sync_dataset_tags(
    session: AsyncSession, dataset_id: str, names: list[str]
) -> None:
    """全量替换某数据集的标签:names 去重去空白 → find-or-create Tag → 删旧关联 → 建新。"""
    seen: set[str] = set()
    clean: list[str] = []
    for n in names:
        v = (n or "").strip()
        if v and v not in seen:
            seen.add(v)
            clean.append(v)
    existing: dict[str, str] = {}  # name -> id
    if clean:
        rows = (
            await session.execute(
                select(Tag.id, Tag.name).where(Tag.name.in_(clean))
            )
        ).all()
        existing = {name: tid for tid, name in rows}
    for n in clean:
        if n not in existing:
            tag = Tag(id=_new_tag_id(), name=n)
            session.add(tag)
            await session.flush()
            existing[n] = tag.id
    await session.execute(
        delete(DatasetTag).where(DatasetTag.dataset_id == dataset_id)
    )
    for n in clean:
        session.add(DatasetTag(dataset_id=dataset_id, tag_id=existing[n]))


@router.post("/datasets")
async def create_dataset_endpoint(
    payload: DatasetCreate,
    session: SessionDep,
    user: Annotated[User | None, Depends(current_user)] = None,
) -> JSONResponse:
    """建空数据集(不含任何版本);上传/采集随后往里加表成员(数据集优先流程)。"""
    actor = user.id if user else "admin"
    dataset = await create_dataset(
        session,
        name=payload.name,
        data_type=payload.data_type,
        semantic_type=payload.semantic_type.value if payload.semantic_type else None,
        creator=actor,
    )
    # 训练用途默认模板挂到数据集级(落首个成员时由 add_table_member 写入版本级);
    # 暂存于内存详情返回,不入 Dataset 列(版本级不变量,见 spec §4.3)。
    if payload.category_id:
        dataset.category_id = payload.category_id
        await session.commit()
        await session.refresh(dataset)
    if payload.tags:
        await _sync_dataset_tags(session, dataset.id, payload.tags)
        await session.commit()
    detail = _to_detail(dataset, [])
    detail.tags = (await _dataset_tags_map(session, [dataset.id])).get(dataset.id, [])
    if payload.train_type:
        detail.train_type = payload.train_type
    if payload.schema_variant:
        detail.schema_variant = payload.schema_variant
    if dataset.category_id:
        names = await build_category_name_map(session, [dataset.category_id])
        detail.category_name = names.get(dataset.category_id)
    return JSONResponse(
        content=DatasetResult(data=detail).model_dump(by_alias=True, mode="json")
    )


@router.post("/datasets/upload")
async def upload_as_dataset(
    file: UploadFileDep,
    session: SessionDep,
    dataset_id: Annotated[str, Form(alias="datasetId")],
    user: Annotated[User | None, Depends(current_user)] = None,
    semantic_type: SemanticTypeForm = None,
    strict: StrictQuery = False,
) -> JSONResponse:
    """本地上传连接器(数据集优先):文件 → 表成员落进所选数据集的 draft 版本。

    必选 `datasetId`(缺失 422);需对该数据集有写权(否则 403)。
    可选 `semanticType`(与 dataType 正交):传则按其标准 schema 归一+校验;
    `?strict=true` 时不合规整单 422,否则只计数不阻断(见 docs/plan/14)。
    """
    # 显式语义类型先校验合法性(非法值 422),再交给落地层
    try:
        parse_semantic_type(semantic_type)
    except SemanticValidationError as exc:
        return JSONResponse(
            status_code=422,
            content={"success": False, "message": str(exc)},
        )
    # 目标数据集存在性 + 写权校验
    dataset = await session.get(Dataset, dataset_id)
    if dataset is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "目标数据集不存在"},
        )
    if not await dataset_acl.can_access(session, user, dataset_id, "edit"):
        return JSONResponse(
            status_code=403,
            content={"success": False, "message": "无该数据集写入权限"},
        )
    filename = file.filename or ""
    fmt = _file_ext(filename)
    content = await file.read()
    # 二进制类原样存(land_upload_raw),其余规范化落地(land_upload);均落进所选数据集
    try:
        if fmt in BINARY_FORMATS:
            dataset, version = await land_upload_raw(
                session,
                content=content,
                filename=filename,
                source_format=fmt,
                dataset_id=dataset_id,
            )
        else:
            dataset, version = await land_upload(
                session,
                content=content,
                filename=filename,
                source_format=fmt,
                dataset_id=dataset_id,
                semantic_type=semantic_type,
                strict_semantic=strict,
            )
    except SemanticValidationError as exc:
        return JSONResponse(
            status_code=422,
            content={"success": False, "message": f"语义校验失败:{exc}"},
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

    # 重取该数据集全部版本,返回完整详情(成员落在 draft 版本)
    versions = (
        await session.execute(
            select(DatasetVersion)
            .where(DatasetVersion.dataset_id == dataset.id)
            .order_by(DatasetVersion.version_no)
        )
    ).scalars().all()
    detail = _to_detail(dataset, list(versions))
    await _attach_tables(session, detail)
    if dataset.category_id:
        names = await build_category_name_map(session, [dataset.category_id])
        detail.category_name = names.get(dataset.category_id)
    detail.tags = (await _dataset_tags_map(session, [dataset.id])).get(dataset.id, [])
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
    dataset_id: Annotated[str, Form(alias="datasetId")],
    data_type: DataTypeForm = None,
    user: Annotated[User | None, Depends(current_user)] = None,
) -> JSONResponse:
    """媒体批量接入(数据集优先):一批文件 → 传平台 MinIO → 作为一个 **manifest 版本**
    落进所选数据集(一文件一行)。

    必选 `datasetId`(缺失 422、不存在 404、无写权 403)。manifest 是整版本形态
    (非表成员),故每次媒体接入在该数据集追加一个新版本(version_no=max+1),
    keys 落在 v<n>/ 下,避免与其它版本的 manifest 冲突。仅图/音/视频(同模态)。
    """
    if (data_type or "") not in _MEDIA_FIELD:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": "媒体批量接入仅支持 image / audio / video 类型",
            },
        )
    # 目标数据集存在性 + 写权
    dataset = await session.get(Dataset, dataset_id)
    if dataset is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "目标数据集不存在"},
        )
    if not await dataset_acl.can_access(session, user, dataset_id, "edit"):
        return JSONResponse(
            status_code=403,
            content={"success": False, "message": "无该数据集写入权限"},
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
        items = [
            (f.filename or f"file{idx}", await f.read())
            for idx, f in enumerate(files)
        ]
        await land_media_manifest(
            session, dataset_id, files=items, data_type=data_type or ""
        )
        await session.refresh(dataset)
    except LandingError as exc:
        return JSONResponse(
            status_code=400, content={"success": False, "message": str(exc)}
        )
    except ExternalStoreError as exc:
        return JSONResponse(
            status_code=503, content={"success": False, "message": str(exc)}
        )

    versions = (
        await session.execute(
            select(DatasetVersion)
            .where(DatasetVersion.dataset_id == dataset.id)
            .order_by(DatasetVersion.version_no)
        )
    ).scalars().all()
    detail = _to_detail(dataset, list(versions))
    await _attach_tables(session, detail)
    if dataset.category_id:
        names = await build_category_name_map(session, [dataset.category_id])
        detail.category_name = names.get(dataset.category_id)
    detail.tags = (await _dataset_tags_map(session, [dataset.id])).get(dataset.id, [])
    payload = DatasetResult(data=detail)
    return JSONResponse(content=payload.model_dump(by_alias=True, mode="json"))


@router.post("/datasets/upload-batch")
async def upload_batch_as_dataset(
    files: MediaFilesDep,
    session: SessionDep,
    dataset_id: Annotated[str, Form(alias="datasetId")],
    user: Annotated[User | None, Depends(current_user)] = None,
    semantic_type: SemanticTypeForm = None,
    table_name: Annotated[str | None, Form(alias="tableName")] = None,
    safety_check: SafetyCheckForm = True,
    safety_use_llm: SafetyUseLlmForm = False,
    raw: RawStoreForm = False,
    safety_sample_limit: SafetySampleLimitForm = None,
) -> JSONResponse:
    """单一格式批量本地上传(数据集优先):一批同格式文件 → 原件留存 + 合并解析为
    一个表成员,落进所选数据集的 draft 版本。

    必选 `datasetId`(缺失 422、不存在 404、无写权 403)。可选 `tableName`(默认
    由首个文件名派生)。原件逐个存 uploads/<id>/originals/;合并 jsonl 作为该
    版本一个成员(table_name)。

    `raw=true`("单一数据"页):纯文件存储,不对内容做任何解析/提取——表成员只记
    文件名/格式/大小/对象 key 这类元信息,不生成正文记录。供"场景数据"(COT/
    问答对/偏好/时序/GIS)复用同一接口时不受影响,那几种场景从不传 raw,仍走
    `normalize_to_records` 解析出真实结构化字段。
    """
    # 语义类型合法性(可选;与 data_type 正交,非法值 422)
    try:
        parse_semantic_type(semantic_type)
    except SemanticValidationError as exc:
        return JSONResponse(
            status_code=422, content={"success": False, "message": str(exc)}
        )
    # 目标数据集存在性 + 写权
    dataset = await session.get(Dataset, dataset_id)
    if dataset is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "目标数据集不存在"},
        )
    if not await dataset_acl.can_access(session, user, dataset_id, "edit"):
        return JSONResponse(
            status_code=403,
            content={"success": False, "message": "无该数据集写入权限"},
        )
    if not files:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "请至少选择一个文件"},
        )
    # 只收非二进制、可规范化格式:二进制走 /upload-media,未知格式直接拒绝
    for f in files:
        ext = _file_ext(f.filename or "")
        if ext in BINARY_FORMATS:
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "message": f"文件 {f.filename} 是媒体二进制,请走媒体接入",
                },
            )
        if ext not in LANDABLE_FORMATS:
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "message": f"文件 {f.filename} 的格式 .{ext} 暂不支持落地",
                },
            )
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
    # 数据集已存在(数据集优先);原件直接落数据集根下的 originals/,与其它批次
    # 共享该前缀。失败回收按本批已上传的 key 逐个删,不按前缀删,绝不误删其它
    # 批次已存的原件。
    orig_prefix = f"{dataset_id}/originals/"
    eff_table = _safe_table_name(table_name or (files[0].filename or "data"))
    uploaded_keys: list[str] = []
    # 任一步失败(体积/解析/对象写入/落库)都回收本批已上传的原件,绝不留孤儿对象
    try:
        all_records: list[dict] = []
        total_size = 0
        for idx, f in enumerate(files):
            content = await f.read()
            if len(content) > _MAX_MEDIA_FILE_BYTES:
                raise ValueError(f"文件 {f.filename} 超过单文件 200MB 上限")
            total_size += len(content)
            if total_size > MAX_MATERIALIZE_BYTES:
                raise ValueError("本批文件总体积超过上限")
            fmt = _file_ext(f.filename or "")
            base = Path(f.filename or f"file{idx}").name  # 去路径,防 key 注入
            orig_key = f"{orig_prefix}{idx:06d}-{base}"
            await upload_object(
                cfg, bucket, orig_key, io.BytesIO(content), len(content),
                content_type=f.content_type or "application/octet-stream",
            )
            uploaded_keys.append(orig_key)
            if not raw:
                all_records.extend(normalize_to_records(content, fmt))

        if raw:
            # 纯上传:不解析内容、不造表成员——版本直接登记这批文件的计数/体积,
            # 交给 _members_of 的 originals/ 枚举兜底,如实列出每个原件的真实格式。
            version = await add_raw_batch(
                session,
                dataset_id,
                file_count=len(files),
                total_size=total_size,
                bucket=bucket,
                prefix=orig_prefix,
                note=(
                    f"批量上传原始文件(不解析内容):{len(files)} 个文件"
                    f"(原件存 {orig_prefix})"
                ),
            )
        else:
            # 语义维度(与 data_type 正交):显式→归一+校验(非严格只计数)。
            # data_type 归数据集级(建集时已定),批量上传不再单独传。
            explicit = coerce_semantic_type(semantic_type)
            if explicit is not None:
                all_records, _report = apply_semantic_spec(
                    all_records, explicit, strict=False
                )

            # 内容安全前置预检(#4):数据集落库前对全量解析文本跑审核,违规则回滚 +
            # 回收 MinIO 原件,绝不创建脏数据集。默认敏感词 + PII(秒级),LLM 可选
            # (默认关)。拦截口径:高危命中 或 违规占比 ≥ _BLOCK_RATIO(见
            # review.precheck_records)。
            if safety_check:
                # 规则库启用项自动并入预检(与正式审核同口径的自定义规则/敏感数据)
                _rules = (
                    await session.scalars(
                        select(ReviewRule).where(ReviewRule.enabled)
                    )
                ).all()
                rule_words, rule_regex = rules_to_config(list(_rules))
                pre_cfg = {
                    "useFlaggedWords": True,
                    "usePii": True,
                    "useLlm": safety_use_llm,
                    "sampleLimit": safety_sample_limit if safety_sample_limit else 500,
                    "ruleWords": rule_words,
                    "ruleRegex": rule_regex,
                }
                provider = get_ai_provider(settings) if safety_use_llm else None
                async with _semaphore:
                    pre = await precheck_records(
                        all_records, pre_cfg, provider=provider
                    )
                if pre["blocked"]:
                    await session.rollback()
                    await _gc_batch_objects(bucket, uploaded_keys)
                    return JSONResponse(
                        status_code=422,
                        content={
                            "success": False,
                            "message": "内容安全预检未通过,已拦截创建",
                            "reviewReport": pre["report"],
                            "findings": pre["findings_sample"],
                            "ratio": pre["ratio"],
                            "highSeverity": pre["highSeverity"],
                        },
                    )

            # 合并记录作为目标数据集 draft 版本的一个成员(table_name)
            version, _member = await add_table_member(
                session,
                dataset_id,
                all_records,
                table_name=eff_table,
                storage_format="jsonl",
                semantic_type=semantic_type,
                note=(
                    f"单一格式批量上传:{len(files)} 个文件"
                    f"(原件存 {orig_prefix})"
                ),
            )
        dataset = await session.get(Dataset, dataset_id)
    except (ValueError, UnsupportedFormatError, ParseError) as exc:
        await session.rollback()
        await _gc_batch_objects(bucket, uploaded_keys)
        return JSONResponse(
            status_code=400, content={"success": False, "message": str(exc)}
        )
    except ExternalStoreError as exc:
        await session.rollback()
        await _gc_batch_objects(bucket, uploaded_keys)
        return JSONResponse(
            status_code=503,
            content={"success": False, "message": f"对象写入失败:{exc}"},
        )
    except Exception:
        await session.rollback()
        await _gc_batch_objects(bucket, uploaded_keys)
        raise

    versions = (
        await session.execute(
            select(DatasetVersion)
            .where(DatasetVersion.dataset_id == dataset.id)
            .order_by(DatasetVersion.version_no)
        )
    ).scalars().all()
    detail = _to_detail(dataset, list(versions))
    await _attach_tables(session, detail)
    if dataset.category_id:
        names = await build_category_name_map(session, [dataset.category_id])
        detail.category_name = names.get(dataset.category_id)
    detail.tags = (await _dataset_tags_map(session, [dataset.id])).get(dataset.id, [])
    payload = DatasetResult(data=detail)
    return JSONResponse(content=payload.model_dump(by_alias=True, mode="json"))


@router.get("/datasets/{dataset_id}/lineage")
async def dataset_lineage(dataset_id: str, session: SessionDep) -> JSONResponse:
    """数据集血缘图(#11):以该数据集各版本为起点,BFS 上下游(可跨数据集)构建
    版本↔任务 DAG,返回 nodes + edges 供前端分层渲染。深度上限防图过大。

    边:输入版本 --input--> 任务 --output--> 产出版本。
    """
    starts = (
        await session.scalars(
            select(DatasetVersion).where(DatasetVersion.dataset_id == dataset_id)
        )
    ).all()
    if not starts:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "数据集不存在或无版本"},
        )

    MAX_DEPTH = 6
    nodes: dict[str, dict[str, Any]] = {}
    edges: list[dict[str, str]] = []
    seen_edge: set[tuple[str, str]] = set()
    seen_v: set[str] = set()
    seen_j: set[str] = set()
    ds_cache: dict[str, str] = {}

    async def ds_name(did: str) -> str:
        if did not in ds_cache:
            d = await session.get(Dataset, did)
            ds_cache[did] = d.name if d else did
        return ds_cache[did]

    def add_edge(a: str, b: str, kind: str) -> None:
        if (a, b) not in seen_edge:
            seen_edge.add((a, b))
            edges.append({"from": a, "to": b, "kind": kind})

    def job_node(job: Job) -> dict[str, Any]:
        """任务节点:含其执行的算子链(name+params,从 job.spec 取)——供前端在版本/
        任务卡上展示「经什么任务、跑了哪些算子及参数」。"""
        spec = job.spec or {}
        ops = [
            {"name": o.get("name"), "params": o.get("params") or {}}
            for o in (spec.get("operators") or [])
            if isinstance(o, dict)
        ]
        # review 等非算子任务:无 operators 但有 config → 把 config 原语字段作为
        # 伪算子("审核配置")吐出,让版本卡能看到 LLM/PII/抽样等设置。
        # 旧任务(spec 为空,早于 spec 存储特性)仍为空。
        if not ops and isinstance(spec.get("config"), dict):
            parts = {
                k: v
                for k, v in spec["config"].items()
                if isinstance(v, (str, int, float, bool))
            }
            if parts:
                ops = [{"name": "审核配置", "params": parts}]
        return {
            "id": job.id,
            "kind": "job",
            "name": job.name,
            "jobType": job.type,
            "state": job.state,
            "operators": ops,
            "createdAt": job.created_at.isoformat(),
        }

    dq: deque[tuple[str, int]] = deque((v.id, 0) for v in starts)
    while dq:
        vid, depth = dq.popleft()
        if vid in seen_v:
            continue
        seen_v.add(vid)
        version = await session.get(DatasetVersion, vid)
        if version is None:
            continue
        nodes[vid] = {
            "id": vid,
            "kind": "version",
            "datasetId": version.dataset_id,
            "datasetName": await ds_name(version.dataset_id),
            "versionNo": version.version_no,
            "versionLabel": format_version_label(
                version.version_no, version.created_at
            ),
            "origin": version.origin,
            "rows": version.rows,
            "scanVerdict": version.scan_verdict,
            "publishStatus": version.publish_status,
            "isOriginal": version.produced_by_job_id is None,
            "isFocus": version.dataset_id == dataset_id,
            "createdAt": version.created_at.isoformat(),
        }
        if depth >= MAX_DEPTH:
            continue
        # 上游:产出该版本的任务(及其输入版本)
        jid = version.produced_by_job_id
        if jid and jid not in seen_j:
            seen_j.add(jid)
            job = await session.get(Job, jid)
            if job:
                nodes[jid] = job_node(job)
                add_edge(jid, vid, "output")
                in_jis = (
                    await session.scalars(
                        select(JobInput).where(JobInput.job_id == jid)
                    )
                ).all()
                for ji in in_jis:
                    add_edge(ji.dataset_version_id, jid, "input")
                    if ji.dataset_version_id not in seen_v:
                        dq.append((ji.dataset_version_id, depth + 1))
        # 下游:消费该版本的任务(及其产出版本)
        down_jis = (
            await session.scalars(
                select(JobInput).where(JobInput.dataset_version_id == vid)
            )
        ).all()
        for ji in down_jis:
            jid2 = ji.job_id
            add_edge(vid, jid2, "input")
            if jid2 in seen_j:
                continue
            seen_j.add(jid2)
            job2 = await session.get(Job, jid2)
            if job2 is None:
                continue
            nodes[jid2] = job_node(job2)
            out_vs = (
                await session.scalars(
                    select(DatasetVersion).where(
                        DatasetVersion.produced_by_job_id == jid2
                    )
                )
            ).all()
            for ov in out_vs:
                add_edge(jid2, ov.id, "output")
                if ov.id not in seen_v:
                    dq.append((ov.id, depth + 1))
    return JSONResponse(
        content={"data": {"nodes": list(nodes.values()), "edges": edges}, "success": True}
    )


async def _members_of(
    version: DatasetVersion, session: AsyncSession
) -> list[DatasetMemberRead]:
    """枚举版本的成员文件(优先 dataset_version_tables 表成员 → manifest → originals/ → 单一成员)。
    复用于 members 端点与多文件 zip 下载。存储错误抛 ExternalStoreError。"""
    # 数据集优先改造:优先按显式表成员(dataset_version_tables)枚举。
    # 单表数据集 = 恰好一个 "data" 成员;多表 = 各表一个成员。回填后的存量版本
    # 也有一行 "data" 成员,故新旧版本统一走此路径。
    table_members = (
        await session.execute(
            select(DatasetVersionTable)
            .where(DatasetVersionTable.dataset_version_id == version.id)
            .order_by(DatasetVersionTable.table_name)
        )
    ).scalars().all()
    if table_members:
        out: list[DatasetMemberRead] = []
        for tm in table_members:
            bucket, key = "", tm.storage_uri
            if str(tm.storage_uri).startswith("s3://"):
                try:
                    bucket, key = parse_s3_uri(tm.storage_uri)
                except ExternalStoreError:
                    bucket, key = "", tm.storage_uri
            out.append(
                DatasetMemberRead(
                    name=tm.table_name,
                    key=key,
                    bucket=bucket,
                    format=tm.format,
                    size=tm.size,
                )
            )
        return out
    if str(version.storage_uri).startswith("pending://"):
        # 空版本占位(新建版本/克隆上一版尚未写入任何成员):storage_uri 是
        # 占位字符串,不是真实对象,不能当成一个成员枚举出去(否则预览列表
        # 会冒出一个 key 指向不存在对象的假成员)。
        return []
    if version.format != MANIFEST_FORMAT:
        # 受管批量上传版本(单一格式批量接入):枚举 originals/ 下各原件。
        # 仅限批量上传落地版本(produced_by_job_id 为空);加工/采集等 job 产出是
        # 单文件版本,storage_uri 直指产物对象——若也走 originals/ 会错列回数据集的
        # 原始上传件(预览所有版本都显示成第一版原件,加工产物被掩盖)。
        if (
            version.produced_by_job_id is None
            and version.origin == "managed"
            and str(version.storage_uri).startswith("s3://")
        ):
            try:
                cfg = platform_config()
                bucket, _vkey = parse_s3_uri(version.storage_uri)
                objs = await list_objects(
                    cfg, bucket, f"{version.dataset_id}/originals/"
                )
            except ExternalStoreError:
                objs = []
            if objs:
                return [
                    DatasetMemberRead(
                        name=Path(o["key"]).name,
                        key=o["key"],
                        bucket=bucket,
                        format=_file_ext(Path(o["key"]).name),
                        size=o.get("size"),
                    )
                    for o in objs
                ]
        # 回退:单一成员(合并 jsonl 本身)
        bucket = ""
        key = version.storage_uri
        if str(version.storage_uri).startswith("s3://"):
            try:
                bucket, key = parse_s3_uri(version.storage_uri)
            except ExternalStoreError:
                bucket, key = "", version.storage_uri
        return [
            DatasetMemberRead(
                name=Path(key).name,
                key=key,
                bucket=bucket,
                format=version.format,
                size=version.size,
            )
        ]
    # manifest 媒体集:从清单 __member 取
    cfg = await _version_storage_cfg(version, session)
    if cfg is None:
        raise ExternalStoreError("平台存储(MinIO)未配置")
    rows = await _read_manifest_rows(cfg, version.storage_uri)
    return [
        DatasetMemberRead(**m)
        for r in rows
        if isinstance((m := r.get("__member")), dict)
    ]


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
    try:
        members = await _members_of(version, session)
    except ExternalStoreError as exc:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": f"读取成员失败:{exc}"},
        )
    return JSONResponse(
        content={
            "data": [m.model_dump(by_alias=True) for m in members],
            "success": True,
        }
    )


@router.delete("/dataset-versions/{version_id}/members")
async def delete_version_members(
    version_id: str,
    session: SessionDep,
    user: UserDep,
    keys: Annotated[list[str], Query()],
) -> JSONResponse:
    """删除版本内的一个或多个成员文件(单删/批量)。

    - 仅允许 draft 版本;发布/下架版本返回 409。
    - 调用者须有该数据集的 edit 或 admin 级别权限;否则 403。
    - 支持两类成员:
      · DatasetVersionTable 成员(结构化 parquet/jsonl):删对象 + 删 DB 行 + 刷
        新版本 rollup。
      · originals/ 原件(raw 批次上传):删 MinIO 对象;无对应 DB 行。
    - 返回 {data: {deleted: N, notFound: N}, success: true}。
    """
    version = await session.get(DatasetVersion, version_id)
    if version is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "版本不存在"},
        )
    if version.publish_status != "draft":
        return JSONResponse(
            status_code=409,
            content={"success": False, "message": "只有草稿版本可以删除成员"},
        )
    if not await dataset_acl.can_access(session, user, version.dataset_id, "edit"):
        return JSONResponse(
            status_code=403,
            content={"success": False, "message": "无权限修改该数据集"},
        )
    try:
        cfg = platform_config()
    except ExternalStoreError as exc:
        return JSONResponse(
            status_code=503, content={"success": False, "message": str(exc)}
        )

    # 查 DatasetVersionTable 成员(按 storage_uri 末段 key 匹配)
    table_rows: list[DatasetVersionTable] = list(
        (
            await session.execute(
                select(DatasetVersionTable).where(
                    DatasetVersionTable.dataset_version_id == version_id
                )
            )
        )
        .scalars()
        .all()
    )
    # 建立 key → row 映射(storage_uri 可能是完整 s3:// URI 或裸 key)
    key_to_row: dict[str, DatasetVersionTable] = {}
    for row in table_rows:
        try:
            _, rkey = parse_s3_uri(row.storage_uri)
        except Exception:
            rkey = str(row.storage_uri)
        key_to_row[rkey] = row

    deleted = 0
    not_found = 0
    size_freed = 0
    for key in keys:
        if row := key_to_row.get(key):
            # 结构化成员:删对象 + DB 行
            bucket, obj_key = ("", key)
            try:
                bucket, obj_key = parse_s3_uri(row.storage_uri)
            except Exception:
                pass
            try:
                await remove_object(cfg, bucket or settings.storage_minio_upload_bucket, obj_key)
            except ExternalStoreError:
                pass
            size_freed += row.size or 0
            await session.delete(row)
            deleted += 1
        else:
            # originals/ 原件:只删 MinIO 对象
            bucket = settings.storage_minio_upload_bucket
            try:
                await remove_object(cfg, bucket, key)
                deleted += 1
            except ExternalStoreError:
                not_found += 1

    # 刷新版本 rollup(rows/size 按剩余 DatasetVersionTable 求和)
    remaining: list[DatasetVersionTable] = list(
        (
            await session.execute(
                select(DatasetVersionTable).where(
                    DatasetVersionTable.dataset_version_id == version_id
                )
            )
        )
        .scalars()
        .all()
    )
    if remaining:
        version.rows = sum(r.rows or 0 for r in remaining)
        version.size = sum(r.size or 0 for r in remaining)
    else:
        # 没有结构化成员了:rows/size 减去已删的 originals 体积(无精确 DB 值则置 None)
        version.size = max(0, (version.size or 0) - size_freed)

    await session.commit()
    return JSONResponse(
        content={"data": {"deleted": deleted, "notFound": not_found}, "success": True}
    )


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

    # 成员必须与 manifest.jsonl 同前缀(images/audios/videos 字段存相对该前缀的
    # 文件名,见 landing.land_media_manifest 的写入约定与
    # external_store._materialized_manifest 的解析侧)
    manifest_prefix = manifest_key.rpartition("/")[0]
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
            local_name = f"{secrets.token_hex(4)}-{base}"
            member_key = (
                f"{manifest_prefix}/{local_name}" if manifest_prefix else local_name
            )
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
                    field: [local_name],
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


async def _showcase_modalities(
    session: AsyncSession, dataset_ids: list[str]
) -> dict[str, list[str] | None]:
    """取各数据集**展示版本**(优先 published,否则 version_no 最大)的 modalities。

    与 latest_version_label 同口径,供列表子标签展示 + modality 筛选分类。
    展示版本无 modalities(存量/非多模态)→ None。
    """
    if not dataset_ids:
        return {}
    rows = (
        await session.execute(
            select(
                DatasetVersion.dataset_id,
                DatasetVersion.version_no,
                DatasetVersion.publish_status,
                DatasetVersion.modalities,
            ).where(DatasetVersion.dataset_id.in_(dataset_ids))
        )
    ).all()
    published: dict[str, tuple[int, object]] = {}
    latest: dict[str, tuple[int, object]] = {}
    for ds_id, vno, status, mods in rows:
        if status == "published":
            published[ds_id] = (vno, mods)
        cur = latest.get(ds_id)
        if cur is None or vno > cur[0]:
            latest[ds_id] = (vno, mods)
    out: dict[str, list[str] | None] = {}
    for ds_id, (vno, mods) in latest.items():
        out[ds_id] = published.get(ds_id, (vno, mods))[1]
    return out


async def _showcase_version(
    session: AsyncSession, dataset_id: str
) -> DatasetVersion | None:
    """取数据集**展示版本**对象(优先 published,否则 version_no 最大),与
    ``_showcase_modalities`` 同口径。供快速设置多模态子类型时反写 modalities。"""
    versions = (
        await session.scalars(
            select(DatasetVersion).where(
                DatasetVersion.dataset_id == dataset_id
            )
        )
    ).all()
    if not versions:
        return None
    published = [v for v in versions if v.publish_status == "published"]
    pool = published or list(versions)
    return max(pool, key=lambda v: v.version_no)


@router.get(
    "/datasets",
    response_model=PageResponse[DatasetRead],
    dependencies=[Depends(require_perm("dataset:list"))],
)
async def list_datasets(
    session: SessionDep,
    user: Annotated[User | None, Depends(current_user)] = None,
    current: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, alias="pageSize"),
    name: str | None = Query(None),
    data_type: str | None = Query(None, alias="dataType"),
    semantic_type: str | None = Query(None, alias="semanticType"),
    source_kind: str | None = Query(None, alias="sourceKind"),
    category_id: str | None = Query(None, alias="categoryId"),
    # 选父含子筛选:逗号分隔的分类 id 列表(前端展开选中分类的全部后代 id),IN 查询。
    category_ids: str | None = Query(None, alias="categoryIds"),
    tags: str | None = Query(None),
    creator: str | None = Query(None),
    created_start: CreatedStartQuery = None,
    created_end: CreatedEndQuery = None,
    publish_status: str | None = Query(None, alias="publishStatus"),
    train_type: str | None = Query(
        None,
        alias="trainType",
        description="按训练用途过滤(版本级):pretrain|sft|distill|dpo|rlhf|eval|custom",
    ),
    modality: str | None = Query(
        None,
        description="多模态子分类筛选:image|video|audio|cross(按展示版本 modalities 分类)",
    ),
) -> PageResponse[DatasetRead]:
    """分页查询数据集,按创建时间倒序;按元数据条件过滤(向后兼容)。

    publishStatus=published 时只返回含已发布版本的数据集(算法工程师消费视图)。
    """
    conds = []
    if name:
        conds.append(Dataset.name.ilike(f"%{name}%"))
    if data_type:
        conds.append(Dataset.data_type == data_type)
    if semantic_type:
        conds.append(Dataset.semantic_type == semantic_type)
    if source_kind:
        conds.append(Dataset.source_kind == source_kind)
    if category_id:
        conds.append(Dataset.category_id == category_id)
    # 选父含子:categoryIds(逗号分隔)展开成 id 列表 IN 查询,选中父分类时连带所有子孙。
    if category_ids:
        cat_id_list = [x.strip() for x in category_ids.split(",") if x.strip()]
        if cat_id_list:
            conds.append(Dataset.category_id.in_(cat_id_list))
    # 标签过滤(多标签 OR):tags(逗号分隔)→ 命中含任一标签的数据集。
    if tags:
        tag_names = [x.strip() for x in tags.split(",") if x.strip()]
        if tag_names:
            tagged_ds_ids = (
                select(DatasetTag.dataset_id)
                .join(Tag, Tag.id == DatasetTag.tag_id)
                .where(Tag.name.in_(tag_names))
            )
            conds.append(Dataset.id.in_(tagged_ds_ids))
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
    # 训练用途过滤(train_type 是版本级字段,走 DatasetVersion 子查询取 dataset_id):
    # 数据集存在任一版本 train_type==X 即命中(与 publishStatus 同口径)。
    if train_type:
        tt_ds_ids = (
            await session.scalars(
                select(DatasetVersion.dataset_id)
                .where(DatasetVersion.train_type == train_type)
                .distinct()
            )
        ).all()
        conds.append(Dataset.id.in_(tt_ds_ids))
    # 数据集 ACL:匿名沿用现状(不过滤)、登录用户按 owner+超管+授权过滤
    base = await dataset_acl.visible_dataset_filter(
        select(Dataset).where(*conds), session, user
    )
    # 多模态子分类筛选(modality 是版本级字段,按展示版本 modalities 分类过滤):
    # 取所有候选(其他条件 + ACL)的展示版本 modalities → Python 分类 → 命中 id 集再 IN。
    if modality:
        cand_ids = (
            await session.scalars(
                select(Dataset.id).select_from(base.subquery())
            )
        ).all()
        sm = await _showcase_modalities(session, cand_ids)
        matched = [d for d in cand_ids if classify_modalities(sm.get(d)) == modality]
        base = select(Dataset).where(Dataset.id.in_(matched))
    total = await session.scalar(select(func.count()).select_from(base.subquery()))
    offset = (current - 1) * page_size
    rows = (
        await session.scalars(
            base.order_by(Dataset.created_at.desc()).offset(offset).limit(page_size)
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
    # 一次查出本页各数据集的「当前展示版本」标签,避免 N+1。优先取已发布版本
    # (publish_version 不变量保证同数据集至多一个 published——算法侧消费的唯一当前
    # 发布版);无已发布版本时回退最新版本(version_no 最大者,供纯草稿数据集展示)。
    # 否则会把更新的草稿版本号当成"已发布版本号"显示,与详情页对不上。
    # 治理整改 G1:同时取 train_type、schema_variant 回填列表展示。
    latest_label: dict[str, str] = {}
    showcase_train_type: dict[str, str | None] = {}
    showcase_schema_variant: dict[str, str | None] = {}
    if page_ids:
        ver_rows = (
            await session.execute(
                select(
                    DatasetVersion.dataset_id,
                    DatasetVersion.version_no,
                    DatasetVersion.created_at,
                    DatasetVersion.publish_status,
                    DatasetVersion.train_type,
                    DatasetVersion.schema_variant,
                ).where(DatasetVersion.dataset_id.in_(page_ids))
            )
        ).all()
        published: dict[str, tuple[int, object, str | None, str | None]] = {}
        latest: dict[str, tuple[int, object, str | None, str | None]] = {}
        for ds_id, vno, created, status, tt, sv in ver_rows:
            if status == "published":
                published[ds_id] = (vno, created, tt, sv)
            cur = latest.get(ds_id)
            if cur is None or vno > cur[0]:
                latest[ds_id] = (vno, created, tt, sv)
        for ds_id, (vno, created, tt, sv) in latest.items():
            # 已发布版本优先;无则用最新版本
            pick_vno, pick_created, pick_tt, pick_sv = published.get(
                ds_id, (vno, created, tt, sv)
            )
            latest_label[ds_id] = format_version_label(
                pick_vno, pick_created  # type: ignore[arg-type]
            )
            showcase_train_type[ds_id] = pick_tt
            showcase_schema_variant[ds_id] = pick_sv
    # 展示版本(优先 published,否则最新)的多模态模态集合,回填 modalities(子标签)
    showcase_mods = await _showcase_modalities(session, page_ids)
    # 批量取本页分类名(避免 N+1),回填 categoryName
    cat_names = await build_category_name_map(
        session, [r.category_id for r in rows]
    )
    # 批量取本页标签(避免 N+1),回填 tags
    tags_map = await _dataset_tags_map(session, page_ids)
    data = []
    for r in rows:
        item = DatasetRead.model_validate(r)
        item.hosted = r.id in hosted_ids
        item.latest_version_label = latest_label.get(r.id)
        item.modalities = showcase_mods.get(r.id)
        item.train_type = showcase_train_type.get(r.id)
        item.schema_variant = showcase_schema_variant.get(r.id)
        if r.category_id:
            item.category_name = cat_names.get(r.category_id)
        item.tags = tags_map.get(r.id, [])
        data.append(item)
    return PageResponse[DatasetRead](data=data, total=total or 0)


@router.get("/datasets/expiring")
async def list_expiring_datasets(
    session: SessionDep,
    user: Annotated[User, Depends(require_user)],
    days: int = Query(14, ge=1, le=365),
) -> dict:
    """当前用户负责(owner/creator)的即将到期数据集,登录后弹窗提醒用。

    命中口径:valid_until 非空且 <= 今天 + days(含已过期,expired=true)。
    按 valid_until 升序(最紧急在前)。路由声明在 /datasets/{dataset_id} 之前,
    否则 "expiring" 会被当成 dataset_id 捕获。
    """
    now = datetime.now(UTC).replace(tzinfo=None)
    threshold = now + timedelta(days=days)
    rows = (
        await session.scalars(
            select(Dataset)
            .where(
                Dataset.valid_until.is_not(None),
                Dataset.valid_until <= threshold,
                or_(
                    Dataset.owner == user.username,
                    Dataset.creator == user.username,
                ),
            )
            .order_by(Dataset.valid_until.asc())
        )
    ).all()
    today = now.date()
    items = [
        ExpiringDatasetOut(
            id=r.id,
            name=r.name,
            valid_until=r.valid_until,
            days_left=(r.valid_until.date() - today).days,
            expired=r.valid_until.date() < today,
        )
        for r in rows
    ]
    return {"data": items, "success": True}


@router.get("/datasets/{dataset_id}")
async def get_dataset(
    dataset_id: str,
    session: SessionDep,
    user: Annotated[User | None, Depends(current_user)] = None,
) -> JSONResponse:
    """数据集详情:元信息 + 版本列表(按版本号升序)。登录用户受 ACL 约束,匿名放行。"""
    dataset = await session.get(Dataset, dataset_id)
    if dataset is None or not await dataset_acl.can_access(
        session, user, dataset_id, "view"
    ):
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
    await _attach_tables(session, detail)
    if dataset.category_id:
        names = await build_category_name_map(session, [dataset.category_id])
        detail.category_name = names.get(dataset.category_id)
    detail.tags = (await _dataset_tags_map(session, [dataset_id])).get(
        dataset_id, []
    )
    # 当前用户对该数据集的生效级别,供前端按钮门控(如「权限管理」仅 admin 显示)
    detail.my_level = await dataset_acl.get_acl_level(session, user, dataset_id)
    payload = DatasetResult(data=detail)
    return JSONResponse(content=payload.model_dump(by_alias=True, mode="json"))


@router.patch("/datasets/{dataset_id}")
async def update_dataset(
    dataset_id: str,
    body: DatasetUpdate,
    session: SessionDep,
    user: Annotated[User | None, Depends(current_user)] = None,
) -> JSONResponse:
    """编辑数据集可变元数据:仅 owner/超管/ACL-edit+ 可改;记录变更人;匿名放行。"""
    dataset = await session.get(Dataset, dataset_id)
    if dataset is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "数据集不存在"},
        )
    if not await dataset_acl.can_access(session, user, dataset_id, "edit"):
        return JSONResponse(
            status_code=403,
            content={"success": False, "message": "无权限"},
        )
    updates = body.model_dump(exclude_unset=True)
    # tags 是多对多关联(非 Dataset 列),单独同步,不走 setattr。
    if "tags" in updates:
        await _sync_dataset_tags(session, dataset_id, updates.pop("tags") or [])
    # modality_subtype 是版本级派生字段(非 Dataset 列):反写展示版本 modalities,
    # 列表「数据类型」子标签据此还原(无展示版本则静默跳过,仅设 semantic_type)。
    # 对称清理:semantic_type 显式切到非多模态(或清空)时一并清掉残留 modalities,
    # 否则旧的多模态模态会继续命中模态筛选(列表/详情共用本 PATCH)。
    subtype = updates.pop("modality_subtype", None)
    sem_set = "semantic_type" in updates
    if subtype is not None or (sem_set and updates["semantic_type"] != "multimodal"):
        ver = await _showcase_version(session, dataset_id)
        if ver is not None:
            ver.modalities = (
                modalities_for_subtype(subtype) if subtype is not None else None
            )
    for field, value in updates.items():
        setattr(dataset, field, value)
    dataset.last_modifier = user.id if user else "admin"
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
    await _attach_tables(session, detail)
    if dataset.category_id:
        names = await build_category_name_map(session, [dataset.category_id])
        detail.category_name = names.get(dataset.category_id)
    detail.tags = (await _dataset_tags_map(session, [dataset_id])).get(
        dataset_id, []
    )
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
    """数据集在平台 MinIO 有自有对象(媒体 manifest 或批量 jsonl,storage_uri=s3://…)。

    返回 (bucket, '<id>/') 供删除时回收整个前缀;无 s3 版本→None。
    仅用于可删除(非 hosted)数据集:hosted 外部数据在删除门控处已被拦截,不会走到这里,
    故按 dataset 前缀回收平台对象不会误删任何外部源。"""
    versions = (
        await session.scalars(
            select(DatasetVersion).where(
                DatasetVersion.dataset_id == dataset_id,
            )
        )
    ).all()
    for ver in versions:
        uri = str(ver.storage_uri or "")
        if not uri.startswith("s3://"):
            continue
        try:
            bucket, _ = parse_s3_uri(uri)
        except ExternalStoreError:
            continue
        return bucket, f"{dataset_id}/"
    return None


async def _gc_manifest_objects(target: tuple[str, str] | None) -> None:
    """尽力回收 manifest 数据集在平台 MinIO 的对象;平台未配/不可达时不阻断删除。"""
    if target is None:
        return
    try:
        await remove_prefix(platform_config(), target[0], target[1])
    except ExternalStoreError:
        pass  # DB 行已删;残留对象由后续清理,不让 GC 失败阻断删除


async def _gc_batch_objects(bucket: str, keys: list[str]) -> None:
    """尽力逐个删除批量上传已写入的原件(按 key,非按前缀——前缀与其它批次共享)。"""
    for key in keys:
        try:
            await remove_object(platform_config(), bucket, key)
        except ExternalStoreError:
            pass  # 残留对象由后续清理,不让 GC 失败阻断错误返回


class BatchDeleteRequest(CamelModel):
    """批量删除入参。"""

    ids: list[str]


# 外部托管数据禁止删除的统一文案(#18:删源是部署红线)
_HOSTED_DELETE_MSG = "外部托管数据不支持删除,请用取消托管"


@router.delete("/datasets/{dataset_id}")
async def delete_dataset(
    dataset_id: str,
    session: SessionDep,
    user: Annotated[User | None, Depends(current_user)] = None,
) -> JSONResponse:
    """删除数据集:级联删版本 + 清血缘边(job_inputs)+ 删磁盘产物。

    仅 owner/超管可删(销毁性操作不给 ACL-admin);匿名放行(兼容现状)。
    含 hosted 版本 → 403 拒绝(#18:删源禁止,请走取消托管),绝不动 S3。
    非管理员且非 owner → 403(数据集不存在也判 403,避免泄露存在性)。
    """
    # 鉴权:匿名→401(销毁性操作必须登录);超管放行;其余必须是 owner。
    # 用 HTTPException 抛 403 以保持原 require_admin 的 detail 包裹契约(test_rbac)。
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    if user.role != "admin":
        ds = await session.get(Dataset, dataset_id)
        if ds is None or (ds.owner != user.id and ds.creator != user.id):
            raise HTTPException(
                status_code=403,
                detail={"success": False, "message": "无权限"},
            )
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
    key: str | None = Query(None, description="指定则预览该原件(单文件,列纯净)"),
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

    # 指定原件预览(受管批量版本的某个原始文件):单文件 normalize,列纯净,
    # 绕开合并 jsonl 的全行 key 并集,消除多文件字段错乱。
    # 仅对 s3:// 背书的版本生效——本地单文件版本(任务产出/输入 jsonl)的成员 key
    # 即其本地 storage_uri,应直接走末尾的本地读盘分支,而非尝试 S3 解析(否则 503)。
    # parquet 排除在外:head_records→normalize_to_records 仅解析文本格式(jsonl/csv/...),
    # 对 parquet 字节会 UTF-8 解码失败;parquet 单成员的 key 即 storage_uri 的 key 部分,
    # 指向同一文件,放行到下方 DuckDB read_parquet 分支处理(类型保真,数据一致)。
    if key and version.format != "parquet" and str(version.storage_uri).startswith(
        "s3://"
    ):
        try:
            cfg = platform_config()
            bucket, _vk = parse_s3_uri(version.storage_uri)
        except ExternalStoreError as exc:
            return JSONResponse(
                status_code=503, content={"success": False, "message": str(exc)}
            )
        try:
            rows = await head_records(
                cfg, bucket, key, _file_ext(Path(key).name), offset + limit
            )
        except ExternalStoreError as exc:
            return JSONResponse(
                status_code=400,
                content={"success": False, "message": f"读取原件失败:{exc}"},
            )
        rows = rows[offset : offset + limit]
        return JSONResponse(
            content={
                "data": rows,
                "columns": _columns_of(rows),
                "total": version.rows or len(rows),
                "success": True,
            }
        )

    # s3:// 背书(hosted 外部 / 平台自有 jsonl):按需取前 offset+limit 条再切片
    # (按 storage_uri scheme 路由,不再凭 origin 二分;平台自有 jsonl 走 source=None 分支)
    if str(version.storage_uri).startswith("s3://"):
        # parquet:走 DuckDB read_parquet(类型保真),复用 SQL 查询同款执行器
        if version.format == "parquet":
            try:
                cfg = await _version_storage_cfg(version, session)
                if cfg is None:
                    raise ExternalStoreError("平台存储(MinIO)未配置")
                s3 = s3_settings_for_duckdb(cfg)
                # 如果指定了 key(成员级预览),用 key 构造完整路径;否则用 storage_uri
                if key:
                    bucket, _vk = parse_s3_uri(version.storage_uri)
                    parquet_path = f"s3://{bucket}/{key}"
                else:
                    parquet_path = version.storage_uri
                rows, columns, _total = await asyncio.to_thread(
                    _duck_query, parquet_path, "parquet",
                    "SELECT * FROM t", limit, offset, s3,
                )
            except ExternalStoreError as exc:
                return JSONResponse(
                    status_code=400,
                    content={"success": False, "message": f"读取 parquet 失败:{exc}"},
                )
            return JSONResponse(
                content={
                    "data": rows,
                    "columns": columns,
                    "total": version.rows or 0,
                    "success": True,
                }
            )
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


# ===== DuckDB SQL 查询(只读):对标竞品 LAS 的数据集 SQL 查询能力 =====
# 用户 SQL 跑在只读 view `t` 上(对版本数据 jsonl/parquet/csv 的封装);
# httpfs 直查 MinIO(s3://) 或读本地文件,与 preview 同 storage_uri 形态路由。

# DuckDB 读函数 → 格式映射(txt/log 当 jsonl 一行一对象)
_DUCK_READERS = {
    "jsonl": "read_json_auto",
    "json": "read_json_auto",
    "txt": "read_json_auto",
    "log": "read_json_auto",
    "csv": "read_csv_auto",
    "tsv": "read_csv_auto",
    "parquet": "read_parquet",
}

# 仅允许只读查询:拦截写/结构变更/副作用关键字(大小写不敏感,词边界)
_SQL_FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|CREATE|TRUNCATE|MERGE|PRAGMA|COPY|ATTACH|"
    r"DETACH|EXPORT|INSTALL|LOAD|CALL|VACUUM|REPLACE)\b",
    re.IGNORECASE,
)


def _duck_reader_sql(fmt: str, path: str) -> str:
    """拼 DuckDB 读表表达式;tsv 指定制表符分隔。"""
    fn = _DUCK_READERS.get((fmt or "jsonl").lower(), "read_json_auto")
    if (fmt or "").lower() == "tsv":
        return f"read_csv_auto('{path}', delim='\\t', header=true)"
    return f"{fn}('{path}')"


def _duck_safe(v: object) -> object:
    """把 DuckDB 返回值归一为 JSON 可序列化(Decimal/datetime/bytes → str)。"""
    if v is None or isinstance(v, (bool, int, float, str, list, dict)):
        return v
    return str(v)


def _duck_query(
    path: str,
    fmt: str,
    sql: str,
    limit: int,
    offset: int,
    s3: tuple[str, bool, str, str] | None,
) -> tuple[list[dict], list[str], int]:
    """同步执行 DuckDB 只读查询(供 asyncio.to_thread,避免阻塞事件循环)。

    s3 非 None 时配 httpfs 直查对象存储(MinIO/S3,免下载);否则读本地路径。
    用户 SQL 作为子查询包裹、强制 LIMIT/OFFSET 兜底,跑在 view `t` 上。
    """
    con = duckdb.connect()
    try:
        if s3 is not None:
            endpoint, use_ssl, ak, sk = s3
            con.execute("INSTALL httpfs; LOAD httpfs;")
            con.execute(f"SET s3_endpoint='{endpoint}';")
            con.execute("SET s3_url_style='path';")
            con.execute(f"SET s3_use_ssl={'true' if use_ssl else 'false'};")
            con.execute(f"SET s3_access_key_id='{ak}';")
            con.execute(f"SET s3_secret_access_key='{sk}';")
        con.execute(f"CREATE VIEW t AS SELECT * FROM {_duck_reader_sql(fmt, path)}")
        wrapped = f"SELECT * FROM ({sql}) AS _q LIMIT {limit} OFFSET {offset}"
        cur = con.execute(wrapped)
        columns = [d[0] for d in cur.description]
        rows = [
            {columns[i]: _duck_safe(r[i]) for i in range(len(columns))}
            for r in cur.fetchall()
        ]
        return rows, columns, len(rows)
    finally:
        con.close()


@router.post("/dataset-versions/{version_id}/query", response_model=None)
async def query_version(
    version_id: str,
    payload: dict,
    session: SessionDep,
) -> JSONResponse:
    """对某版本数据跑 DuckDB 只读 SQL,返回 {columns,data,total,success,message?}(形状同 preview)。

    - 仅允许 SELECT(关键字黑名单拦截写/结构变更/副作用)。
    - storage_uri 形态路由同 preview:s3:// → httpfs 直查 MinIO;本地路径 → read_json_auto。
    - manifest / 二进制不支持(同 preview 拒绝策略)。
    - 重计算下沉 asyncio.to_thread(仿 quality._scan_stats),不阻塞事件循环。
    """
    version = await session.get(DatasetVersion, version_id)
    if version is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "版本不存在"},
        )
    if version.format == MANIFEST_FORMAT:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": "manifest 媒体集不支持 SQL 查询,请用表格预览",
            },
        )
    if version.format in BINARY_FORMATS:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "二进制文件不支持 SQL 查询"},
        )

    sql_raw = str(payload.get("sql") or "").strip().rstrip(";").strip()
    if not sql_raw:
        return JSONResponse(
            status_code=400, content={"success": False, "message": "SQL 不能为空"}
        )
    if _SQL_FORBIDDEN.search(sql_raw):
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "仅支持只读 SELECT 查询"},
        )
    try:
        limit = max(1, min(int(payload.get("limit") or 50), 500))
        offset = max(0, int(payload.get("offset") or 0))
    except (TypeError, ValueError):
        limit, offset = 50, 0

    storage_uri = str(version.storage_uri)
    try:
        if storage_uri.startswith("s3://"):
            cfg = await _version_storage_cfg(version, session)
            if cfg is None:
                return JSONResponse(
                    status_code=503,
                    content={"success": False, "message": "平台存储(MinIO)未配置"},
                )
            bucket, key = parse_s3_uri(storage_uri)
            s3 = s3_settings_for_duckdb(cfg)
            rows, columns, total = await asyncio.to_thread(
                _duck_query,
                f"s3://{bucket}/{key}",
                version.format,
                sql_raw,
                limit,
                offset,
                s3,
            )
        else:
            p = Path(storage_uri)
            if not p.exists():
                return JSONResponse(
                    content={
                        "data": [],
                        "columns": [],
                        "total": 0,
                        "success": True,
                        "message": "产物文件缺失",
                    }
                )
            rows, columns, total = await asyncio.to_thread(
                _duck_query, str(p), version.format, sql_raw, limit, offset, None
            )
    except ExternalStoreError as exc:
        return JSONResponse(
            status_code=503,
            content={"success": False, "message": f"读取存储失败:{exc}"},
        )
    except Exception as exc:  # noqa: BLE001 DuckDB SQL/连接错误统一上报
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": f"查询失败:{exc}"},
        )
    return JSONResponse(
        content={"data": rows, "columns": columns, "total": total, "success": True}
    )


# ---------------------------------------------------------------------------
# 外部 S3 数据托管:登记 / 取消(#18)
# ---------------------------------------------------------------------------
def _new_dataset_id() -> str:
    """形如 ``dset-`` + UUIDv7 十六进制(与 landing 同款,时间前缀天然按创建时间排序)。"""
    return f"dset-{uuid7_hex()}"


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
    # 先整批校验 key 格式;任一非法即整批 400(未 stat、未 add session,无脏状态)
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


@router.post("/datasets/{dataset_id}/versions")
async def create_dataset_version(
    dataset_id: str,
    session: SessionDep,
    user: Annotated[User | None, Depends(current_user)] = None,
) -> JSONResponse:
    """显式新建一个空版本(数据集详情页「新建版本」按钮)。

    与 `landing._target_draft_version`(上传时的隐式选取:latest 是 draft 则复用,
    是 published 才开 v+1)是两条独立路径——这是用户的显式动作,不管当前最新版本
    状态如何,永远新建 v(max+1),不克隆成员、不复用现有 draft。同步在 uploads
    桶写一个空占位对象,让 `v<n>/` 目录在「文件管理」页立即可见(S3 无空目录,
    靠公共前缀+至少一个对象体现)。
    """
    dataset = await session.get(Dataset, dataset_id)
    if dataset is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "数据集不存在"},
        )
    if not await dataset_acl.can_access(session, user, dataset_id, "edit"):
        return JSONResponse(
            status_code=403,
            content={"success": False, "message": "无该数据集写入权限"},
        )
    max_no = await session.scalar(
        select(func.max(DatasetVersion.version_no)).where(
            DatasetVersion.dataset_id == dataset_id
        )
    )
    next_no = (max_no or 0) + 1
    version = DatasetVersion(
        id=_new_version_id(),
        dataset_id=dataset_id,
        version_no=next_no,
        storage_uri=f"pending://{dataset_id}/v{next_no}/",
        format="jsonl",
        origin="managed",
        publish_status="draft",
    )
    session.add(version)
    await session.commit()
    await session.refresh(version)
    try:
        cfg = platform_config()
        await upload_object(
            cfg,
            settings.storage_minio_upload_bucket,
            f"{dataset_id}/v{next_no}/.keep",
            io.BytesIO(b""),
            0,
        )
    except ExternalStoreError:
        pass  # 占位对象纯展示性,写入失败不影响版本已创建
    return JSONResponse(content=_version_item(version))


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
        # 不变量:同数据集同时只允许一个 published 版本——发布此版本前,
        # 下架同数据集其他已发布版本(算法侧消费唯一的"当前发布版")。
        others = (
            await session.scalars(
                select(DatasetVersion)
                .where(DatasetVersion.dataset_id == version.dataset_id)
                .where(DatasetVersion.publish_status == "published")
                .where(DatasetVersion.id != version_id)
            )
        ).all()
        for o in others:
            o.publish_status = "unpublished"
            o.published_at = None
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


# ===== 消费导出:算法工程师取「已发布」训练集(端到端闭环终点)=====
# 发布门的消费侧对偶:只有 publish_status=published 的版本可被导出/下载,
# 维持「published ⟹ 可被消费、draft/unpublished 不可流出」。读端点与平台其余
# 读路径一致不强制登录,门控落在发布状态上(草稿区数据取不出去)。


async def _download_zip(
    version: DatasetVersion,
    members: list[DatasetMemberRead],
    session: AsyncSession,
) -> JSONResponse | FileResponse:
    """打包版本全部成员为 zip 流式下发(单文件也打包——下载体验一致)。

    s3 成员从对象存储拉字节(cached_bytes),本地成员读盘;同名成员自动加序号去重;
    拉取/读取失败的成员跳过;响应结束(BackgroundTask)清理临时文件。
    """
    has_s3 = any(m.bucket for m in members)
    cfg = await _version_storage_cfg(version, session) if has_s3 else None
    if has_s3 and cfg is None:
        return JSONResponse(
            status_code=503,
            content={"success": False, "message": "平台存储(MinIO)未配置"},
        )
    try:
        own_bucket, _ = parse_s3_uri(version.storage_uri)
    except ExternalStoreError:
        own_bucket = ""
    tmp = Path(tempfile.mktemp(suffix=".zip"))
    used: set[str] = set()
    # 记录被跳过的成员及原因(全部跳过时诚实回因,不再笼统报「无可用成员」,fail-loud)
    skipped: list[str] = []
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zf:
            for m in members:
                if m.bucket:  # s3 成员
                    try:
                        data = await cached_bytes(
                            cfg, m.bucket or own_bucket, m.key
                        )
                    except ExternalStoreError as exc:
                        skipped.append(f"{m.name or m.key}:S3 拉取失败({exc})")
                        continue
                else:  # 本地路径成员(managed 本地 jsonl)
                    p = Path(m.key)
                    if not p.exists():
                        skipped.append(
                            f"{m.name or m.key}:本地文件不存在({m.key});"
                            "该版本数据存于其它部署机磁盘,请在数据所在机器下载,"
                            "或改用对象存储(s3://)的版本"
                        )
                        continue
                    data = p.read_bytes()
                name = m.name or Path(m.key).name or "file"
                if name in used:
                    pp = Path(name)
                    n = 1
                    while f"{pp.stem} ({n}){pp.suffix}" in used:
                        n += 1
                    name = f"{pp.stem} ({n}){pp.suffix}"
                used.add(name)
                zf.writestr(name, data)
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": f"打包失败:{exc}"},
        )
    if not used:
        tmp.unlink(missing_ok=True)
        reason = "；".join(skipped[:5]) if skipped else "成员列表为空"
        return JSONResponse(
            status_code=410,
            content={
                "success": False,
                "message": f"无可下载的成员文件:{reason}",
            },
        )
    # 文件名用「数据集名 + 版本号」;名取不到回退 dataset_id;剥文件名非法字符
    ds = await session.get(Dataset, version.dataset_id)
    raw_name = ds.name if ds else version.dataset_id
    safe_name = (
        "".join("_" if c in '\\/:*?"<>|' else c for c in raw_name).strip()
        or version.dataset_id
    )
    filename = f"{safe_name}_v{version.version_no}.zip"
    return FileResponse(
        str(tmp),
        media_type="application/zip",
        filename=filename,
        background=BackgroundTask(lambda t=tmp: t.unlink(missing_ok=True)),
    )


class DatasetVersionUpdate(CamelModel):
    """版本元数据可编辑字段(训练用途 / 说明 / schema变体)。"""

    train_type: str | None = None
    note: str | None = None
    schema_variant: str | None = None


@router.patch("/dataset-versions/{version_id}", response_model=None)
async def update_dataset_version(
    version_id: str,
    payload: DatasetVersionUpdate,
    session: SessionDep,
    user: Annotated[User | None, Depends(current_user)] = None,
) -> JSONResponse:
    """更新版本元数据(训练用途/说明/schema变体)。需登录,无需 admin。"""
    version = await session.get(DatasetVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="Version not found")
    if payload.train_type is not None:
        version.train_type = payload.train_type or None
    if payload.note is not None:
        version.note = payload.note or None
    if payload.schema_variant is not None:
        version.schema_variant = payload.schema_variant or None
    await session.commit()
    await session.refresh(version)
    return JSONResponse(content=_version_item(version))


@router.delete("/dataset-versions/{version_id}", response_model=None)
async def delete_dataset_version(
    version_id: str,
    session: SessionDep,
    user: Annotated[User | None, Depends(current_user)] = None,
) -> JSONResponse:
    """删除数据集版本(仅允许删除 draft 状态版本)。"""
    version = await session.get(DatasetVersion, version_id)
    if version is None:
        raise HTTPException(status_code=404, detail="Version not found")
    if version.publish_status != "draft":
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "只能删除草稿(draft)状态的版本"},
        )
    await session.delete(version)
    await session.commit()
    return JSONResponse(content={"success": True})


@router.get("/dataset-versions/{version_id}/download", response_model=None)
async def download_version(
    version_id: str, session: SessionDep
) -> JSONResponse | FileResponse:
    """导出/下载一个版本的数据(任意版本均可,不再设发布门控)。

    统一打包为 zip 下发(单文件亦打包,体验一致);成员含本地文件与/或 s3 对象。
    """
    version = await session.get(DatasetVersion, version_id)
    if version is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "数据集版本不存在"},
        )
    try:
        members = await _members_of(version, session)
    except ExternalStoreError as exc:
        return JSONResponse(
            status_code=503,
            content={"success": False, "message": f"读取成员失败:{exc}"},
        )
    # 单成员 s3 版本:直接预签名 302,让训练平台/浏览器直连 MinIO 拉文件
    # (免后端中转打包,跨机器通用,S3 协议)。多成员/本地版本仍走 zip 打包。
    if len(members) == 1 and members[0].bucket:
        cfg = await _version_storage_cfg(version, session)
        if cfg is None:
            return JSONResponse(
                status_code=503,
                content={"success": False, "message": "平台存储(MinIO)未配置"},
            )
        try:
            url = await presigned_get_url(cfg, members[0].bucket, members[0].key)
        except ExternalStoreError as exc:
            return JSONResponse(
                status_code=503,
                content={"success": False, "message": f"生成下载链接失败:{exc}"},
            )
        return RedirectResponse(url, status_code=302)
    return await _download_zip(version, members, session)


@router.post("/dataset-versions/{version_id}/export-s3", response_model=None)
async def export_version_to_s3(
    version_id: str, body: ExportS3Request, session: SessionDep
) -> JSONResponse:
    """导出一个版本到外部 S3 数据源(下载/导出至 S3,download 的对偶,任意版本均可)。

    把版本各成员(本地 / 平台 / 托管 S3)读出后上传到目标 s3 数据源的
    ``bucket[/prefix]``——**读源、写目标**,绝不回写托管源对象
    (同源同桶同 key 会被拦)。返回导出对象数。
    """
    version = await session.get(DatasetVersion, version_id)
    if version is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "数据集版本不存在"},
        )
    if not body.bucket:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "请选择导出目标桶"},
        )
    ds = await session.get(DataSource, body.datasource_id)
    if ds is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "目标数据源不存在"},
        )
    if ds.type != "s3":
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "仅支持导出到 s3 类型数据源"},
        )
    try:
        members = await _members_of(version, session)
    except ExternalStoreError as exc:
        return JSONResponse(
            status_code=503,
            content={"success": False, "message": f"读取成员失败:{exc}"},
        )
    has_s3 = any(m.bucket for m in members)
    src_cfg = await _version_storage_cfg(version, session) if has_s3 else None
    if has_s3 and src_cfg is None:
        return JSONResponse(
            status_code=503,
            content={"success": False, "message": "平台存储(MinIO)未配置"},
        )
    try:
        own_bucket, _own_key = parse_s3_uri(version.storage_uri)
    except ExternalStoreError:
        own_bucket = ""

    prefix = (body.prefix or "").strip().strip("/")
    exported = 0
    used: set[str] = set()
    # 记录被跳过的成员及原因(全部跳过时诚实回因,fail-loud)
    skipped: list[str] = []
    for m in members:
        if m.bucket:  # s3 成员:从源对象存储取字节(命中物化缓存则免重复下载)
            try:
                data = await cached_bytes(src_cfg, m.bucket or own_bucket, m.key)
            except ExternalStoreError as exc:
                skipped.append(f"{m.name or m.key}:S3 拉取失败({exc})")
                continue
        else:  # 本地路径成员(managed 本地 jsonl)
            p = Path(m.key)
            if not p.exists():
                skipped.append(
                    f"{m.name or m.key}:本地文件不存在({m.key});"
                    "该版本数据存于其它部署机磁盘,请在数据所在机器导出,"
                    "或改用对象存储(s3://)的版本"
                )
                continue
            data = await asyncio.to_thread(p.read_bytes)
        name = m.name or Path(m.key).name or "file"
        if name in used:  # 同名成员加序号去重(与 _download_zip 一致)
            pp = Path(name)
            n = 1
            while f"{pp.stem} ({n}){pp.suffix}" in used:
                n += 1
            name = f"{pp.stem} ({n}){pp.suffix}"
        used.add(name)
        dest_key = f"{prefix}/{name}" if prefix else name
        # 红线:绝不回写托管源对象(同数据源 + 同桶 + 同 key)→ 阻止
        if (
            version.source_datasource_id == ds.id
            and m.bucket == body.bucket
            and m.key == dest_key
        ):
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "message": "导出目标与托管源对象相同,已阻止回写源;请换目标桶或前缀",
                },
            )
        try:
            await upload_object(
                ds.config, body.bucket, dest_key, io.BytesIO(data), len(data)
            )
        except ExternalStoreError as exc:
            return JSONResponse(
                status_code=502,
                content={"success": False, "message": f"导出失败:{exc}"},
            )
        exported += 1

    if exported == 0:
        reason = "；".join(skipped[:5]) if skipped else "成员列表为空"
        return JSONResponse(
            status_code=410,
            content={
                "success": False,
                "message": f"无可导出的成员文件:{reason}",
            },
        )
    target = f"s3://{body.bucket}/{prefix}" if prefix else f"s3://{body.bucket}"
    return JSONResponse(
        content={"success": True, "data": {"exported": exported, "target": target}}
    )


# ---- 数据集级 ACL(共享/成员权限)----------------------------------------------
# 管理 (dataset × subject × level) 授权条目;需 admin 级(owner/超管/ACL-admin)。
# 权限本体挂在菜单上(系统 perms),这里的 ACL 是数据集维度的共享控制。


def _new_acl_id() -> str:
    """生成形如 dac-<6位hex> 的 ACL 行主键。"""
    return f"dac-{secrets.token_hex(3)}"


def _acl_payload(row: DatasetAcl) -> dict:
    return AclRead.model_validate(row).model_dump(by_alias=True, mode="json")


async def _require_acl_admin(
    session: SessionDep, dataset_id: str, user: Annotated[User, Depends(require_user)]
) -> JSONResponse | None:
    """校验当前用户对该数据集有 admin 级;返回 None 表示放行,否则返回 403/404 响应。"""
    if await session.get(Dataset, dataset_id) is None:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "数据集不存在"}
        )
    if not await dataset_acl.can_access(session, user, dataset_id, "admin"):
        return JSONResponse(
            status_code=403, content={"success": False, "message": "无权限"}
        )
    return None


@router.get("/datasets/{dataset_id}/acl")
async def list_dataset_acl(
    dataset_id: str,
    session: SessionDep,
    user: Annotated[User, Depends(require_user)],
) -> JSONResponse:
    """列出数据集的授权条目(需 admin 级);subjectName 批量解析显示名,避免前端只拿到 subjectId(UUID)。"""
    denied = await _require_acl_admin(session, dataset_id, user)
    if denied is not None:
        return denied
    rows = (
        await session.scalars(
            select(DatasetAcl)
            .where(DatasetAcl.dataset_id == dataset_id)
            .order_by(DatasetAcl.created_at)
        )
    ).all()
    # 批量解析主体显示名:user→display_name/username,role→name,all→固定文案
    name_map: dict[tuple[str, str], str] = {}
    user_ids = [r.subject_id for r in rows if r.subject_type == "user"]
    role_ids = [r.subject_id for r in rows if r.subject_type == "role"]
    if user_ids:
        for u in (await session.scalars(select(User).where(User.id.in_(user_ids)))).all():
            name_map[("user", u.id)] = u.display_name or u.username
    if role_ids:
        for rl in (await session.scalars(select(Role).where(Role.id.in_(role_ids)))).all():
            name_map[("role", rl.id)] = rl.name

    def name_of(r: DatasetAcl) -> str:
        if r.subject_type == "all":
            return "组织内所有人"
        return name_map.get((r.subject_type, r.subject_id), r.subject_id)

    return JSONResponse(
        {
            "data": [{**_acl_payload(r), "subjectName": name_of(r)} for r in rows],
            "success": True,
        }
    )


def _like_q(q: str) -> str:
    r"""转义 ILIKE 通配符(%/_/\),防止 q 被当成通配符导致全员目录枚举。"""
    return q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("/datasets/{dataset_id}/acl/candidates")
async def search_acl_candidates(
    dataset_id: str,
    session: SessionDep,
    user: Annotated[User, Depends(require_user)],
    q: str = "",
    type: str = "user",  # noqa: A002 - 与查询参数名一致
) -> JSONResponse:
    """模糊搜索可授权主体(用户/角色),供 ACL 抽屉的"指定主体"选择;需 admin 级,避免泄露全员目录。"""
    denied = await _require_acl_admin(session, dataset_id, user)
    if denied is not None:
        return denied
    if type not in ("user", "role"):
        return JSONResponse(
            status_code=400, content={"success": False, "message": "type 非法"}
        )
    if type == "user":
        rows = (
            await session.scalars(
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
    else:
        rows = (
            await session.scalars(
                select(Role).where(Role.name.ilike(f"%{_like_q(q)}%", escape="\\")).order_by(Role.name).limit(20)
            )
        ).all()
        data = [{"id": r.id, "name": r.name, "type": "role"} for r in rows]
    return JSONResponse({"data": data, "success": True})


@router.post("/datasets/{dataset_id}/acl")
async def add_dataset_acl(
    dataset_id: str,
    body: AclCreate,
    session: SessionDep,
    user: Annotated[User, Depends(require_user)],
) -> JSONResponse:
    """新增授权条目;重复授权 (dataset,subject) → 409。"""
    denied = await _require_acl_admin(session, dataset_id, user)
    if denied is not None:
        return denied
    if body.subject_type not in ("user", "role", "all") or body.level not in (
        "view",
        "edit",
        "admin",
    ):
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "subject_type/level 非法"},
        )
    # user/role 必须带真实主体 id(all 的 subjectId 由下面归一为 "*",不受此约束)
    if body.subject_type in ("user", "role") and not body.subject_id.strip():
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "subject_id 不能为空"},
        )
    # "组织内所有人" 整表只此一行,subject_id 固定,忽略传入值
    subject_id = (
        dataset_acl.ALL_SUBJECT_ID if body.subject_type == "all" else body.subject_id
    )
    exists = await session.scalar(
        select(DatasetAcl.id).where(
            DatasetAcl.dataset_id == dataset_id,
            DatasetAcl.subject_type == body.subject_type,
            DatasetAcl.subject_id == subject_id,
        )
    )
    if exists is not None:
        return JSONResponse(
            status_code=409,
            content={"success": False, "message": "该主体已授权,请用修改"},
        )
    row = DatasetAcl(
        id=_new_acl_id(),
        dataset_id=dataset_id,
        subject_type=body.subject_type,
        subject_id=subject_id,
        level=body.level,
    )
    session.add(row)
    await session.commit()
    await session.refresh(row)
    return JSONResponse({"data": _acl_payload(row), "success": True})


@router.put("/datasets/{dataset_id}/acl/{acl_id}")
async def update_dataset_acl(
    dataset_id: str,
    acl_id: str,
    body: AclUpdate,
    session: SessionDep,
    user: Annotated[User, Depends(require_user)],
) -> JSONResponse:
    """修改某授权条目的级别(需 admin 级)。"""
    denied = await _require_acl_admin(session, dataset_id, user)
    if denied is not None:
        return denied
    if body.level not in ("view", "edit", "admin"):
        return JSONResponse(
            status_code=400, content={"success": False, "message": "level 非法"}
        )
    row = await session.get(DatasetAcl, acl_id)
    if row is None or row.dataset_id != dataset_id:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "授权条目不存在"}
        )
    row.level = body.level
    await session.commit()
    await session.refresh(row)
    return JSONResponse({"data": _acl_payload(row), "success": True})


@router.delete("/datasets/{dataset_id}/acl/{acl_id}")
async def delete_dataset_acl(
    dataset_id: str,
    acl_id: str,
    session: SessionDep,
    user: Annotated[User, Depends(require_user)],
) -> JSONResponse:
    """删除某授权条目(需 admin 级)。"""
    denied = await _require_acl_admin(session, dataset_id, user)
    if denied is not None:
        return denied
    row = await session.get(DatasetAcl, acl_id)
    if row is None or row.dataset_id != dataset_id:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "授权条目不存在"}
        )
    await session.delete(row)
    await session.commit()
    return JSONResponse({"success": True})
