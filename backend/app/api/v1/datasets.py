"""数据集路由:上传落地 POST /datasets/upload、列表 GET /datasets、详情 GET /datasets/{id}。"""

from __future__ import annotations

import json
import secrets
import shutil
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.core.config import settings
from app.core.db import get_session
from app.models.dataset import Dataset
from app.models.dataset_version import DatasetVersion
from app.models.datasource import DataSource
from app.models.job_input import JobInput
from app.schemas.common import CamelModel, PageResponse
from app.schemas.dataset import (
    DatasetDetailRead,
    DatasetRead,
    DatasetUpdate,
    DatasetVersionRead,
    HostS3Request,
)
from app.services.external_store import (
    ExternalStoreError,
    head_records,
    parse_s3_uri,
    stat_object,
)
from app.services.landing import (
    LANDABLE_FORMATS,
    LandingError,
    UnsupportedFormatError,
    land_upload,
)

router = APIRouter(tags=["datasets"])

# 依赖别名(与其他路由同款,规避 ruff B008)
SessionDep = Annotated[AsyncSession, Depends(get_session)]
UploadFileDep = Annotated[UploadFile, File(...)]
NameForm = Annotated[str | None, Form()]
DataTypeForm = Annotated[str | None, Form()]
DescForm = Annotated[str | None, Form()]
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
) -> JSONResponse:
    """本地上传连接器:文件 → 规范化 jsonl → 受管 Dataset(v1) + DatasetVersion。"""
    filename = file.filename or ""
    fmt = _file_ext(filename)
    content = await file.read()
    try:
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
                "jsonl/json/csv/tsv/txt/xlsx/xls/html/pdf/doc/docx/ppt/pptx",
            },
        )
    except LandingError as exc:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": f"解析失败:{exc}"},
        )

    payload = DatasetResult(data=_to_detail(dataset, [version]))
    return JSONResponse(content=payload.model_dump(by_alias=True, mode="json"))


@router.get("/datasets", response_model=PageResponse[DatasetRead])
async def list_datasets(
    session: SessionDep,
    current: int = Query(1, ge=1),
    page_size: int = Query(10, ge=1, alias="pageSize"),
    name: str | None = Query(None),
    data_type: str | None = Query(None, alias="dataType"),
    creator: str | None = Query(None),
    created_start: CreatedStartQuery = None,
    created_end: CreatedEndQuery = None,
) -> PageResponse[DatasetRead]:
    """分页查询数据集,按创建时间倒序;按元数据条件过滤(向后兼容)。"""
    conds = []
    if name:
        conds.append(Dataset.name.ilike(f"%{name}%"))
    if data_type:
        conds.append(Dataset.data_type == data_type)
    if creator:
        conds.append(Dataset.creator.ilike(f"%{creator}%"))
    if created_start is not None:
        conds.append(Dataset.created_at >= created_start)
    if created_end is not None:
        conds.append(Dataset.created_at <= created_end)
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
    data = []
    for r in rows:
        item = DatasetRead.model_validate(r)
        item.hosted = r.id in hosted_ids
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
    payload = DatasetResult(data=_to_detail(dataset, list(versions)))
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
    payload = DatasetResult(data=_to_detail(dataset, list(versions)))
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
    if not await _purge_dataset(session, dataset_id):
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "数据集不存在"},
        )
    await session.commit()
    _rmdir(dataset_id)
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
    deleted = [
        ds_id for ds_id in body.ids if await _purge_dataset(session, ds_id)
    ]
    await session.commit()
    for ds_id in deleted:
        _rmdir(ds_id)
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

    # hosted:按需从 S3 取前 offset+limit 条再切片(预览成本由取前 N 缓解)
    if version.origin == "hosted":
        if not version.source_datasource_id:
            return JSONResponse(
                status_code=400,
                content={"success": False, "message": "托管版本缺少数据源引用"},
            )
        ds = await session.get(DataSource, version.source_datasource_id)
        if ds is None:
            return JSONResponse(
                status_code=400,
                content={"success": False, "message": "托管版本对应的数据源已不存在"},
            )
        try:
            bucket, key = parse_s3_uri(version.storage_uri)
            head = await head_records(
                ds.config, bucket, key, version.format, offset + limit
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
    created: list[DatasetDetailRead] = []
    for dataset, version in pairs:
        await session.refresh(dataset)
        await session.refresh(version)
        created.append(_to_detail(dataset, [version]))
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
