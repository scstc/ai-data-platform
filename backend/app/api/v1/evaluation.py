"""评估数据集 + 裁判员(judge)API(治理整改 G4/G5)。

- POST /eval/datasets:上传评估集({prompt,response}),≥300 Fail-loud 校验。
- POST /eval/judge/jobs:对版本起裁判任务(LLM-as-judge 打分)。
- GET  /eval/judge/jobs[/{id}][/report][/results]:列表/详情/报告/逐条结果。
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from app.api.deps import require_admin, require_perm
from app.api.v1.jobs import (
    SessionDep,
    _binary_block,
    _build_input,
    _build_output,
    _dataset_job_filter,
    _item,
    _new_job_id,
)
from app.models.dataset_version import DatasetVersion
from app.models.eval_result import EvalResult
from app.models.job import Job
from app.schemas.common import PageResponse
from app.schemas.eval import EvalResultRead, JudgeJobCreate
from app.schemas.job import JobRead
from app.services import job_runner
from app.services.eval_dataset import EvalValidationError, land_eval_dataset
from app.services.landing import LandingError, UnsupportedFormatError
from app.services.semantic_registry import SemanticValidationError

router = APIRouter(tags=["evaluation"])

_JUDGE_TYPE = "judge"


def _file_ext(filename: str) -> str:
    suffix = Path(filename).suffix
    return suffix[1:].lower() if suffix else ""


@router.post("/eval/datasets", dependencies=[Depends(require_admin)])
async def upload_eval_dataset(
    session: SessionDep,
    file: Annotated[UploadFile, File(...)],
    name: Annotated[str | None, Form()] = None,
    description: Annotated[str | None, Form()] = None,
) -> JSONResponse:
    """上传评估数据集:jsonl/csv → {prompt,response},≥300 条 Fail-loud 校验。"""
    filename = file.filename or ""
    fmt = _file_ext(filename)
    content = await file.read()
    try:
        dataset, version = await land_eval_dataset(
            session,
            content=content,
            filename=filename,
            source_format=fmt,
            dataset_name=name,
            description=description,
        )
    except EvalValidationError as exc:
        return JSONResponse(
            status_code=422, content={"success": False, "message": str(exc)}
        )
    except SemanticValidationError as exc:
        return JSONResponse(
            status_code=422, content={"success": False, "message": str(exc)}
        )
    except (UnsupportedFormatError, LandingError) as exc:
        return JSONResponse(
            status_code=400, content={"success": False, "message": str(exc)}
        )
    return JSONResponse(
        content={
            "success": True,
            "data": {
                "datasetId": dataset.id,
                "versionId": version.id,
                "trainType": version.train_type,
                "schemaVariant": version.schema_variant,
                "recordCount": version.rows,
            },
        }
    )


@router.post("/eval/judge/jobs", dependencies=[Depends(require_admin)])
async def create_judge_job(
    body: JudgeJobCreate, session: SessionDep
) -> JSONResponse:
    """对一个版本起裁判任务(对比 reference 与 completion 打分)。"""
    input_version = await session.get(DatasetVersion, body.dataset_version_id)
    if input_version is None:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "数据集版本不存在"}
        )
    if (blocked := _binary_block(input_version)) is not None:
        return blocked
    job = Job(
        id=_new_job_id(),
        name=body.name or "评估裁判",
        type=_JUDGE_TYPE,
        state="pending",
        progress=0,
        created_by="admin",
        spec=body.model_dump(mode="json"),
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    job_runner.spawn(job.id)
    return JSONResponse(content=_item(job))


@router.get(
    "/eval/judge/jobs",
    response_model=PageResponse[JobRead],
    dependencies=[Depends(require_perm("governance:make:list"))],
)
async def list_judge_jobs(
    session: SessionDep,
    current: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, alias="pageSize")] = 10,
    dataset_id: Annotated[str | None, Query(alias="datasetId")] = None,
) -> PageResponse[JobRead]:
    """分页列出裁判任务。"""
    count_stmt = select(func.count()).select_from(Job).where(Job.type == _JUDGE_TYPE)
    list_stmt = select(Job).where(Job.type == _JUDGE_TYPE)
    if dataset_id:
        count_stmt = count_stmt.where(_dataset_job_filter(dataset_id))
        list_stmt = list_stmt.where(_dataset_job_filter(dataset_id))
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


@router.get("/eval/judge/jobs/{job_id}")
async def get_judge_job(job_id: str, session: SessionDep) -> JSONResponse:
    """裁判任务详情。"""
    job = await session.get(Job, job_id)
    if job is None or job.type != _JUDGE_TYPE:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "裁判任务不存在"}
        )
    output = await _build_output(session, job.id)
    input_ = await _build_input(session, job.id)
    return JSONResponse(content=_item(job, output, input_))


@router.get("/eval/judge/jobs/{job_id}/report")
async def get_judge_report(job_id: str, session: SessionDep) -> JSONResponse:
    """裁判汇总报告(eval_report:avgScore/passRate/byCategory/scoreBuckets/warnings)。"""
    job = await session.get(Job, job_id)
    if job is None or job.type != _JUDGE_TYPE:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "裁判任务不存在"}
        )
    return JSONResponse(
        content={
            "success": True,
            "data": {
                "state": job.state,
                "error": job.error,
                "evalReport": job.eval_report,
            },
        }
    )


@router.get(
    "/eval/judge/jobs/{job_id}/results",
    response_model=PageResponse[EvalResultRead],
)
async def list_judge_results(
    job_id: str,
    session: SessionDep,
    current: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=200, alias="pageSize")] = 20,
    verdict: Annotated[str | None, Query()] = None,
    category: Annotated[str | None, Query()] = None,
) -> PageResponse[EvalResultRead]:
    """分页逐条裁判结果;可按 verdict/category 过滤,按 row_index 升序。"""
    conds = [EvalResult.job_id == job_id]
    if verdict:
        conds.append(EvalResult.verdict == verdict)
    if category:
        conds.append(EvalResult.category == category)
    total = (
        await session.scalar(
            select(func.count()).select_from(EvalResult).where(*conds)
        )
        or 0
    )
    rows = (
        await session.scalars(
            select(EvalResult)
            .where(*conds)
            .order_by(EvalResult.row_index.asc())
            .offset((current - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    data = [EvalResultRead.model_validate(r) for r in rows]
    return PageResponse[EvalResultRead](data=data, total=total)
