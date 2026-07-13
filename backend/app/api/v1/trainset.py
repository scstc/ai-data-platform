"""数据合成(trainset) API:8 个端点,Job.type='trainset'。

数据合成——LLM 从源数据造训练样本(QA/COT/偏好,1→N)。算子需 LLM,
未配 LLM Key → needs_api 拦截。产物 ``DatasetVersion.origin='synthetic'``。
与 augment(1→1 改写)、make(merge 拼接)共享 origin 约定,但 Job.type 独立。

注意:内部标识 ``trainset``,避开已被「数据集构造层」占用的 ``construct``。
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
    _reset_for_edit_rerun,
)
from app.core.config import settings
from app.models.dataset_version import DatasetVersion
from app.models.job import Job
from app.models.job_input import JobInput
from app.models.user import User
from app.schemas.common import PageResponse
from app.schemas.job import JobRead
from app.schemas.trainset import TrainsetJobCreate, TrainsetReport
from app.services import dataset_acl, job_runner
from app.services import operator_catalog as oc
from app.services.llm_config import get_active_llm_config

router = APIRouter(tags=["trainset"])

_TRAINSET_TYPE = "trainset"


def _trainset_operator_block(operators: list) -> str | None:
    """资源前置校验:算子不限白名单,仅按运行时能力(LLM/GPU)逐个拦截。

    不整体强制配 LLM:算子可选用本地模型(hf_* 参数),仅 needs_api 算子
    在未配 LLM 时被单独拦下(配置测试通过即算已配,无需激活)。
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


async def _start_trainset(
    session: AsyncSession,
    body: TrainsetJobCreate,
    job: Job | None = None,
    user: User | None = None,
) -> JSONResponse:
    """数据合成版 _start_job:校验 → 建任务 → spawn 后台 → 立即返回 pending。

    传入 job = 编辑任务:校验通过后覆盖该任务配置并原地重跑,不新建记录。
    """
    if body.member_configs and body.operators:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": "不能同时指定 memberConfigs 和 operators",
            },
        )
    if not body.member_configs and not body.operators:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "请至少选择一个算子"},
        )
    if body.member_configs:
        for cfg in body.member_configs:
            if not cfg.operators:
                return JSONResponse(
                    status_code=400,
                    content={
                        "success": False,
                        "message": f"成员 {cfg.member_name} 请至少选择一个算子",
                    },
                )
    # member_configs 模式下拉平所有成员算子做统一校验(未知算子/资源门)
    all_operators = body.operators or [
        o for cfg in body.member_configs for o in cfg.operators
    ]
    unknown = [o.name for o in all_operators if oc.get_operator(o.name) is None]
    if unknown:
        return JSONResponse(
            status_code=400, content={"success": False, "message": f"未知算子:{', '.join(unknown)}"}
        )
    if (block_msg := _trainset_operator_block(all_operators)) is not None:
        return JSONResponse(
            status_code=400, content={"success": False, "message": block_msg}
        )
    if body.goal.mode not in ("synthesize",):
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": f"goal.mode 非法:{body.goal.mode}"},
        )

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
    # member_configs 指向的成员必须存在于该版本(否则引擎会静默跳过)
    if body.member_configs:
        from app.models.dataset_version_table import DatasetVersionTable

        stmt = select(DatasetVersionTable.table_name).where(
            DatasetVersionTable.dataset_version_id == body.dataset_version_id
        )
        member_names = set((await session.scalars(stmt)).all())
        missing = [
            cfg.member_name
            for cfg in body.member_configs
            if cfg.member_name not in member_names
        ]
        if missing:
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "message": f"成员不存在:{', '.join(missing)}",
                },
            )

    if job is None:
        job = Job(
            id=_new_job_id(),
            name=body.name,
            type=_TRAINSET_TYPE,
            state="pending",
            progress=0,
            created_by=user.username if user else "admin",
            spec=body.model_dump(mode="json"),
            pipeline_id=body.pipeline_id,
        )
        session.add(job)
    else:
        await _reset_for_edit_rerun(session, job, body)
    await session.commit()
    await session.refresh(job)
    # spawn 只传 job_id:goal/output_dataset_id 已随 body 落进 job.spec,
    # job_runner._run_job 按 type=trainset 从 spec 重建 body 并取出 goal 等
    job_runner.spawn(job.id)
    return JSONResponse(content=_item(job))


@router.post("/trainset/jobs")
async def create_trainset_job(
    body: TrainsetJobCreate,
    session: SessionDep,
    user: Annotated[User, Depends(require_user)],
) -> JSONResponse:
    """新建数据合成任务并异步执行。

    需登录 + 输入数据集 ACL ≥ edit(超管/owner 隐式满足)。
    """
    return await _start_trainset(session, body, user=user)


@router.get(
    "/trainset/jobs",
    response_model=PageResponse[JobRead],
    dependencies=[Depends(require_perm("governance:trainset:list"))],
)
async def list_trainset_jobs(
    session: SessionDep,
    current: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, alias="pageSize")] = 10,
    dataset_id: Annotated[str | None, Query(alias="datasetId")] = None,
    user: Annotated[User | None, Depends(current_user)] = None,
) -> PageResponse[JobRead]:
    """分页列出数据合成任务;可按 datasetId 过滤(输入或产物版本属于该数据集)。

    非超管只看到自己创建的 + 授权数据集上的任务。
    """
    count_stmt = select(func.count()).select_from(Job).where(Job.type == _TRAINSET_TYPE)
    list_stmt = select(Job).where(Job.type == _TRAINSET_TYPE)
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


