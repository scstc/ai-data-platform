"""加工任务路由:算子目录、建任务并执行(子进程跑 dj-process)、列表、详情。"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import get_session
from app.models.dataset import Dataset
from app.models.dataset_version import DatasetVersion
from app.models.job import Job
from app.models.job_input import JobInput
from app.schemas.common import CamelModel, PageResponse
from app.schemas.job import JobCreate, JobRead, OperatorSpec
from app.services import operator_catalog as oc
from app.services.engine import EngineError, run_preview, run_process_job

router = APIRouter(tags=["jobs"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


class JobItemResponse(CamelModel):
    """单个加工任务响应：{data:{...}, success:true}。"""

    data: JobRead
    success: bool = True


class PreviewRequest(CamelModel):
    """样例试跑入参:在某数据集版本前 N 行上跑算子,不建版本、不写 DB。"""

    dataset_version_id: str
    operators: list[OperatorSpec]
    sample_size: int = 20


def _new_job_id() -> str:
    return f"job-{secrets.token_hex(3)}"


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


async def _build_output(session: AsyncSession, job_id: str) -> dict | None:
    """查该任务的产物版本(按 produced_by_job_id 反查)。"""
    stmt = (
        select(DatasetVersion, Dataset)
        .join(Dataset, Dataset.id == DatasetVersion.dataset_id)
        .where(DatasetVersion.produced_by_job_id == job_id)
        .order_by(DatasetVersion.created_at.desc())
    )
    row = (await session.execute(stmt)).first()
    if row is None:
        return None
    version, dataset = row
    return {
        "datasetId": dataset.id,
        "datasetName": dataset.name,
        "versionId": version.id,
        "versionNo": version.version_no,
        "rows": version.rows,
    }


async def _build_input(session: AsyncSession, job_id: str) -> dict | None:
    """查该任务的输入版本(经 job_inputs 血缘边反查,镜像 _build_output)。"""
    stmt = (
        select(DatasetVersion, Dataset)
        .join(JobInput, JobInput.dataset_version_id == DatasetVersion.id)
        .join(Dataset, Dataset.id == DatasetVersion.dataset_id)
        .where(JobInput.job_id == job_id)
    )
    row = (await session.execute(stmt)).first()
    if row is None:
        return None
    version, dataset = row
    return {
        "datasetId": dataset.id,
        "datasetName": dataset.name,
        "versionId": version.id,
        "versionNo": version.version_no,
    }


def _item(
    job: Job, output: dict | None = None, input_: dict | None = None
) -> dict:
    read = JobRead.model_validate(job)
    if output is not None:
        read.output = output
    if input_ is not None:
        read.input = input_
    return JobItemResponse(data=read).model_dump(by_alias=True, mode="json")


@router.get("/jobs", response_model=PageResponse[JobRead])
async def list_jobs(
    session: SessionDep,
    current: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, alias="pageSize")] = 10,
    type_: Annotated[str | None, Query(alias="type")] = None,
) -> PageResponse[JobRead]:
    """分页列出加工任务,按创建时间倒序;可按 type 过滤(如 type=quality)。"""
    count_stmt = select(func.count()).select_from(Job)
    list_stmt = select(Job)
    if type_:
        count_stmt = count_stmt.where(Job.type == type_)
        list_stmt = list_stmt.where(Job.type == type_)
    total = await session.scalar(count_stmt) or 0
    rows = (
        await session.scalars(
            list_stmt.order_by(Job.created_at.desc())
            .offset((current - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    data: list[JobRead] = []
    for r in rows:
        read = JobRead.model_validate(r)
        read.input = await _build_input(session, r.id)
        data.append(read)
    return PageResponse[JobRead](data=data, total=total)


@router.post("/jobs")
async def create_job(body: JobCreate, session: SessionDep) -> JSONResponse:
    """新建并执行加工任务:对一个数据集版本跑算子流水线 → 产出新版本。"""
    if not body.operators:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "请至少选择一个算子"},
        )
    known = oc.operator_names()
    unknown = [o.name for o in body.operators if o.name not in known]
    if unknown:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": f"未知算子:{', '.join(unknown)}"},
        )
    # 资源前置校验:把算子路由到合适后端,跑不了的直接拦截并给原因
    llm_configured = bool(settings.openai_api_key)
    blocked = [
        reason
        for o in body.operators
        if (reason := oc.runnable_reason(o.name, llm_configured=llm_configured))
    ]
    if blocked:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "；".join(blocked)},
        )

    input_version = await session.get(DatasetVersion, body.dataset_version_id)
    if input_version is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "数据集版本不存在"},
        )

    job = Job(
        id=_new_job_id(),
        name=body.name,
        type=body.type,
        state="running",
        progress=0,
        created_by="admin",
        started_at=_now(),
    )
    session.add(job)
    await session.commit()

    try:
        _version, yaml_text, log_path = await run_process_job(
            session,
            job_id=job.id,
            input_version=input_version,
            operators=[o.model_dump() for o in body.operators],
        )
        job.state = "success"
        job.progress = 100
        job.config_yaml = yaml_text
        job.logs_uri = log_path
    except EngineError as exc:
        job.state = "failed"
        job.error = str(exc)
    job.finished_at = _now()
    await session.commit()
    await session.refresh(job)

    output = await _build_output(session, job.id)
    input_ = await _build_input(session, job.id)
    return JSONResponse(content=_item(job, output, input_))


@router.post("/jobs/preview")
async def preview_job(body: PreviewRequest, session: SessionDep) -> JSONResponse:
    """样例试跑:在数据集版本前 N 行上跑算子流水线,返回加工前后样本。

    不建 DatasetVersion、不写 DB;校验与 create_job 一致。
    """
    if not body.operators:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "请至少选择一个算子"},
        )
    known = oc.operator_names()
    unknown = [o.name for o in body.operators if o.name not in known]
    if unknown:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": f"未知算子:{', '.join(unknown)}"},
        )
    llm_configured = bool(settings.openai_api_key)
    blocked = [
        reason
        for o in body.operators
        if (reason := oc.runnable_reason(o.name, llm_configured=llm_configured))
    ]
    if blocked:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "；".join(blocked)},
        )

    size = max(1, min(body.sample_size, 200))
    input_version = await session.get(DatasetVersion, body.dataset_version_id)
    if input_version is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "数据集版本不存在"},
        )

    try:
        result = await run_preview(
            input_version=input_version,
            operators=[o.model_dump() for o in body.operators],
            sample_size=size,
        )
    except EngineError as exc:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": str(exc)},
        )
    return JSONResponse({"data": result, "success": True})


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, session: SessionDep) -> JSONResponse:
    """加工任务详情(含产物版本与输入版本)。"""
    job = await session.get(Job, job_id)
    if job is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "任务不存在"},
        )
    output = await _build_output(session, job.id)
    input_ = await _build_input(session, job.id)
    return JSONResponse(content=_item(job, output, input_))
