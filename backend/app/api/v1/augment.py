"""数据增强(augment) API:8 个端点,Job.type='augmentation'。

需求文档 #8:数据增强——LLM 改写已有数据(1→1)。算子在 AUGMENT_OPS(9 个 LLM Mapper),
未配 LLM Key → needs_api 拦截。产物 ``DatasetVersion.origin='synthetic'``。
与 make 共享 origin 约定(原始 vs 合成 二分),但 Job.type 独立。
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

from app.api.deps import require_admin, require_perm
from app.api.v1.jobs import (
    BatchDeleteRequest,
    SessionDep,
    _binary_block,
    _build_input,
    _build_output,
    _dataset_job_filter,
    _item,
    _new_job_id,
    _now,
    _reset_for_edit_rerun,
)
from app.core.config import settings
from app.models.dataset_version import DatasetVersion
from app.models.job import Job
from app.models.job_input import JobInput
from app.schemas.augment import AugmentJobCreate, AugmentReport
from app.schemas.common import PageResponse
from app.schemas.job import JobRead
from app.services import job_runner
from app.services import operator_catalog as oc
from app.services.llm_config import get_active_llm_config

router = APIRouter(tags=["augmentation"])

_AUGMENT_TYPE = "augmentation"


def _augment_operator_block(operators: list) -> str | None:
    """资源前置校验(增强特有):算子不限白名单,仅按运行时能力(LLM/GPU)拦截。"""
    llm_configured = bool(get_active_llm_config().api_key)
    if not llm_configured:
        return "数据增强需 LLM 支持:请先在运维监控 / LLM 配置页设置 OPENAI_API_KEY"
    blocked = [
        reason
        for o in operators
        if (reason := oc.runnable_reason(o.name, llm_configured=llm_configured))
    ]
    if blocked:
        return "；".join(blocked)
    return None


async def _start_augment(
    session: AsyncSession, body: AugmentJobCreate, job: Job | None = None
) -> JSONResponse:
    """增强版 _start_job:校验 → 建任务 → spawn 后台 → 立即返回 pending。

    传入 job = 编辑任务:校验通过后覆盖该任务配置并原地重跑,不新建记录。
    """
    if not body.operators:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "请至少选择一个算子"},
        )
    unknown = [o.name for o in body.operators if oc.get_operator(o.name) is None]
    if unknown:
        return JSONResponse(
            status_code=400, content={"success": False, "message": f"未知算子:{', '.join(unknown)}"}
        )
    if (block_msg := _augment_operator_block(body.operators)) is not None:
        return JSONResponse(
            status_code=400, content={"success": False, "message": block_msg}
        )
    if body.goal.mode not in ("augment",):
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": f"goal.mode 非法:{body.goal.mode}"},
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
            type=_AUGMENT_TYPE,
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
    # spawn 只传 job_id:augment_goal/output_dataset_id 已随 body 落进 job.spec,
    # job_runner._run_job 按 type=augmentation 从 spec 重建 body 并取出 goal 等
    job_runner.spawn(job.id)
    return JSONResponse(content=_item(job))


@router.post("/augmentation/jobs", dependencies=[Depends(require_admin)])
async def create_augment_job(
    body: AugmentJobCreate, session: SessionDep
) -> JSONResponse:
    """新建数据增强任务并异步执行。"""
    return await _start_augment(session, body)


@router.get(
    "/augmentation/jobs",
    response_model=PageResponse[JobRead],
    dependencies=[Depends(require_perm("governance:augment:list"))],
)
async def list_augment_jobs(
    session: SessionDep,
    current: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, alias="pageSize")] = 10,
    dataset_id: Annotated[str | None, Query(alias="datasetId")] = None,
) -> PageResponse[JobRead]:
    """分页列出增强任务;可按 datasetId 过滤(输入或产物版本属于该数据集)。"""
    count_stmt = select(func.count()).select_from(Job).where(Job.type == _AUGMENT_TYPE)
    list_stmt = select(Job).where(Job.type == _AUGMENT_TYPE)
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


@router.get("/augmentation/jobs/{job_id}")
async def get_augment_job(job_id: str, session: SessionDep) -> JSONResponse:
    """增强任务详情。"""
    job = await session.get(Job, job_id)
    if job is None or job.type != _AUGMENT_TYPE:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "增强任务不存在"}
        )
    output = await _build_output(session, job.id)
    input_ = await _build_input(session, job.id)
    return JSONResponse(content=_item(job, output, input_))


@router.put(
    "/augmentation/jobs/{job_id}", dependencies=[Depends(require_admin)]
)
async def update_augment_job(
    job_id: str, body: AugmentJobCreate, session: SessionDep
) -> JSONResponse:
    """编辑增强任务:覆盖原任务配置并原地重跑(沿用任务 id,不新建记录)。

    仅终态/已暂停任务可编辑;运行中/排队中 → 409(先停止)。
    """
    job = await session.get(Job, job_id)
    if job is None or job.type != _AUGMENT_TYPE:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "增强任务不存在"}
        )
    if job.state in ("pending", "running"):
        return JSONResponse(
            status_code=409,
            content={"success": False, "message": "任务运行中,请先停止再编辑"},
        )
    return await _start_augment(session, body, job=job)


@router.post(
    "/augmentation/jobs/{job_id}/rerun", dependencies=[Depends(require_admin)]
)
async def rerun_augment_job(job_id: str, session: SessionDep) -> JSONResponse:
    """用原 spec 重跑。"""
    job = await session.get(Job, job_id)
    if job is None or job.type != _AUGMENT_TYPE:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "增强任务不存在"}
        )
    if not job.spec:
        return JSONResponse(
            status_code=400, content={"success": False, "message": "该任务无可重跑的配置"}
        )
    try:
        spec = AugmentJobCreate.model_validate(job.spec)
    except ValidationError:
        return JSONResponse(
            status_code=400, content={"success": False, "message": "任务配置已损坏,无法重跑"}
        )
    return await _start_augment(session, spec)


@router.post(
    "/augmentation/jobs/{job_id}/stop", dependencies=[Depends(require_admin)]
)
async def stop_augment_job(job_id: str, session: SessionDep) -> JSONResponse:
    """停止运行中/排队的增强任务。"""
    job = await session.get(Job, job_id)
    if job is None or job.type != _AUGMENT_TYPE:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "增强任务不存在"}
        )
    if job.state not in ("pending", "running"):
        return JSONResponse(
            status_code=409, content={"success": False, "message": "任务不在运行中,无法停止"}
        )
    job_runner.request_cancel(job_id)
    from app.services.engine import terminate_job
    terminate_job(job_id)
    job.state = "cancelled"
    job.finished_at = _now()
    await session.commit()
    return JSONResponse(content={"success": True})


async def _delete_augment_cascade(session: AsyncSession, job: Job) -> None:
    await session.execute(delete(JobInput).where(JobInput.job_id == job.id))
    await session.execute(
        update(DatasetVersion)
        .where(DatasetVersion.produced_by_job_id == job.id)
        .values(produced_by_job_id=None)
    )
    await session.delete(job)


@router.delete(
    "/augmentation/jobs/{job_id}", dependencies=[Depends(require_admin)]
)
async def delete_augment_job(job_id: str, session: SessionDep) -> JSONResponse:
    """删除增强任务(只删任务,产物版本保留)。"""
    job = await session.get(Job, job_id)
    if job is None or job.type != _AUGMENT_TYPE:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "增强任务不存在"}
        )
    if job.state == "running":
        return JSONResponse(
            status_code=409, content={"success": False, "message": "任务运行中,无法删除"}
        )
    await _delete_augment_cascade(session, job)
    await session.commit()
    return JSONResponse(content={"success": True})


@router.post(
    "/augmentation/jobs/batch-delete", dependencies=[Depends(require_admin)]
)
async def batch_delete_augment_jobs(
    body: BatchDeleteRequest, session: SessionDep
) -> JSONResponse:
    """批量删除增强任务。"""
    deleted = 0
    for job_id in body.ids:
        job = await session.get(Job, job_id)
        if job is None or job.type != _AUGMENT_TYPE or job.state == "running":
            continue
        await _delete_augment_cascade(session, job)
        deleted += 1
    await session.commit()
    return JSONResponse(content={"data": {"deleted": deleted}, "success": True})


def _to_camel(d: dict[str, Any]) -> dict[str, Any]:
    """snake_case 字段名 → camelCase(老报告兼容性)。"""
    import re
    out: dict[str, Any] = {}
    for k, v in d.items():
        ck = re.sub(r"_([a-z])", lambda m: m.group(1).upper(), k)
        out[ck] = v
    return out


@router.get("/augmentation/jobs/{job_id}/report")
async def get_augment_report(job_id: str, session: SessionDep) -> JSONResponse:
    """拿增强报告(输入/输出条数/扩增比/warnings)。

    读取顺序:1) DB jobs.eval_report(主存,跨机器可读)
            2) 本地 report.json(兜底,兼容升级前的历史任务)
            3) 都没有 → 404

    返回 camelCase 字段(与前端约定一致):即便老报告以 snake_case 写入 DB,
    读取时也规范化到 camelCase。
    """
    job = await session.get(Job, job_id)
    if job is None or job.type != _AUGMENT_TYPE:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "增强任务不存在"}
        )

    # 1) 主存:DB eval_report
    if job.eval_report:
        return JSONResponse(
            content={"data": _to_camel(job.eval_report), "success": True}
        )

    stmt = (
        select(DatasetVersion)
        .where(DatasetVersion.produced_by_job_id == job_id)
        .order_by(DatasetVersion.created_at.desc())
    )
    version = (await session.scalars(stmt)).first()
    if version is None:
        spec = job.spec or {}
        goal_mode = (spec.get("goal") or {}).get("mode", "augment")
        empty = AugmentReport(
            job_id=job.id,
            input_version_id=spec.get("dataset_version_id", ""),
            mode=goal_mode,
            input_count=0,
            operator_chain=[o["name"] for o in spec.get("operators", [])],
            warnings=["任务尚未完成"] if job.state != "success" else [],
        )
        # by_alias=True:AugmentReport 继承 CamelModel,默认 model_dump 输出 snake_case;
        # 显式 by_alias 才能输出 camelCase,与前端约定一致。
        return JSONResponse(
            content={
                "data": empty.model_dump(mode="json", by_alias=True),
                "success": True,
            }
        )

    # 2) 兜底:本地 report.json(老任务升级前跑的,DB 没存)
    out_dir = (
        Path(settings.datasets_dir)
        / version.dataset_id
        / f"v{version.version_no}"
    )
    report_path = out_dir / "report.json"
    if report_path.exists():
        try:
            raw: dict[str, Any] = json.loads(report_path.read_text(encoding="utf-8"))
            return JSONResponse(content={"data": _to_camel(raw), "success": True})
        except (OSError, json.JSONDecodeError) as exc:
            return JSONResponse(
                status_code=500,
                content={"success": False, "message": f"报告解析失败:{exc}"},
            )

    # 3) 都没有——常见于跨机器查看老任务
    return JSONResponse(
        status_code=404,
        content={
            "success": False,
            "message": (
                "报告不可用:数据库与本地缓存均无此任务的报告"
                "(老任务在执行机器本地清理后即失效,可重新运行)"
            ),
        },
    )
