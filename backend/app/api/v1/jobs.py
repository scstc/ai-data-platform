"""加工任务路由:算子目录、建任务并执行(子进程跑 dj-process)、列表、详情。"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.core.db import get_session
from app.models.dataset import Dataset
from app.models.dataset_version import DatasetVersion
from app.models.job import Job
from app.models.job_input import JobInput
from app.schemas.common import CamelModel, PageResponse, format_version_label
from app.schemas.job import JobCreate, JobRead, OperatorSpec
from app.services import job_runner
from app.services import operator_catalog as oc
from app.services.engine import (
    EngineError,
    multimodal_ready,
    run_preview,
    terminate_job,
)
from app.services.external_store import ExternalStoreError
from app.services.landing import BINARY_FORMATS, MANIFEST_FORMAT
from app.services.llm_config import get_active_llm_config

router = APIRouter(tags=["jobs"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _binary_block(version: DatasetVersion) -> JSONResponse | None:
    """二进制数据集(图像/音频/视频)无法规范化为 jsonl → 提前 400,不让任务跑起来才失败。"""
    if version.format in BINARY_FORMATS:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": (
                    f"二进制数据集(.{version.format})不支持加工,请选择文本类数据集"
                ),
            },
        )
    return None


async def _multimodal_block(version: DatasetVersion) -> JSONResponse | None:
    """媒体(manifest)数据集需 DJ 多模态引擎(torch);本部署没装则提前 400。

    避免在无 torch 的环境里运行时 ``uv pip install torch`` 卡几十分钟(且装进重启即丢
    的可写层)。媒体加工请在已装多模态引擎的环境(如 GPU 机)运行。
    """
    if version.format == MANIFEST_FORMAT and not await multimodal_ready():
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": (
                    "当前部署未安装多模态引擎(torch),媒体数据集加工请在已装多模态"
                    "引擎的环境(如 GPU 机)运行"
                ),
            },
        )
    return None


def _operator_block(
    operators: list[OperatorSpec], version: DatasetVersion
) -> JSONResponse | None:
    """按 LLM 配置 + 数据类型校验算子可执行性,有跑不了的返回 400(含原因)。

    needs_api 看是否配了 LLM;needs_media 看输入是否为 manifest 媒体集
    (_multimodal_block 已确保此处 manifest ⇒ torch 就绪);needs_compute 恒拦截。
    调用前须已做"非空 + 算子存在"校验。
    """
    llm_configured = bool(get_active_llm_config().api_key)
    media_ok = version.format == MANIFEST_FORMAT
    blocked = [
        reason
        for o in operators
        if (
            reason := oc.runnable_reason(
                o.name, llm_configured=llm_configured, media_ok=media_ok
            )
        )
    ]
    if blocked:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "；".join(blocked)},
        )
    return None


class JobItemResponse(CamelModel):
    """单个加工任务响应：{data:{...}, success:true}。"""

    data: JobRead
    success: bool = True


class PreviewRequest(CamelModel):
    """样例试跑入参:在某数据集版本前 N 行上跑算子,不建版本、不写 DB。"""

    dataset_version_id: str
    operators: list[OperatorSpec]
    sample_size: int = 20
    # 清洗作用字段(留空=自动探测);与 create_job 一致,使试跑与正式任务效果对齐
    text_keys: list[str] | None = None


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
        "versionLabel": format_version_label(version.version_no, version.created_at),
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
        "versionLabel": format_version_label(version.version_no, version.created_at),
    }


def _item(
    job: Job, output: dict | None = None, input_: dict | None = None
) -> dict:
    read = JobRead.model_validate(job)
    read.can_rerun = bool(job.spec)
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
        read.can_rerun = bool(r.spec)
        read.input = await _build_input(session, r.id)
        read.output = await _build_output(session, r.id)
        data.append(read)
    return PageResponse[JobRead](data=data, total=total)


async def _start_job(session: AsyncSession, body: JobCreate) -> JSONResponse:
    """校验 → 建任务(pending,存 spec 以备重跑)→ 起后台任务执行 → 立即返回(不等跑完)。

    实际执行在 job_runner 后台进行(状态机 pending→running→success/failed/cancelled),
    任务可经 POST /jobs/{id}/stop 停止;create_job 与 rerun_job 共用此入口
    (rerun 用原任务存下的 spec 重建 body)。
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
    input_version = await session.get(DatasetVersion, body.dataset_version_id)
    if input_version is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "数据集版本不存在"},
        )
    if (blocked_resp := _binary_block(input_version)) is not None:
        return blocked_resp
    if (blocked_resp := await _multimodal_block(input_version)) is not None:
        return blocked_resp

    # 资源前置校验:按 LLM 配置 + 数据类型把算子路由到合适后端,跑不了的提前拦截给原因。
    # media_ok:输入是 manifest 媒体集(_multimodal_block 已确保此时 torch 就绪)。
    if (blocked_resp := _operator_block(body.operators, input_version)) is not None:
        return blocked_resp

    job = Job(
        id=_new_job_id(),
        name=body.name,
        type=body.type,
        state="pending",
        progress=0,
        created_by="admin",
        # 存原始执行规格(算子 + 输出去向 + 输入版本),供 rerun 原样重跑
        spec=body.model_dump(mode="json"),
    )
    session.add(job)
    await session.commit()
    await session.refresh(job)
    # 交后台执行:产物去向(含另存新数据集的构建)由 job_runner 在加工时处理
    # spawn 只传 job_id——入参 body 由 _run_job 从 job.spec 重建,统一新建/重跑/继续
    job_runner.spawn(job.id)
    return JSONResponse(content=_item(job))


