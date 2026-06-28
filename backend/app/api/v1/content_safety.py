"""内容安全审核路由(#4)。

- POST /content-safety/jobs:对一个数据集版本建 review job + 同步跑 run_review
  (沿用 jobs.py / quality.py 的 create→run→回写 状态机),产出打标版本。
  不加 require_admin(审核属分析类;POST 经 #5 审计中间件留痕)。
- GET  /content-safety/jobs:分页列 type=review job。
- GET  /content-safety/jobs/{id}/report:job 状态 + review_report + 打标版本 id。
- GET  /content-safety/jobs/{id}/findings:分页 review_findings,过滤
  category/source/severity,按 row_index 升序。

设计见 docs/plan/07-内容安全设计.md §3.3。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from app.api.deps import require_perm
from app.api.v1.jobs import (
    SessionDep,
    _binary_block,
    _build_input,
    _build_output,
    _item,
    _new_job_id,
)
from app.models.dataset_version import DatasetVersion
from app.models.job import Job
from app.models.review_finding import ReviewFinding
from app.schemas.common import PageResponse
from app.schemas.job import JobRead
from app.schemas.review import ReviewFindingRead, ReviewJobCreate
from app.services import job_runner

router = APIRouter(tags=["content-safety"])


@router.post("/content-safety/jobs")
async def create_review_job(
    body: ReviewJobCreate, session: SessionDep
) -> JSONResponse:
    """新建内容审核任务并后台异步执行:扫描版本 → 落命中 → 产出打标版本 → 回写报告。

    异步(同治理类任务):立即返回 pending,不阻塞请求;可经 /jobs/{id}/stop|pause|resume
    统一管控。打标版本与报告在后台跑完后产出。
    """
    input_version = await session.get(DatasetVersion, body.dataset_version_id)
    if input_version is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "数据集版本不存在"},
        )
    if (blocked_resp := _binary_block(input_version)) is not None:
        return blocked_resp

    job = Job(
        id=_new_job_id(),
        name=body.name or "内容安全审核",
        type="review",
        state="pending",
        progress=0,
        created_by="admin",
        # 存原始执行规格,供 job_runner 后台重建 body(config)+ 供重跑/继续
        spec=body.model_dump(mode="json"),
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    # 交后台异步执行(与治理类任务同一执行路径 / 同一状态机)
    job_runner.spawn(job.id)
    return JSONResponse(content=_item(job))


@router.get(
    "/content-safety/jobs",
    response_model=PageResponse[JobRead],
    dependencies=[Depends(require_perm("governance:contentsafety:list"))],
)
async def list_review_jobs(
    session: SessionDep,
    current: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, alias="pageSize")] = 10,
) -> PageResponse[JobRead]:
    """分页列出 type=review 任务,按创建时间倒序(带输入版本概要)。"""
    total = (
        await session.scalar(
            select(func.count()).select_from(Job).where(Job.type == "review")
        )
    ) or 0
    rows = (
        await session.scalars(
            select(Job)
            .where(Job.type == "review")
            .order_by(Job.created_at.desc())
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


@router.get("/content-safety/jobs/{job_id}/report")
async def review_report(job_id: str, session: SessionDep) -> JSONResponse:
    """审核报告:job 状态 + review_report + 打标版本 id。"""
    job = await session.get(Job, job_id)
    if job is None or job.type != "review":
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "审核任务不存在"},
        )
    output = await _build_output(session, job.id)
    return JSONResponse(
        content={
            "data": {
                "jobId": job.id,
                "name": job.name,
                "state": job.state,
                "error": job.error,
                "reviewReport": job.review_report,
                "taggedVersionId": output["versionId"] if output else None,
            },
            "success": True,
        }
    )


@router.get(
    "/content-safety/jobs/{job_id}/findings",
    response_model=PageResponse[ReviewFindingRead],
)
async def list_findings(
    job_id: str,
    session: SessionDep,
    current: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, alias="pageSize")] = 10,
    category: Annotated[str | None, Query()] = None,
    source: Annotated[str | None, Query()] = None,
    severity: Annotated[str | None, Query()] = None,
) -> PageResponse[ReviewFindingRead]:
    """分页列出某审核任务的命中,可按 category/source/severity 过滤,按行号升序。"""
    conds = [ReviewFinding.job_id == job_id]
    if category:
        conds.append(ReviewFinding.category == category)
    if source:
        conds.append(ReviewFinding.source == source)
    if severity:
        conds.append(ReviewFinding.severity == severity)

    total = (
        await session.scalar(
            select(func.count()).select_from(ReviewFinding).where(*conds)
        )
    ) or 0
    rows = (
        await session.scalars(
            select(ReviewFinding)
            .where(*conds)
            .order_by(ReviewFinding.row_index.asc())
            .offset((current - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    data = [ReviewFindingRead.model_validate(r) for r in rows]
    return PageResponse[ReviewFindingRead](data=data, total=total)
