"""数据集构造层(construct)API:原始列 → 训练 schema(治理整改 G2/G3)。

Job.type='construct'。方式A确定性列映射,不需算子/LLM,故无白名单/needs_api 校验。
产物 origin='managed',打 train_type/schema_variant 元数据。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import delete, func, select, update
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
from app.core.config import settings
from app.models.dataset_version import DatasetVersion
from app.models.job import Job
from app.models.job_input import JobInput
from app.models.user import User
from app.schemas.common import PageResponse
from app.schemas.construct import ConstructJobCreate, ConstructReport
from app.schemas.job import JobRead
from app.services import dataset_acl, job_runner

router = APIRouter(tags=["construct"])

_CONSTRUCT_TYPE = "construct"


async def _start_construct(
    session: AsyncSession,
    body: ConstructJobCreate,
    user: User | None = None,
) -> JSONResponse:
    """构造版 _start_job:校验输入版本 → 建任务 → spawn → 立即返回 pending。

    goal 组合合法性已由 ConstructGoal 的 model_validator 在入参解析时兜住(422)。
    """
    input_version = await session.get(DatasetVersion, body.dataset_version_id)
    if input_version is None:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "数据集版本不存在"}
        )

    # 数据集 ACL:加工消费该数据集,要求 edit 及以上(view 只能查看数据)
    if not await dataset_acl.can_access(
        session, user, input_version.dataset_id, "edit"
    ):
        return JSONResponse(
            status_code=403,
            content={"success": False, "message": "无数据集编辑权限,无法发起加工"},
        )
    if (blocked_resp := _binary_block(input_version)) is not None:
        return blocked_resp
    if input_version.format == "manifest":
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": "多模态 manifest 暂不支持构造(占位符注入见规范 §8.5)",
            },
        )

    job = Job(
        id=_new_job_id(),
        name=body.name,
        type=_CONSTRUCT_TYPE,
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


@router.post("/construct/jobs")
async def create_construct_job(
    body: ConstructJobCreate,
    session: SessionDep,
    user: Annotated[User, Depends(require_user)],
) -> JSONResponse:
    """新建数据集构造任务并异步执行(原始列 → 训练 schema)。

    需登录 + 输入数据集 ACL ≥ edit(超管/owner 隐式满足)。
    """
    return await _start_construct(session, body, user=user)


@router.get(
    "/construct/jobs",
    response_model=PageResponse[JobRead],
    dependencies=[Depends(require_perm("governance:make:list"))],
)
async def list_construct_jobs(
    session: SessionDep,
    current: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, alias="pageSize")] = 10,
    dataset_id: Annotated[str | None, Query(alias="datasetId")] = None,
    user: Annotated[User | None, Depends(current_user)] = None,
) -> PageResponse[JobRead]:
    """分页列出构造任务;可按 datasetId 过滤。

    非超管只看到自己创建的 + 授权数据集上的任务。
    """
    count_stmt = (
        select(func.count()).select_from(Job).where(Job.type == _CONSTRUCT_TYPE)
    )
    list_stmt = select(Job).where(Job.type == _CONSTRUCT_TYPE)
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


@router.get("/construct/jobs/{job_id}")
async def get_construct_job(job_id: str, session: SessionDep) -> JSONResponse:
    """构造任务详情。"""
    job = await session.get(Job, job_id)
    if job is None or job.type != _CONSTRUCT_TYPE:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "构造任务不存在"}
        )
    output = await _build_output(session, job.id)
    input_ = await _build_input(session, job.id)
    return JSONResponse(content=_item(job, output, input_))


@router.post("/construct/jobs/{job_id}/rerun")
async def rerun_construct_job(
    job_id: str,
    session: SessionDep,
    user: Annotated[User, Depends(require_user)],
) -> JSONResponse:
    """用原 spec 重跑。需登录 + 输入数据集 ACL ≥ edit。"""
    job = await session.get(Job, job_id)
    if job is None or job.type != _CONSTRUCT_TYPE:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "构造任务不存在"}
        )
    if not job.spec:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "该任务无可重跑的配置"},
        )
    try:
        spec = ConstructJobCreate.model_validate(job.spec)
    except ValidationError:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "任务配置已损坏,无法重跑"},
        )
    return await _start_construct(session, spec, user=user)


@router.post("/construct/jobs/{job_id}/stop", dependencies=[Depends(require_admin)])
async def stop_construct_job(job_id: str, session: SessionDep) -> JSONResponse:
    """停止运行中/排队的构造任务。"""
    job = await session.get(Job, job_id)
    if job is None or job.type != _CONSTRUCT_TYPE:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "构造任务不存在"}
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


async def _delete_construct_cascade(session: AsyncSession, job: Job) -> None:
    await session.execute(delete(JobInput).where(JobInput.job_id == job.id))
    await session.execute(
        update(DatasetVersion)
        .where(DatasetVersion.produced_by_job_id == job.id)
        .values(produced_by_job_id=None)
    )
    await session.delete(job)


@router.delete("/construct/jobs/{job_id}", dependencies=[Depends(require_admin)])
async def delete_construct_job(job_id: str, session: SessionDep) -> JSONResponse:
    """删除构造任务(只删任务,产物版本保留)。"""
    job = await session.get(Job, job_id)
    if job is None or job.type != _CONSTRUCT_TYPE:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "构造任务不存在"}
        )
    if job.state == "running":
        return JSONResponse(
            status_code=409,
            content={"success": False, "message": "任务运行中,无法删除"},
        )
    await _delete_construct_cascade(session, job)
    await session.commit()
    return JSONResponse(content={"success": True})


@router.post("/construct/jobs/batch-delete", dependencies=[Depends(require_admin)])
async def batch_delete_construct_jobs(
    body: BatchDeleteRequest, session: SessionDep
) -> JSONResponse:
    """批量删除构造任务。"""
    deleted = 0
    for job_id in body.ids:
        job = await session.get(Job, job_id)
        if job is None or job.type != _CONSTRUCT_TYPE or job.state == "running":
            continue
        await _delete_construct_cascade(session, job)
        deleted += 1
    await session.commit()
    return JSONResponse(content={"data": {"deleted": deleted}, "success": True})


@router.get("/construct/jobs/{job_id}/report")
async def get_construct_report(job_id: str, session: SessionDep) -> JSONResponse:
    """读 report.json 拿构造报告(输入/输出条数/不合规行/warnings)。"""
    job = await session.get(Job, job_id)
    if job is None or job.type != _CONSTRUCT_TYPE:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "构造任务不存在"}
        )
    stmt = (
        select(DatasetVersion)
        .where(DatasetVersion.produced_by_job_id == job_id)
        .order_by(DatasetVersion.created_at.desc())
    )
    version = (await session.scalars(stmt)).first()
    if version is None:
        spec = job.spec or {}
        goal = spec.get("goal") or {}
        empty = ConstructReport(
            job_id=job.id,
            input_version_id=spec.get("dataset_version_id", ""),
            train_type=goal.get("train_type", ""),
            schema_variant=goal.get("schema_variant", ""),
            input_count=0,
            warnings=["任务尚未完成"] if job.state != "success" else [],
        )
        return JSONResponse(
            content={"data": empty.model_dump(mode="json"), "success": True}
        )
    out_dir = (
        Path(settings.datasets_dir) / version.dataset_id / f"v{version.version_no}"
    )
    report_path = out_dir / "report.json"
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
