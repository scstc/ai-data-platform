"""数据集路由:上传落地 POST /datasets/upload、列表 GET /datasets、详情 GET /datasets/{id}。"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_session
from app.models.dataset import Dataset
from app.models.dataset_version import DatasetVersion
from app.models.job_input import JobInput
from app.schemas.common import CamelModel, PageResponse
from app.schemas.dataset import DatasetDetailRead, DatasetRead, DatasetVersionRead
from app.services.landing import LandingError, UnsupportedFormatError, land_upload

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
    """组装数据集详情(元信息 + 版本列表)。"""
    detail = DatasetDetailRead.model_validate(dataset)
    detail.versions = [DatasetVersionRead.model_validate(v) for v in versions]
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
    data = [DatasetRead.model_validate(r) for r in rows]
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


async def _purge_dataset(session: AsyncSession, dataset_id: str) -> bool:
    """暂存删除一个数据集(版本 + 血缘边 + 元数据),不 commit;返回是否命中。"""
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


@router.delete("/datasets/{dataset_id}")
async def delete_dataset(dataset_id: str, session: SessionDep) -> JSONResponse:
    """删除数据集:级联删版本 + 清血缘边(job_inputs)+ 删磁盘产物。"""
    if not await _purge_dataset(session, dataset_id):
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "数据集不存在"},
        )
    await session.commit()
    _rmdir(dataset_id)
    return JSONResponse(content={"success": True})


@router.post("/datasets/batch-delete")
async def batch_delete_datasets(
    body: BatchDeleteRequest, session: SessionDep
) -> JSONResponse:
    """批量删除数据集,返回实际删除数量。"""
    deleted = [
        ds_id for ds_id in body.ids if await _purge_dataset(session, ds_id)
    ]
    await session.commit()
    for ds_id in deleted:
        _rmdir(ds_id)
    return JSONResponse(
        content={"data": {"deleted": len(deleted)}, "success": True}
    )


@router.get("/dataset-versions/{version_id}/preview")
async def preview_version(
    version_id: str,
    session: SessionDep,
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
) -> JSONResponse:
    """预览某版本的数据(读 jsonl,分页返回若干行 + 列名 + 总行数)。"""
    version = await session.get(DatasetVersion, version_id)
    if version is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "版本不存在"},
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
    # 列序:首行键序优先,再补后续行出现的新键
    columns: list[str] = []
    for rec in rows:
        for key in rec:
            if key not in columns:
                columns.append(key)
    return JSONResponse(
        content={
            "data": rows,
            "columns": columns,
            "total": version.rows or 0,
            "success": True,
        }
    )