@router.post("/jobs", dependencies=[Depends(require_admin)])
async def create_job(body: JobCreate, session: SessionDep) -> JSONResponse:
    """新建加工任务并后台执行:对一个数据集版本跑算子流水线 → 产出新版本。

    立即返回 pending 任务(不阻塞到跑完);进度经轮询 GET 反映,可经 stop 端点停止。
    """
    return await _start_job(session, body)


@router.post("/jobs/{job_id}/rerun", dependencies=[Depends(require_admin)])
async def rerun_job(job_id: str, session: SessionDep) -> JSONResponse:
    """用原任务存下的配置(算子 + 输出去向)对原输入版本重跑一次 → 产出新版本。

    新建一条任务记录(不改动原记录),保留每次运行的血缘;早于本特性、无 spec
    的旧任务返回 400。输入版本若已删除,沿用 create 校验返回 404。
    """
    job = await session.get(Job, job_id)
    if job is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "任务不存在"},
        )
    if not job.spec:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": "该任务无可重跑的配置(早于重跑特性创建),请新建任务",
            },
        )
    try:
        spec = JobCreate.model_validate(job.spec)
    except ValidationError:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "任务配置已损坏,无法重跑"},
        )
    return await _start_job(session, spec)


@router.post("/jobs/{job_id}/stop", dependencies=[Depends(require_admin)])
async def stop_job(job_id: str, session: SessionDep) -> JSONResponse:
    """停止运行中/排队中/已暂停的任务:杀子进程(若有)并把任务标记为 cancelled。

    pending/running:登记停止意图(让排队中的后台任务起跑前放弃、被杀任务记
    cancelled 而非 failed)+ 杀子进程 + 置 cancelled。paused:已无后台任务在跑,
    直接置 cancelled。终态任务 → 409;未知 → 404。停止不删产物,要重来用「重新运行」。
    """
    job = await session.get(Job, job_id)
    if job is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "任务不存在"},
        )
    if job.state not in ("pending", "running", "paused"):
        return JSONResponse(
            status_code=409,
            content={"success": False, "message": "任务已结束,无需停止"},
        )
    if job.state in ("pending", "running"):
        was_running = job.state == "running"
        job_runner.request_cancel(job_id)
        # 有子进程(治理/quality)→ terminate_job 杀之;无子进程却在 running
        # (review 纯计算/LLM)→ 取消后台协程。pending 靠起跑前意图检查,无需取消。
        if not terminate_job(job_id) and was_running:
            job_runner.cancel_running_task(job_id)
    job.state = "cancelled"
    job.finished_at = _now()
    await session.commit()
    return JSONResponse(content={"success": True})


@router.post("/jobs/{job_id}/pause", dependencies=[Depends(require_admin)])
async def pause_job(job_id: str, session: SessionDep) -> JSONResponse:
    """暂停运行中/排队中的任务:杀子进程(若有)并标记为 paused(保留 spec)。

    dj-process 无原生暂停,故暂停=终止当前运行;继续(resume)按 spec 从头重跑,
    不保留已处理进度——这是诚实语义,前端需明示告知用户。仅 pending/running 可暂停;
    paused/终态 → 409;未知 → 404。
    """
    job = await session.get(Job, job_id)
    if job is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "任务不存在"},
        )
    if job.state not in ("pending", "running"):
        return JSONResponse(
            status_code=409,
            content={"success": False, "message": "任务不在运行中,无法暂停"},
        )
    # 先登记暂停意图(让排队中的后台任务起跑前放弃、被杀任务记 paused 而非 failed);
    # 有子进程→terminate_job 杀之;无子进程却在 running(review)→ 取消后台协程。
    was_running = job.state == "running"
    job_runner.request_pause(job_id)
    if not terminate_job(job_id) and was_running:
        job_runner.cancel_running_task(job_id)
    job.state = "paused"
    job.finished_at = _now()
    await session.commit()
    return JSONResponse(content={"success": True})


