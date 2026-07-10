"""数据蒸馏 API:8 个端点,复用 Job 表(``type='distillation'``)。

执行模式:与 processing 相同的**异步**流水线(``job_runner.spawn`` 立即返回 pending);
产物:与 processing 相同的"原数据集新版本";报告:落 ``<out_dir>/report.json``。
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
    SessionDep,
    _acl_job_filter,
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
from app.core.config import settings
from app.models.dataset_version import DatasetVersion
from app.models.job import Job
from app.models.job_input import JobInput
from app.models.user import User
from app.schemas.common import PageResponse
from app.schemas.distillation import DistillationJobCreate, DistillationReport
from app.schemas.job import JobRead
from app.services import dataset_acl, job_runner
from app.services import operator_catalog as oc
from app.services.llm_config import get_active_llm_config

router = APIRouter(tags=["distillation"])

_DISTILL_TYPE = "distillation"


# ---------------------------------------------------------------------------
# 资源前置校验(蒸馏特有)
# ---------------------------------------------------------------------------
def _distill_operator_block(
    operators: list,  # list[OperatorSpec]——避免循环 import,运行时只读 .name
) -> str | None:
    """返回不可执行的原因;None 表示全部可执行。

    算子不限白名单,仅按运行时能力(GPU/LLM 资源门)拦截。
    """
    llm_configured = bool(get_active_llm_config().api_key)
    blocked = [
        reason
        for o in operators
        if (reason := oc.runnable_reason(o.name, llm_configured=llm_configured))
    ]
    if blocked:
        return "；".join(blocked)
    return None


async def _start_distillation(
    session: AsyncSession,
    body: DistillationJobCreate,
    job: Job | None = None,
    user: User | None = None,
) -> JSONResponse:
    """蒸馏版 _start_job:校验 → 建任务 → spawn 后台 → 立即返回 pending。

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
            status_code=400,
            content={"success": False, "message": f"未知算子:{', '.join(unknown)}"},
        )
    if (block_msg := _distill_operator_block(body.operators)) is not None:
        return JSONResponse(
            status_code=400, content={"success": False, "message": block_msg}
        )
    # 至少 1 个 selector(否则蒸馏退化成纯过滤,语义不符)
    has_selector = any(
        (oc.get_operator(o.name) or {}).get("category") == "selector"
        for o in body.operators
    )
    if not has_selector:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": "蒸馏算子链必须包含至少 1 个 selector(如 topk_specified_field_selector)",
            },
        )

    input_version = await session.get(DatasetVersion, body.dataset_version_id)
    if input_version is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "数据集版本不存在"},
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
            content={"success": False, "message": "蒸馏不支持 manifest 输入"},
        )

    if job is None:
        job = Job(
            id=_new_job_id(),
            name=body.name,
            type=_DISTILL_TYPE,
            state="pending",
            progress=0,
            created_by=user.username if user else "admin",
            # 完整存 body(output_dataset_id + 算子链),供 rerun 整参重跑
            spec=body.model_dump(mode="json"),
            pipeline_id=body.pipeline_id,
        )
        session.add(job)
    else:
        await _reset_for_edit_rerun(session, job, body)
    await session.commit()
    await session.refresh(job)
    # spawn 只传 job_id:output_dataset_id 已随 body 落进 job.spec,
    # job_runner._run_job 按 type=distillation 从 spec 重建 body
    job_runner.spawn(job.id)
    return JSONResponse(content=_item(job))


# ---------------------------------------------------------------------------
# 端点
# ---------------------------------------------------------------------------
@router.post("/distillation/jobs")
async def create_distillation_job(
    body: DistillationJobCreate,
    session: SessionDep,
    user: Annotated[User, Depends(require_user)],
) -> JSONResponse:
    """新建数据蒸馏任务并异步执行(类比 processing,不阻塞)。

    需登录 + 输入数据集 ACL ≥ edit(超管/owner 隐式满足)。
    """
    return await _start_distillation(session, body, user=user)