@router.get("/trainset/jobs/{job_id}")
async def get_trainset_job(job_id: str, session: SessionDep) -> JSONResponse:
    """数据合成任务详情。"""
    job = await session.get(Job, job_id)
    if job is None or job.type != _TRAINSET_TYPE:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "数据合成任务不存在"}
        )
    output = await _build_output(session, job.id)
    input_ = await _build_input(session, job.id)
    return JSONResponse(content=_item(job, output, input_))


@router.put("/trainset/jobs/{job_id}")
async def update_trainset_job(
    job_id: str,
    body: TrainsetJobCreate,
    session: SessionDep,
    user: Annotated[User, Depends(require_user)],
) -> JSONResponse:
    """编辑数据合成任务:覆盖原任务配置并原地重跑(沿用任务 id,不新建记录)。

    仅终态/已暂停任务可编辑;运行中/排队中 → 409(先停止)。
    需登录 + 输入数据集 ACL ≥ edit。
    """
    job = await session.get(Job, job_id)
    if job is None or job.type != _TRAINSET_TYPE:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "数据合成任务不存在"}
        )
    if job.state in ("pending", "running"):
        return JSONResponse(
            status_code=409,
            content={"success": False, "message": "任务运行中,请先停止再编辑"},
        )
    return await _start_trainset(session, body, job=job, user=user)


@router.post("/trainset/jobs/{job_id}/rerun")
async def rerun_trainset_job(
    job_id: str,
    session: SessionDep,
    user: Annotated[User, Depends(require_user)],
) -> JSONResponse:
    """用原 spec 重跑。需登录 + 输入数据集 ACL ≥ edit。"""
    job = await session.get(Job, job_id)
    if job is None or job.type != _TRAINSET_TYPE:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "数据合成任务不存在"}
        )
    if not job.spec:
        return JSONResponse(
            status_code=400, content={"success": False, "message": "该任务无可重跑的配置"}
        )
    try:
        spec = TrainsetJobCreate.model_validate(job.spec)
    except ValidationError:
        return JSONResponse(
            status_code=400, content={"success": False, "message": "任务配置已损坏,无法重跑"}
        )
    return await _start_trainset(session, spec, user=user)


@router.post(
    "/trainset/jobs/{job_id}/stop", dependencies=[Depends(require_admin)]
)
async def stop_trainset_job(job_id: str, session: SessionDep) -> JSONResponse:
    """停止运行中/排队的数据合成任务。"""
    job = await session.get(Job, job_id)
    if job is None or job.type != _TRAINSET_TYPE:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "数据合成任务不存在"}
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


async def _delete_trainset_cascade(session: AsyncSession, job: Job) -> None:
    await session.execute(delete(JobInput).where(JobInput.job_id == job.id))
    await session.execute(
        update(DatasetVersion)
        .where(DatasetVersion.produced_by_job_id == job.id)
        .values(produced_by_job_id=None)
    )
    await session.delete(job)


@router.delete(
    "/trainset/jobs/{job_id}", dependencies=[Depends(require_admin)]
)
async def delete_trainset_job(job_id: str, session: SessionDep) -> JSONResponse:
    """删除数据合成任务(只删任务,产物版本保留)。"""
    job = await session.get(Job, job_id)
    if job is None or job.type != _TRAINSET_TYPE:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "数据合成任务不存在"}
        )
    if job.state == "running":
        return JSONResponse(
            status_code=409, content={"success": False, "message": "任务运行中,无法删除"}
        )
    await _delete_trainset_cascade(session, job)
    await session.commit()
    return JSONResponse(content={"success": True})


@router.post(
    "/trainset/jobs/batch-delete", dependencies=[Depends(require_admin)]
)
async def batch_delete_trainset_jobs(
    body: BatchDeleteRequest, session: SessionDep
) -> JSONResponse:
    """批量删除数据合成任务。"""
    deleted = 0
    for job_id in body.ids:
        job = await session.get(Job, job_id)
        if job is None or job.type != _TRAINSET_TYPE or job.state == "running":
            continue
        await _delete_trainset_cascade(session, job)
        deleted += 1
    await session.commit()
    return JSONResponse(content={"data": {"deleted": deleted}, "success": True})


@router.get("/trainset/jobs/{job_id}/report")
async def get_trainset_report(job_id: str, session: SessionDep) -> JSONResponse:
    """读 report.json 拿生成报告(输入/输出条数/扩增比/warnings)。"""
    job = await session.get(Job, job_id)
    if job is None or job.type != _TRAINSET_TYPE:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "数据合成任务不存在"}
        )
    stmt = (
        select(DatasetVersion)
        .where(DatasetVersion.produced_by_job_id == job_id)
        .order_by(DatasetVersion.created_at.desc())
    )
    version = (await session.scalars(stmt)).first()
    if version is None:
        spec = job.spec or {}
        goal_mode = (spec.get("goal") or {}).get("mode", "synthesize")
        empty = TrainsetReport(
            job_id=job.id,
            input_version_id=spec.get("dataset_version_id", ""),
            mode=goal_mode,
            input_count=0,
            operator_chain=[o["name"] for o in spec.get("operators") or []],
            warnings=["任务尚未完成"] if job.state != "success" else [],
        )
        return JSONResponse(content={"data": empty.model_dump(mode="json"), "success": True})
    out_dir = Path(settings.datasets_dir) / version.dataset_id / f"v{version.version_no}"
    report_path = out_dir / "report.json"
    if not report_path.exists():
        return JSONResponse(
            status_code=404, content={"success": False, "message": "报告文件不存在"}
        )
    try:
        raw: dict[str, Any] = json.loads(report_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return JSONResponse(
            status_code=500, content={"success": False, "message": f"报告解析失败:{exc}"}
        )
    return JSONResponse(content={"data": raw, "success": True})
