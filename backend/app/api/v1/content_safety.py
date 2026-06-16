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

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from app.api.v1.jobs import (
    SessionDep,
    _binary_block,
    _build_input,
    _build_output,
    _item,
    _new_job_id,
    _now,
)
from app.models.dataset_version import DatasetVersion
from app.models.job import Job
from app.models.review_finding import ReviewFinding
from app.schemas.common import PageResponse
from app.schemas.job import JobRead
from app.schemas.review import ReviewFindingRead, ReviewJobCreate
from app.services.external_store import ExternalStoreError
from app.services.review_runner import ReviewError, run_review

router = APIRouter(tags=["content-safety"])


@router.post("/content-safety/jobs")
async def create_review_job(
    body: ReviewJobCreate, session: SessionDep
) -> JSONResponse:
    """新建并执行内容审核任务:扫描版本 → 落命中 → 产出打标版本 → 回写报告。"""
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
        state="running",
        progress=0,
        created_by="admin",
        started_at=_now(),
    )
    session.add(job)
    await session.commit()

    try:
        await run_review(
            session,
            job=job,
            version=input_version,
            # scan_version 按 camelCase 键读取(useLlm/sampleLimit 等),
            # CamelModel 须 by_alias 导出,否则 snake_case 键全被忽略(降级+不限样本)
            config=body.config.model_dump(by_alias=True),
        )
        job.state = "success"
        job.progress = 100
    except ReviewError as exc:
        job.state = "failed"
        job.error = str(exc)
    except ExternalStoreError as exc:  # hosted 版本:S3 读取失败给明确文案
        job.state = "failed"
        job.error = f"读取 S3 对象失败:{exc}"
    except Exception as exc:  # 未预期异常也不能让 job 卡死在 running
        job.state = "failed"
        job.error = f"未预期错误:{exc}"
    job.finished_at = _now()
    await session.commit()
    await session.refresh(job)

    output = await _build_output(session, job.id)
    input_ = await _build_input(session, job.id)
    return JSONResponse(content=_item(job, output, input_))


@router.get("/content-safety/jobs", response_model=PageResponse[JobRead])
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