@router.get(
    "/distillation/jobs",
    response_model=PageResponse[JobRead],
    dependencies=[Depends(require_perm("governance:distillation:list"))],
)
async def list_distillation_jobs(
    session: SessionDep,
    current: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, alias="pageSize")] = 10,
    dataset_id: Annotated[str | None, Query(alias="datasetId")] = None,
    user: Annotated[User | None, Depends(current_user)] = None,
) -> PageResponse[JobRead]:
    """分页列出蒸馏任务(``Job.type='distillation'``),按创建时间倒序;
    可按 datasetId 过滤(输入或产物版本属于该数据集)。
    非超管只看到自己创建的 + 授权数据集上的任务。"""
    count_stmt = select(func.count()).select_from(Job).where(Job.type == _DISTILL_TYPE)
    list_stmt = select(Job).where(Job.type == _DISTILL_TYPE)
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
        read.output = await _build_output(session, r.id)
        data.append(read)
    return PageResponse[JobRead](data=data, total=total)


@router.get("/distillation/jobs/{job_id}")
async def get_distillation_job(job_id: str, session: SessionDep) -> JSONResponse:
    """蒸馏任务详情(含产物版本 + 输入版本)。"""
    job = await session.get(Job, job_id)
    if job is None or job.type != _DISTILL_TYPE:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "蒸馏任务不存在"},
        )
    output = await _build_output(session, job.id)
    input_ = await _build_input(session, job.id)
    return JSONResponse(content=_item(job, output, input_))


@router.put("/distillation/jobs/{job_id}")
async def update_distillation_job(
    job_id: str,
    body: DistillationJobCreate,
    session: SessionDep,
    user: Annotated[User, Depends(require_user)],
) -> JSONResponse:
    """编辑蒸馏任务:覆盖原任务配置并原地重跑(沿用任务 id,不新建记录)。

    仅终态/已暂停任务可编辑;运行中/排队中 → 409(先停止)。
    需登录 + 输入数据集 ACL ≥ edit。
    """
    job = await session.get(Job, job_id)
    if job is None or job.type != _DISTILL_TYPE:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "蒸馏任务不存在"},
        )
    if job.state in ("pending", "running"):
        return JSONResponse(
            status_code=409,
            content={"success": False, "message": "任务运行中,请先停止再编辑"},
        )
    return await _start_distillation(session, body, job=job, user=user)


@router.post("/distillation/jobs/{job_id}/rerun")
async def rerun_distillation_job(
    job_id: str,
    session: SessionDep,
    user: Annotated[User, Depends(require_user)],
) -> JSONResponse:
    """用原 spec 重跑(output_dataset_id 一并复用)。需登录 + 输入数据集 ACL ≥ edit。"""
    job = await session.get(Job, job_id)
    if job is None or job.type != _DISTILL_TYPE:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "蒸馏任务不存在"},
        )
    if not job.spec:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "该任务无可重跑的配置"},
        )
    try:
        spec = DistillationJobCreate.model_validate(job.spec)
    except ValidationError:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "任务配置已损坏,无法重跑"},
        )
    return await _start_distillation(session, spec, user=user)


@router.post(
    "/distillation/jobs/{job_id}/stop", dependencies=[Depends(require_admin)]
)
async def stop_distillation_job(
    job_id: str, session: SessionDep
) -> JSONResponse:
    """停止运行中/排队的蒸馏任务:杀子进程 + 标 cancelled。"""
    job = await session.get(Job, job_id)
    if job is None or job.type != _DISTILL_TYPE:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "蒸馏任务不存在"},
        )
    if job.state not in ("pending", "running"):
        return JSONResponse(
            status_code=409,
            content={"success": False, "message": "任务不在运行中,无法停止"},
        )
    job_runner.request_cancel(job_id)
    from app.services.engine import terminate_job  # 局部 import 避免循环

    terminate_job(job_id)
    job.state = "cancelled"
    job.finished_at = _now()
    await session.commit()
    return JSONResponse(content={"success": True})


async def _delete_distillation_cascade(session: AsyncSession, job: Job) -> None:
    """清血缘 + 置空产物上游 + 删任务本身(同 jobs._delete_job_cascade)。"""
    await session.execute(delete(JobInput).where(JobInput.job_id == job.id))
    await session.execute(
        update(DatasetVersion)
        .where(DatasetVersion.produced_by_job_id == job.id)
        .values(produced_by_job_id=None)
    )
    await session.delete(job)


