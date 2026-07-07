"""数据合并(make) API:8 个端点,Job.type='synthesis'(沿用,不改 schema)。

现主路径为 merge/concat:多 jsonl 按行拼接/追加(纯 Python)。存量 synthesize
模式(LLM 造新数据,需求文档 #8)代码保留,未配 LLM Key → needs_api 拦截。
产物 ``DatasetVersion.origin='synthetic'``。

注意:type='synthesis' 与 URL /synthesis/jobs 均沿用(避免契约变更),界面名为"数据合并"。
增强走 augment.py(URL /augmentation/jobs, type='augmentation')。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin, require_perm
from app.api.v1.jobs import (
    SessionDep,
    _binary_block,
    _build_input,
    _build_output,
    _dataset_job_filter,
    _item,
    _new_job_id,
    _now,
    _reset_for_edit_rerun,
    BatchDeleteRequest,
)
from app.models.dataset_version import DatasetVersion
from app.models.job import Job
from app.models.job_input import JobInput
from app.schemas.common import PageResponse
from app.schemas.job import JobRead
from app.schemas.make import MakeJobCreate
from app.services import job_runner
from app.services import operator_catalog as oc
from app.services.llm_config import get_active_llm_config

router = APIRouter(tags=["synthesis"])

# Job.type 沿用 'synthesis'(避免 alembic / OpenAPI 行为变化;语义靠 spec.goal.mode 区分)
_MAKE_TYPE = "synthesis"


def _make_operator_block(operators: list) -> str | None:
    """资源前置校验(合成特有):算子不限白名单,仅按运行时能力(LLM/GPU)拦截。"""
    llm_configured = bool(get_active_llm_config().api_key)
    if not llm_configured:
        return "数据合并需 LLM 支持:请先在运维监控 / LLM 配置页设置 OPENAI_API_KEY"
    blocked = [
        reason
        for o in operators
        if (reason := oc.runnable_reason(o.name, llm_configured=llm_configured))
    ]
    if blocked:
        return "；".join(blocked)
    return None


async def _start_make(
    session: AsyncSession, body: MakeJobCreate, job: Job | None = None
) -> JSONResponse:
    """合成版 _start_job:校验 → 建任务 → spawn 后台 → 立即返回 pending。

    传入 job = 编辑任务:校验通过后覆盖该任务配置并原地重跑,不新建记录。
    """
    if body.goal.mode not in ("synthesize", "make", "merge", "concat"):
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": f"goal.mode 非法:{body.goal.mode}"},
        )
    if body.goal.mode in ("merge", "concat"):
        # 合并/追加模式:纯 Python 处理,不走 DJ/LLM,只校验成员配置。
        # 字段是否真为共同字段在执行时逐文件复核(services/make 报错到任务)。
        names = body.goal.merge_members or []
        if len(names) < 2:
            return JSONResponse(
                status_code=400,
                content={"success": False, "message": "至少选择 2 个成员文件"},
            )
        if len(set(names)) != len(names):
            return JSONResponse(
                status_code=400,
                content={"success": False, "message": "成员文件名重复"},
            )
        if body.goal.mode == "merge" and not (body.goal.merge_field or "").strip():
            return JSONResponse(
                status_code=400,
                content={"success": False, "message": "请指定合并字段"},
            )
    else:
        if not body.operators:
            return JSONResponse(
                status_code=400,
                content={"success": False, "message": "请至少选择一个算子"},
            )
        unknown = [
            o.name for o in body.operators if oc.get_operator(o.name) is None
        ]
        if unknown:
            return JSONResponse(
                status_code=400,
                content={"success": False, "message": f"未知算子:{', '.join(unknown)}"},
            )
        if (block_msg := _make_operator_block(body.operators)) is not None:
            return JSONResponse(
                status_code=400, content={"success": False, "message": block_msg}
            )

    input_version = await session.get(DatasetVersion, body.dataset_version_id)
    if input_version is None:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "数据集版本不存在"}
        )
    if (blocked_resp := _binary_block(input_version)) is not None:
        return blocked_resp

    if job is None:
        job = Job(
            id=_new_job_id(),
            name=body.name,
            type=_MAKE_TYPE,
            state="pending",
            progress=0,
            created_by="admin",
            spec=body.model_dump(mode="json"),
            pipeline_id=body.pipeline_id,
        )
        session.add(job)
    else:
        await _reset_for_edit_rerun(session, job, body)
    await session.commit()
    await session.refresh(job)
    # spawn 只传 job_id:make_goal/output_dataset_id 已随 body 落进 job.spec,
    # job_runner._run_job 按 type=synthesis 从 spec 重建 body 并取出 goal 等
    job_runner.spawn(job.id)
    return JSONResponse(content=_item(job))


@router.post("/synthesis/jobs", dependencies=[Depends(require_admin)])
async def create_make_job(
    body: MakeJobCreate, session: SessionDep
) -> JSONResponse:
    """新建数据合并任务并异步执行(URL 用 /synthesis,job type 沿用 synthesis)。"""
    return await _start_make(session, body)


@router.get(
    "/synthesis/jobs",
    response_model=PageResponse[JobRead],
    dependencies=[Depends(require_perm("governance:make:list"))],
)
async def list_make_jobs(
    session: SessionDep,
    current: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, alias="pageSize")] = 10,
    dataset_id: Annotated[str | None, Query(alias="datasetId")] = None,
) -> PageResponse[JobRead]:
    """分页列出合并任务;可按 datasetId 过滤(输入或产物版本属于该数据集)。"""
    count_stmt = select(func.count()).select_from(Job).where(Job.type == _MAKE_TYPE)
    list_stmt = select(Job).where(Job.type == _MAKE_TYPE)
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
        read.output = await _build_output(session, r.id)
        data.append(read)
    return PageResponse[JobRead](data=data, total=total)


@router.get("/synthesis/jobs/{job_id}")
async def get_make_job(job_id: str, session: SessionDep) -> JSONResponse:
    """合并任务详情。"""
    job = await session.get(Job, job_id)
    if job is None or job.type != _MAKE_TYPE:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "合并任务不存在"}
        )
    output = await _build_output(session, job.id)
    input_ = await _build_input(session, job.id)
    return JSONResponse(content=_item(job, output, input_))


@router.put("/synthesis/jobs/{job_id}", dependencies=[Depends(require_admin)])
async def update_make_job(
    job_id: str, body: MakeJobCreate, session: SessionDep
) -> JSONResponse:
    """编辑合并任务:覆盖原任务配置并原地重跑(沿用任务 id,不新建记录)。

    仅终态/已暂停任务可编辑;运行中/排队中 → 409(先停止)。
    """
    job = await session.get(Job, job_id)
    if job is None or job.type != _MAKE_TYPE:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "合并任务不存在"}
        )
    if job.state in ("pending", "running"):
        return JSONResponse(
            status_code=409,
            content={"success": False, "message": "任务运行中,请先停止再编辑"},
        )
    return await _start_make(session, body, job=job)


@router.post(
    "/synthesis/jobs/{job_id}/rerun", dependencies=[Depends(require_admin)]
)
async def rerun_make_job(job_id: str, session: SessionDep) -> JSONResponse:
    """用原 spec 重跑。"""
    job = await session.get(Job, job_id)
    if job is None or job.type != _MAKE_TYPE:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "合并任务不存在"}
        )
    if not job.spec:
        return JSONResponse(
            status_code=400, content={"success": False, "message": "该任务无可重跑的配置"}
        )
    try:
        spec = MakeJobCreate.model_validate(job.spec)
    except ValidationError:
        return JSONResponse(
            status_code=400, content={"success": False, "message": "任务配置已损坏,无法重跑"}
        )
    return await _start_make(session, spec)


@router.post(
    "/synthesis/jobs/{job_id}/stop", dependencies=[Depends(require_admin)]
)
async def stop_make_job(job_id: str, session: SessionDep) -> JSONResponse:
    """停止运行中/排队的合并任务。"""
    job = await session.get(Job, job_id)
    if job is None or job.type != _MAKE_TYPE:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "合并任务不存在"}
        )
    if job.state not in ("pending", "running"):
        return JSONResponse(
            status_code=409, content={"success": False, "message": "任务不在运行中,无法停止"}
        )
    job_runner.request_cancel(job_id)
    from app.services.engine import terminate_job  # 局部 import 避免循环
    terminate_job(job_id)
    job.state = "cancelled"
    job.finished_at = _now()
    await session.commit()
    return JSONResponse(content={"success": True})


async def _delete_make_cascade(session: AsyncSession, job: Job) -> None:
    await session.execute(delete(JobInput).where(JobInput.job_id == job.id))
    await session.execute(
        update(DatasetVersion)
        .where(DatasetVersion.produced_by_job_id == job.id)
        .values(produced_by_job_id=None)
    )
    await session.delete(job)


@router.delete(
    "/synthesis/jobs/{job_id}", dependencies=[Depends(require_admin)]
)
async def delete_make_job(job_id: str, session: SessionDep) -> JSONResponse:
    """删除合并任务(只删任务,产物版本保留)。"""
    job = await session.get(Job, job_id)
    if job is None or job.type != _MAKE_TYPE:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "合并任务不存在"}
        )
    if job.state == "running":
        return JSONResponse(
            status_code=409, content={"success": False, "message": "任务运行中,无法删除"}
        )
    await _delete_make_cascade(session, job)
    await session.commit()
    return JSONResponse(content={"success": True})


@router.post(
    "/synthesis/jobs/batch-delete", dependencies=[Depends(require_admin)]
)
async def batch_delete_make_jobs(
    body: BatchDeleteRequest, session: SessionDep
) -> JSONResponse:
    """批量删除合并任务。"""
    deleted = 0
    for job_id in body.ids:
        job = await session.get(Job, job_id)
        if job is None or job.type != _MAKE_TYPE or job.state == "running":
            continue
        await _delete_make_cascade(session, job)
        deleted += 1
    await session.commit()
    return JSONResponse(content={"data": {"deleted": deleted}, "success": True})
