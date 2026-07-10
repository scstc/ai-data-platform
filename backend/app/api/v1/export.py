"""数据集交付/导出(export)API(治理整改 G8/G9)。

Job.type='export'。把治理后版本导出为训练三件套(train + stats + card)落 S3。
不产新 DatasetVersion;report 走 job.logs_uri 同目录 report.json。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_user, require_admin, require_perm, require_user
from app.api.v1.jobs import (
    BatchDeleteRequest,
    SessionDep,
    _acl_job_filter,
    _binary_block,
    _build_input,
    _build_output,
    _dataset_job_filter,
    _item,
    _new_job_id,
    _now,
)
from app.models.dataset_version import DatasetVersion
from app.models.job import Job
from app.models.job_input import JobInput
from app.models.user import User
from app.schemas.common import PageResponse
from app.schemas.export import ExportJobCreate, ExportReport
from app.schemas.job import JobRead
from app.services import dataset_acl, job_runner

router = APIRouter(tags=["export"])

_EXPORT_TYPE = "export"


async def _start_export(
    session: AsyncSession,
    body: ExportJobCreate,
    user: User | None = None,
) -> JSONResponse:
    """校验输入版本 → 建任务 → spawn → 立即返回 pending。"""
    version = await session.get(DatasetVersion, body.dataset_version_id)
    if version is None:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "数据集版本不存在"}
        )

    # 数据集 ACL:交付消费该数据集,要求 edit 及以上(view 只能查看数据)
    if not await dataset_acl.can_access(session, user, version.dataset_id, "edit"):
        return JSONResponse(
            status_code=403,
            content={"success": False, "message": "无数据集编辑权限,无法导出"},
        )
    if (blocked := _binary_block(version)) is not None:
        return blocked
    if version.format == "manifest":
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": "多模态 manifest 交付走媒体目录+清单(规范 §8.6)",
            },
        )
    job = Job(
        id=_new_job_id(),
        name=body.name,
        type=_EXPORT_TYPE,
        state="pending",
        progress=0,
        created_by=user.username if user else "admin",
        spec=body.model_dump(mode="json"),
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    job_runner.spawn(job.id)
    return JSONResponse(content=_item(job))


@router.post("/export/jobs")
async def create_export_job(
    body: ExportJobCreate,
    session: SessionDep,
    user: Annotated[User, Depends(require_user)],
) -> JSONResponse:
    """新建交付任务并异步执行(治理后版本 → 训练三件套落 S3)。

    需登录 + 输入数据集 ACL ≥ edit(超管/owner 隐式满足)。
    """
    return await _start_export(session, body, user=user)


@router.get(
    "/export/jobs",
    response_model=PageResponse[JobRead],
    dependencies=[Depends(require_perm("governance:make:list"))],
)
async def list_export_jobs(
    session: SessionDep,
    current: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, alias="pageSize")] = 10,
    dataset_id: Annotated[str | None, Query(alias="datasetId")] = None,
    user: Annotated[User | None, Depends(current_user)] = None,
) -> PageResponse[JobRead]:
    """分页列出交付任务。

    非超管只看到自己创建的 + 授权数据集上的任务。
    """
    count_stmt = select(func.count()).select_from(Job).where(Job.type == _EXPORT_TYPE)
    list_stmt = select(Job).where(Job.type == _EXPORT_TYPE)
    if dataset_id:
        count_stmt = count_stmt.where(_dataset_job_filter(dataset_id))
        list_stmt = list_stmt.where(_dataset_job_filter(dataset_id))
    if (acl_cond := await _acl_job_filter(session, user)) is not None:
        count_stmt = count_stmt.where(acl_cond)
        list_stmt = list_stmt.where(acl_cond)
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
        read.can_rerun = bool(r.spec)
        read.input = await _build_input(session, r.id)
        data.append(read)
    return PageResponse[JobRead](data=data, total=total)


@router.get("/export/jobs/{job_id}")
async def get_export_job(job_id: str, session: SessionDep) -> JSONResponse:
    """交付任务详情。"""
    job = await session.get(Job, job_id)
    if job is None or job.type != _EXPORT_TYPE:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "交付任务不存在"}
        )
    output = await _build_output(session, job.id)
    input_ = await _build_input(session, job.id)
    return JSONResponse(content=_item(job, output, input_))


@router.post("/export/jobs/{job_id}/rerun")
async def rerun_export_job(
    job_id: str,
    session: SessionDep,
    user: Annotated[User, Depends(require_user)],
) -> JSONResponse:
    """用原 spec 重跑。需登录 + 输入数据集 ACL ≥ edit。"""
    job = await session.get(Job, job_id)
    if job is None or job.type != _EXPORT_TYPE:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "交付任务不存在"}
        )
    if not job.spec:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "该任务无可重跑的配置"},
        )
    try:
        spec = ExportJobCreate.model_validate(job.spec)
    except ValidationError:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "任务配置已损坏,无法重跑"},
        )
    return await _start_export(session, spec, user=user)


@router.post("/export/jobs/{job_id}/stop", dependencies=[Depends(require_admin)])
async def stop_export_job(job_id: str, session: SessionDep) -> JSONResponse:
    """停止运行中/排队的交付任务。"""
    job = await session.get(Job, job_id)
    if job is None or job.type != _EXPORT_TYPE:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "交付任务不存在"}
        )
    if job.state not in ("pending", "running"):
        return JSONResponse(
            status_code=409,
            content={"success": False, "message": "任务不在运行中,无法停止"},
        )
    job_runner.request_cancel(job_id)
    job.state = "cancelled"
    job.finished_at = _now()
    await session.commit()
    return JSONResponse(content={"success": True})


async def _delete_export_cascade(session: AsyncSession, job: Job) -> None:
    # export 不产 DatasetVersion,只需清血缘边 + 删 job
    await session.execute(delete(JobInput).where(JobInput.job_id == job.id))
    await session.delete(job)


@router.delete("/export/jobs/{job_id}", dependencies=[Depends(require_admin)])
async def delete_export_job(job_id: str, session: SessionDep) -> JSONResponse:
    """删除交付任务(交付物对象保留在 S3)。"""
    job = await session.get(Job, job_id)
    if job is None or job.type != _EXPORT_TYPE:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "交付任务不存在"}
        )
    if job.state == "running":
        return JSONResponse(
            status_code=409,
            content={"success": False, "message": "任务运行中,无法删除"},
        )
    await _delete_export_cascade(session, job)
    await session.commit()
    return JSONResponse(content={"success": True})


@router.post("/export/jobs/batch-delete", dependencies=[Depends(require_admin)])
async def batch_delete_export_jobs(
    body: BatchDeleteRequest, session: SessionDep
) -> JSONResponse:
    """批量删除交付任务。"""
    deleted = 0
    for job_id in body.ids:
        job = await session.get(Job, job_id)
        if job is None or job.type != _EXPORT_TYPE or job.state == "running":
            continue
        await _delete_export_cascade(session, job)
        deleted += 1
    await session.commit()
    return JSONResponse(content={"data": {"deleted": deleted}, "success": True})


@router.get("/export/jobs/{job_id}/report")
async def get_export_report(job_id: str, session: SessionDep) -> JSONResponse:
    """读 report.json(交付物清单/目标 URI/条数/分片/warnings)。"""
    job = await session.get(Job, job_id)
    if job is None or job.type != _EXPORT_TYPE:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "交付任务不存在"}
        )
    # export 不产版本,report 走 job.logs_uri 同目录
    if not job.logs_uri:
        spec = job.spec or {}
        empty = ExportReport(
            job_id=job.id,
            version_id=spec.get("dataset_version_id", ""),
            target_uri="",
            warnings=["任务尚未完成"] if job.state != "success" else [],
        )
        return JSONResponse(
            content={"data": empty.model_dump(mode="json"), "success": True}
        )
    report_path = Path(job.logs_uri).parent / "report.json"
    if not report_path.exists():
        return JSONResponse(
            status_code=404, content={"success": False, "message": "报告文件不存在"}
        )
    try:
        raw: dict[str, Any] = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": f"报告解析失败:{exc}"},
        )
    return JSONResponse(content={"data": raw, "success": True})