@router.post("/jobs/{job_id}/resume", dependencies=[Depends(require_admin)])
async def resume_job(job_id: str, session: SessionDep) -> JSONResponse:
    """继续已暂停的任务:重置为 pending 并按原 spec 从头重跑(复用同一 Job 行,不新建)。

    dj-process 无断点续跑,故继续即整任务重跑(语义同 rerun,但不另建记录、保留原
    任务血缘与 id)。仅 paused 可继续;其余 → 409;spec 损坏 → 400;未知 → 404。
    """
    job = await session.get(Job, job_id)
    if job is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "任务不存在"},
        )
    if job.state != "paused":
        return JSONResponse(
            status_code=409,
            content={"success": False, "message": "仅已暂停的任务可继续"},
        )
    try:
        job_runner.body_from_spec(job)  # 前置校验 spec 可重建为可执行 body
    except (ValidationError, ValueError):
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "任务配置已损坏,无法继续"},
        )
    job.state = "pending"
    job.progress = 0
    job.error = None
    job.started_at = None
    job.finished_at = None
    await session.commit()
    await session.refresh(job)
    job_runner.spawn(job.id)
    return JSONResponse(content=_item(job))


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
    size = max(1, min(body.sample_size, 200))
    input_version = await session.get(DatasetVersion, body.dataset_version_id)
    if input_version is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "数据集版本不存在"},
        )
    if (blocked_resp := _binary_block(input_version)) is not None:
        return blocked_resp
    if (blocked_resp := await _multimodal_block(input_version)) is not None:
        return blocked_resp
    if (blocked_resp := _operator_block(body.operators, input_version)) is not None:
        return blocked_resp

    try:
        result = await run_preview(
            session,
            input_version=input_version,
            operators=[o.model_dump() for o in body.operators],
            sample_size=size,
            text_keys=body.text_keys,
        )
    except (EngineError, ExternalStoreError) as exc:
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


async def _delete_job_cascade(session: AsyncSession, job: Job) -> None:
    """清该任务血缘边(job_inputs)+ 置空其产物版本上游(保留版本)+ 删任务本身,不 commit。

    调用方需先确保 job 可删(存在且非 running)。单删与批量删共用此清理逻辑。
    """
    await session.execute(delete(JobInput).where(JobInput.job_id == job.id))
    await session.execute(
        update(DatasetVersion)
        .where(DatasetVersion.produced_by_job_id == job.id)
        .values(produced_by_job_id=None)
    )
    await session.delete(job)


@router.delete("/jobs/{job_id}", dependencies=[Depends(require_admin)])
async def delete_job(job_id: str, session: SessionDep) -> JSONResponse:
    """删除加工任务记录(只删任务,不删产物)。

    清掉该任务的血缘边(job_inputs),并把它产出版本的 produced_by_job_id 置空——
    产出的数据集版本作为独立资产保留(可能已发布 / 被下游引用,有独立删除入口)。
    运行中的任务不可删 → 409;未知任务 → 404。
    """
    job = await session.get(Job, job_id)
    if job is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "任务不存在"},
        )
    if job.state == "running":
        return JSONResponse(
            status_code=409,
            content={"success": False, "message": "任务运行中,无法删除"},
        )
    await _delete_job_cascade(session, job)
    await session.commit()
    return JSONResponse(content={"success": True})


class BatchDeleteRequest(CamelModel):
    """批量删除入参。"""

    ids: list[str]


@router.post("/jobs/batch-delete", dependencies=[Depends(require_admin)])
async def batch_delete_jobs(
    body: BatchDeleteRequest, session: SessionDep
) -> JSONResponse:
    """批量删除加工任务记录,返回实际删除数量(只删任务,产物版本保留)。

    删除语义同单条 delete;运行中或不存在的任务自动跳过(不阻断整批)。
    """
    deleted = 0
    for job_id in body.ids:
        job = await session.get(Job, job_id)
        if job is None or job.state == "running":
            continue
        await _delete_job_cascade(session, job)
        deleted += 1
    await session.commit()
    return JSONResponse(content={"data": {"deleted": deleted}, "success": True})