@router.delete(
    "/distillation/jobs/{job_id}", dependencies=[Depends(require_admin)]
)
async def delete_distillation_job(
    job_id: str, session: SessionDep
) -> JSONResponse:
    """删除蒸馏任务记录(只删任务,产物版本保留)。"""
    job = await session.get(Job, job_id)
    if job is None or job.type != _DISTILL_TYPE:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "蒸馏任务不存在"},
        )
    if job.state == "running":
        return JSONResponse(
            status_code=409,
            content={"success": False, "message": "任务运行中,无法删除"},
        )
    await _delete_distillation_cascade(session, job)
    await session.commit()
    return JSONResponse(content={"success": True})


@router.post(
    "/distillation/jobs/batch-delete", dependencies=[Depends(require_admin)]
)
async def batch_delete_distillation_jobs(
    body: BatchDeleteRequest, session: SessionDep
) -> JSONResponse:
    """批量删除蒸馏任务,返回实际删除数量。"""
    deleted = 0
    for job_id in body.ids:
        job = await session.get(Job, job_id)
        if job is None or job.type != _DISTILL_TYPE or job.state == "running":
            continue
        await _delete_distillation_cascade(session, job)
        deleted += 1
    await session.commit()
    return JSONResponse(content={"data": {"deleted": deleted}, "success": True})


@router.get("/distillation/jobs/{job_id}/report")
async def get_distillation_report(
    job_id: str, session: SessionDep
) -> JSONResponse:
    """读 report.json 拿蒸馏报告(输入/输出条数/保留比例/warnings)。

    任务未跑完 → 返回空报告 + state 给前端做骨架占位。
    """
    job = await session.get(Job, job_id)
    if job is None or job.type != _DISTILL_TYPE:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "蒸馏任务不存在"},
        )
    # 找该任务的产物版本,定位 report.json
    stmt = (
        select(DatasetVersion)
        .where(DatasetVersion.produced_by_job_id == job_id)
        .order_by(DatasetVersion.created_at.desc())
    )
    version = (await session.scalars(stmt)).first()
    if version is None:
        # 还没产出(pending/running/failed),返回骨架
        empty = DistillationReport(
            job_id=job.id,
            input_version_id=(job.spec or {}).get("dataset_version_id", ""),
            input_count=0,
            operator_chain=[o["name"] for o in (job.spec or {}).get("operators", [])],
            warnings=["任务尚未完成"] if job.state != "success" else [],
        )
        return JSONResponse(
            content={
                "data": empty.model_dump(mode="json", by_alias=True),
                "success": True,
            }
        )
    out_dir = Path(settings.datasets_dir) / version.dataset_id / f"v{version.version_no}"
    report_path = out_dir / "report.json"
    if not report_path.exists():
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "报告文件不存在"},
        )
    try:
        raw: dict[str, Any] = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": f"报告解析失败:{exc}"},
        )
    # report.json 是服务端落盘文件(字段 snake_case);返回给前端必须 camelCase,
    # 避免前端 camelCase 字段访问全是 undefined 导致 Modal 渲染空白/数字显示成 NaN。
    try:
        parsed = DistillationReport.model_validate(raw)
    except ValidationError:
        # 兼容老版本或外部写入的 report.json:schema 不匹配时退回到字段重命名兜底,
        # 不抛 500 阻断前端。
        rename = {
            "job_id": "jobId",
            "input_version_id": "inputVersionId",
            "output_version_id": "outputVersionId",
            "input_count": "inputCount",
            "output_count": "outputCount",
            "keep_ratio_actual": "keepRatioActual",
            "dedup_removed": "dedupRemoved",
            "filter_removed": "filterRemoved",
            "elapsed_seconds": "elapsedSeconds",
            "operator_chain": "operatorChain",
        }
        parsed_dict = {rename.get(k, k): v for k, v in raw.items()}
        return JSONResponse(content={"data": parsed_dict, "success": True})
    return JSONResponse(
        content={"data": parsed.model_dump(mode="json", by_alias=True), "success": True}
    )
