"""加工任务路由:算子目录、建任务并执行(子进程跑 dj-process)、列表、详情。"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_user, require_admin, require_user
from app.core.db import get_session
from app.models.dataset import Dataset
from app.models.dataset_version import DatasetVersion
from app.models.job import Job
from app.models.job_input import JobInput
from app.models.user import User
from app.schemas.common import CamelModel, PageResponse, format_version_label
from app.schemas.job import JobCreate, JobRead, OperatorSpec
from app.services import dataset_acl, job_runner
from app.services import operator_catalog as oc
from app.services.capabilities import get_capabilities
from app.services.engine import (
    multimodal_ready,
    terminate_job,
)
from app.services.landing import BINARY_FORMATS, MANIFEST_FORMAT, MANIFEST_MEMBER_NAME
from app.services.llm_config import get_active_llm_config, get_effective_provider

router = APIRouter(tags=["jobs"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _binary_block(version: DatasetVersion) -> JSONResponse | None:
    """二进制数据集(图像/音频/视频)无法规范化为 jsonl
    → 提前 400,不让任务跑起来才失败。"""
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


async def _deleted_dataset_block(
    session: AsyncSession, dataset_id: str
) -> JSONResponse | None:
    """输入数据集已进回收站(软删)或不存在 → 404,堵「看不见但还在跑」。

    数据集列表/详情已把回收站数据集隐藏(见 datasets.list_datasets/get_dataset 的
    deleted_at 过滤),质量评估/合并/合成的建任务入口不能绕过这层不可见性继续对它
    发起新任务——否则出现"数据集在界面上已消失,任务却还能对它跑起来"的矛盾态。
    仅按 dataset_id 校验(回收站是软删语义,版本行本身仍在库中未必跟随硬删)。
    """
    dataset = await session.get(Dataset, dataset_id)
    if dataset is None or dataset.deleted_at is not None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "数据集已删除或在回收站"},
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


async def _cross_provider_model_block(
    session: AsyncSession, operators: list[OperatorSpec]
) -> JSONResponse | None:
    """跨供应商模型拦截:凭证与端点是任务级单例(engine 只注入生效供应商的
    key,llm-proxy 只转发到快照里的一个 base_url),算子显式选了其他供应商
    清单里的模型,运行期必然 401/模型不存在 → 建任务时提前 400 给出原因。

    只拦"确定属于其他供应商清单且不在生效供应商清单"的模型;生效供应商
    清单内、生效供应商当前模型、本地 HF 模型与自由输入的未知名称一律放行
    (api_or_hf_model 合法值可以是本地模型,不可误伤)。库里无供应商
    (纯 env 回退)时无从判定归属,跳过。
    """
    from app.models.llm_model import LlmModel

    picked = {
        str(v)
        for o in operators
        for k in ("api_model", "api_or_hf_model")
        if (v := (o.params or {}).get(k))
    }
    if not picked:
        return None
    effective = await get_effective_provider(session)
    if effective is None:
        return None
    rows = (
        await session.execute(
            select(LlmModel.provider_id, LlmModel.model).where(
                LlmModel.model.in_(picked)
            )
        )
    ).all()
    owners: dict[str, set[str]] = {}
    for pid, model in rows:
        owners.setdefault(model, set()).add(pid)
    offending = [
        (model, owner_ids)
        for model in sorted(picked)
        if model != effective.model
        and (owner_ids := owners.get(model))
        and effective.id not in owner_ids
    ]
    if not offending:
        return None
    from app.models.llm_provider import LlmProvider

    ids = {pid for _, owner_ids in offending for pid in owner_ids}
    names = {
        p.id: p.name
        for p in (
            await session.scalars(select(LlmProvider).where(LlmProvider.id.in_(ids)))
        ).all()
    }
    model, owner_ids = offending[0]
    other = "、".join(sorted(names.get(i, i) for i in owner_ids))
    return JSONResponse(
        status_code=400,
        content={
            "success": False,
            "message": (
                f"模型 {model} 属于供应商「{other}」,而当前生效供应商是"
                f"「{effective.name}」;同一任务的密钥与端点按生效供应商解析,"
                f"请改选「{effective.name}」的模型,或先在 LLM 配置切换生效供应商"
            ),
        },
    )


class JobItemResponse(CamelModel):
    """单个加工任务响应：{data:{...}, success:true}。"""

    data: JobRead
    success: bool = True


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
    """查该任务的输入版本(经 job_inputs 血缘边反查,镜像 _build_output)。

    失败/取消的任务暂未写 job_inputs 血缘 → 回退到 spec.dataset_version_id 反查,
    让前端任务详情仍能展示「指定过哪个版本」(否则失败任务详情永远显示「无输入版本」)。
    """
    stmt = (
        select(DatasetVersion, Dataset)
        .join(JobInput, JobInput.dataset_version_id == DatasetVersion.id)
        .join(Dataset, Dataset.id == DatasetVersion.dataset_id)
        .where(JobInput.job_id == job_id)
    )
    row = (await session.execute(stmt)).first()
    if row is None:
        # 回退:从 spec 里取 dataset_version_id(dataset_version_id 蛇形键,
        # 与 spec model_dump 的字段一致),即便失败也能让前端展示指定过的版本
        job = await session.get(Job, job_id)
        spec = job.spec if job else None
        fallback_id = None
        if isinstance(spec, dict):
            fallback_id = spec.get("dataset_version_id") or spec.get(
                "datasetVersionId"
            )
        if not fallback_id:
            return None
        v = await session.get(DatasetVersion, fallback_id)
        if v is None:
            return None
        d = await session.get(Dataset, v.dataset_id)
        return {
            "datasetId": d.id if d else None,
            "datasetName": d.name if d else None,
            "versionId": v.id,
            "versionNo": v.version_no,
            "versionLabel": format_version_label(v.version_no, v.created_at),
            "fallback": True,  # 标记为 spec 反查(非成功血缘),供前端可选样式提示
        }
    version, dataset = row
    return {
        "datasetId": dataset.id,
        "datasetName": dataset.name,
        "versionId": version.id,
        "versionNo": version.version_no,
        "versionLabel": format_version_label(version.version_no, version.created_at),
    }


def _edit_spec(job: Job) -> dict | None:
    """把 job.spec(snake_case 原始存储)经对应 Create schema 转成 camelCase。

    供前端编辑器按 jobId 回填配置;spec 缺失/损坏返回 None(前端据此提示不可编辑)。
    """
    if not job.spec:
        return None
    try:
        body = job_runner.body_from_spec(job)
    except (ValidationError, ValueError):
        return None
    return body.model_dump(mode="json", by_alias=True)


def _item(
    job: Job, output: dict | None = None, input_: dict | None = None
) -> dict:
    read = JobRead.model_validate(job)
    read.can_rerun = bool(job.spec)
    read.edit_spec = _edit_spec(job)
    if output is not None:
        read.output = output
    if input_ is not None:
        read.input = input_
    return JobItemResponse(data=read).model_dump(by_alias=True, mode="json")


def _dataset_job_filter(dataset_id: str):
    """构造"任务的输入或产物版本属于该数据集"的 Job.id 过滤条件。

    输入侧:job_inputs → dataset_versions → dataset_id;
    产物侧:dataset_versions.produced_by_job_id == Job.id 且 dataset_id 命中。
    供治理/评估各任务列表按数据集筛选共用。返回可直接塞进 .where() 的表达式。
    """
    input_job_ids = (
        select(JobInput.job_id)
        .join(DatasetVersion, DatasetVersion.id == JobInput.dataset_version_id)
        .where(DatasetVersion.dataset_id == dataset_id)
    )
    output_job_ids = select(DatasetVersion.produced_by_job_id).where(
        DatasetVersion.dataset_id == dataset_id,
        DatasetVersion.produced_by_job_id.is_not(None),
    )
    return Job.id.in_(input_job_ids.union(output_job_ids))


async def _acl_job_filter(session: AsyncSession, user: User | None):
    """任务列表的数据集 ACL 可见性条件;超管/匿名返回 None(不过滤,沿用现状)。

    非超管登录用户只看得到:我创建的任务 + 输入/产物版本落在「我可见数据集」
    (owner/creator/ACL 授权,复用 visible_dataset_filter)上的任务。孤儿任务
    (输入版本已删且非我建)一并收敛,防经任务列表泄露他人数据集元信息。
    供 /jobs 与治理各任务列表共用。
    """
    if user is None or user.role == "admin":
        return None
    visible_ids = await dataset_acl.visible_dataset_filter(
        select(Dataset.id), session, user
    )
    input_job_ids = (
        select(JobInput.job_id)
        .join(DatasetVersion, DatasetVersion.id == JobInput.dataset_version_id)
        .where(DatasetVersion.dataset_id.in_(visible_ids))
    )
    output_job_ids = select(DatasetVersion.produced_by_job_id).where(
        DatasetVersion.produced_by_job_id.is_not(None),
        DatasetVersion.dataset_id.in_(visible_ids),
    )
    return or_(
        Job.created_by == user.username,
        Job.id.in_(input_job_ids.union(output_job_ids)),
    )


@router.get("/jobs", response_model=PageResponse[JobRead])
async def list_jobs(
    session: SessionDep,
    current: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, alias="pageSize")] = 10,
    type_: Annotated[str | None, Query(alias="type")] = None,
    dataset_id: Annotated[str | None, Query(alias="datasetId")] = None,
    user: Annotated[User | None, Depends(current_user)] = None,
) -> PageResponse[JobRead]:
    """分页列出加工任务,按创建时间倒序;可按 type 过滤(如 type=quality)、
    按 datasetId 过滤(输入或产物版本属于该数据集)。
    非超管只看到自己创建的 + 授权数据集上的任务(数据集 ACL 行级裁剪)。"""
    # 级联删除标记的任务(所属数据集过期进回收站)对所有人隐藏,恢复走回收站
    count_stmt = (
        select(func.count()).select_from(Job).where(Job.deleted_at.is_(None))
    )
    list_stmt = select(Job).where(Job.deleted_at.is_(None))
    if type_:
        count_stmt = count_stmt.where(Job.type == type_)
        list_stmt = list_stmt.where(Job.type == type_)
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


async def _reset_for_edit_rerun(
    session: AsyncSession, job: Job, body: CamelModel
) -> None:
    """「编辑任务」覆盖原任务配置并复位为 pending 以原地重跑,不 commit。

    复用同一 Job 行(沿用 id,不新建记录);清掉旧运行的 job_inputs 血缘边——
    联合主键 (job_id, dataset_version_id) 下重跑同一输入版本会撞主键,且血缘
    应反映最新一次运行的输入。旧产物版本保留(produced_by_job_id 不动)。
    四场景(clean/distillation/synthesis/augmentation)的更新端点共用。
    """
    job.name = body.name  # type: ignore[attr-defined]
    job.spec = body.model_dump(mode="json")
    job.pipeline_id = getattr(body, "pipeline_id", None)
    job.state = "pending"
    job.progress = 0
    job.error = None
    job.started_at = None
    job.finished_at = None
    # worker 模式下 claim_job 只认领 attempts < max_attempts 的 pending 任务:
    # 若该任务此前已因心跳超时被反复重排耗尽 attempts 才失败,这里不清零就
    # 会让复位后的 pending 任务永远无人认领,静默卡死(无回收机制兜底)。
    # claimed_by/heartbeat_at/queued_at 一并清空,避免残留上一轮认领信息。
    job.attempts = 0
    job.claimed_by = None
    job.heartbeat_at = None
    job.queued_at = None
    await session.execute(delete(JobInput).where(JobInput.job_id == job.id))


async def _start_job(
    session: AsyncSession,
    body: JobCreate,
    job: Job | None = None,
    user: User | None = None,
) -> JSONResponse:
    """校验 → 建任务(pending,存 spec 以备重跑)→ 起后台任务执行 → 立即返回(不等跑完)。

    实际执行在 job_runner 后台进行(状态机 pending→running→success/failed/cancelled),
    任务可经 POST /jobs/{id}/stop 停止;create_job 与 rerun_job 共用此入口
    (rerun 用原任务存下的 spec 重建 body)。传入 job = 编辑任务:校验通过后
    覆盖该任务的配置并原地重跑,不新建记录。
    """
    # 校验算子配置（二选一）
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
            content={"success": False, "message": "请指定 memberConfigs 或 operators"}
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

    # 校验 member_configs（如果提供）
    if body.member_configs:
        if input_version.format == MANIFEST_FORMAT:
            # manifest 媒体集不落 dataset_version_tables,天然只有一个合成成员
            # (MANIFEST_MEMBER_NAME,见 datasets._attach_tables),整版本一套算子。
            if len(body.member_configs) != 1:
                return JSONResponse(
                    status_code=400,
                    content={
                        "success": False,
                        "message": "媒体(manifest)数据集仅支持单一成员的算子配置",
                    },
                )
            member_names = {MANIFEST_MEMBER_NAME}
        else:
            from app.models import DatasetVersionTable

            stmt = select(DatasetVersionTable).where(
                DatasetVersionTable.dataset_version_id == body.dataset_version_id
            )
            members = (await session.execute(stmt)).scalars().all()
            member_names = {m.table_name for m in members}

        for cfg in body.member_configs:
            if cfg.member_name not in member_names:
                return JSONResponse(
                    status_code=400,
                    content={
                        "success": False,
                        "message": f"成员不存在: {cfg.member_name}",
                    },
                )

            if not cfg.operators:
                return JSONResponse(
                    status_code=400,
                    content={
                        "success": False,
                        "message": f"成员 {cfg.member_name} 未指定算子",
                    },
                )

            # 校验算子存在性
            known = oc.operator_names()
            unknown = [o.name for o in cfg.operators if o.name not in known]
            if unknown:
                return JSONResponse(
                    status_code=400,
                    content={
                        "success": False,
                        "message": (
                            f"成员 {cfg.member_name} 包含未知算子: {', '.join(unknown)}"
                        ),
                    },
                )

    # 校验 operators（旧版统一配置模式）
    if body.operators:
        known = oc.operator_names()
        unknown = [o.name for o in body.operators if o.name not in known]
        if unknown:
            return JSONResponse(
                status_code=400,
                content={"success": False, "message": f"未知算子:{', '.join(unknown)}"},
            )

    if (blocked_resp := _binary_block(input_version)) is not None:
        return blocked_resp
    if (blocked_resp := await _multimodal_block(input_version)) is not None:
        return blocked_resp

    # 校验 target_members 有效性
    if body.target_members and input_version.format != MANIFEST_FORMAT:
        from app.models import DatasetVersionTable

        stmt = select(DatasetVersionTable).where(
            DatasetVersionTable.dataset_version_id == body.dataset_version_id
        )
        members = (await session.execute(stmt)).scalars().all()

        if members:  # 版本有成员表记录
            member_names = {m.table_name for m in members}
            unknown = set(body.target_members) - member_names
            if unknown:
                return JSONResponse(
                    status_code=400,
                    content={
                        "success": False,
                        "message": f"成员不存在: {', '.join(sorted(unknown))}"
                    }
                )

    # 资源前置校验:按 LLM 配置 + 数据类型把算子路由到合适后端,跑不了的提前拦截给原因。
    # media_ok:输入是 manifest 媒体集(_multimodal_block 已确保此时 torch 就绪)。
    # 收集所有算子（member_configs 优先，否则用 operators）
    all_operators: list[OperatorSpec] = []
    if body.member_configs:
        for cfg in body.member_configs:
            all_operators.extend(cfg.operators)
    elif body.operators:
        all_operators = body.operators

    if all_operators and (
        blocked_resp := _operator_block(all_operators, input_version)
    ) is not None:
        return blocked_resp

    # 跨供应商模型拦截:算子选了非生效供应商的模型 → 运行期必挂,提前 400
    if all_operators and (
        blocked_resp := await _cross_provider_model_block(session, all_operators)
    ) is not None:
        return blocked_resp

    # G6:请求 Ray 分布式但环境未就绪(未开启 / DJ venv 未装 ray)→ 提前 400,
    # 不让任务跑起来才在运行期失败(诚实失败,Rule 12)。
    if getattr(body, "use_ray", False) and not get_capabilities().ray:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": "当前环境未就绪 Ray 分布式(需 RAY_ENABLED + DJ venv 装 ray)",
            },
        )

    if job is None:
        job = Job(
            id=_new_job_id(),
            name=body.name,
            type=body.type,
            state="pending",
            progress=0,
            created_by=user.username if user else "admin",
            # 存原始执行规格(算子 + 输出去向 + 输入版本),供 rerun 原样重跑
            spec=body.model_dump(mode="json"),
            pipeline_id=body.pipeline_id,
        )
        session.add(job)
    else:
        await _reset_for_edit_rerun(session, job, body)
    await session.commit()
    await session.refresh(job)
    # 交后台执行:产物去向(含另存新数据集的构建)由 job_runner 在加工时处理
    # spawn 只传 job_id——入参 body 由 _run_job 从 job.spec 重建,统一新建/重跑/继续
    job_runner.spawn(job.id)
    return JSONResponse(content=_item(job))


@router.post("/jobs")
async def create_job(
    body: JobCreate,
    session: SessionDep,
    user: Annotated[User, Depends(require_user)],
) -> JSONResponse:
    """新建加工任务并后台执行:对一个数据集版本跑算子流水线 → 产出新版本。

    立即返回 pending 任务(不阻塞到跑完);进度经轮询 GET 反映,可经 stop 端点停止。
    需登录 + 输入数据集 ACL ≥ edit(超管/owner 隐式满足)。
    """
    return await _start_job(session, body, user=user)


@router.put("/jobs/{job_id}")
async def update_job(
    job_id: str,
    body: JobCreate,
    session: SessionDep,
    user: Annotated[User, Depends(require_user)],
) -> JSONResponse:
    """编辑任务:覆盖原任务配置并原地重跑(沿用任务 id,不新建记录)。

    仅终态/已暂停任务可编辑;运行中/排队中 → 409(先停止)。type 以库中任务为准,
    不允许经编辑改变任务类型。质量评估等其他类型不走本端点 → 404。
    """
    job = await session.get(Job, job_id)
    if job is None or job.type not in ("process", "clean"):
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "任务不存在"},
        )
    if job.state in ("pending", "running"):
        return JSONResponse(
            status_code=409,
            content={"success": False, "message": "任务运行中,请先停止再编辑"},
        )
    body.type = job.type
    return await _start_job(session, body, job=job, user=user)


@router.post("/jobs/{job_id}/rerun")
async def rerun_job(
    job_id: str,
    session: SessionDep,
    user: Annotated[User, Depends(require_user)],
) -> JSONResponse:
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
    if job.type == "quality":
        # 质量评估任务的 spec 是 QualityJobCreate,不能按 JobCreate 重建——否则
        # 重跑会被错误创建成 process(治理)任务,对输入版本跑过滤产新版本。
        # 函数级 import:quality.py 模块头 import 本模块,顶层互引会循环。
        from app.api.v1.quality import QualityJobCreate, create_quality_job

        try:
            qspec = QualityJobCreate.model_validate(job.spec)
        except ValidationError:
            return JSONResponse(
                status_code=400,
                content={"success": False, "message": "任务配置已损坏,无法重跑"},
            )
        # 复用创建入口:重新走成员存在性 / 算子合法性 / ACL 全套校验
        return await create_quality_job(body=qspec, session=session, user=user)
    try:
        spec = JobCreate.model_validate(job.spec)
    except ValidationError:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "任务配置已损坏,无法重跑"},
        )
    return await _start_job(session, spec, user=user)


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
    # CAS:上面的 get() 到这里之间,后台可能已把任务收敛为终态(如 success/failed)——
    # 无条件写 state="cancelled" 会覆盖真实产出信息(TOCTOU)。改用条件 UPDATE,
    # 只有仍处于可停止状态时才落地;rowcount=0 说明已被后台抢先收尾,回 409 而不
    # 覆盖它的真实终态。
    result = await session.execute(
        update(Job)
        .where(Job.id == job_id, Job.state.in_(("pending", "running", "paused")))
        .values(state="cancelled", finished_at=_now())
    )
    await session.commit()
    if result.rowcount == 0:
        return JSONResponse(
            status_code=409,
            content={"success": False, "message": "任务已结束,无需停止"},
        )
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
    # CAS(同 stop_job):防上面 get() 到此刻之间任务被后台收敛为终态时无条件覆写。
    result = await session.execute(
        update(Job)
        .where(Job.id == job_id, Job.state.in_(("pending", "running")))
        .values(state="paused", finished_at=_now())
    )
    await session.commit()
    if result.rowcount == 0:
        return JSONResponse(
            status_code=409,
            content={"success": False, "message": "任务不在运行中,无法暂停"},
        )
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
    # 同 _reset_for_edit_rerun:清零 attempts,否则此前因心跳超时被反复重排
    # 耗尽 attempts 才失败的任务,复位后会因 attempts>=max_attempts 永远无
    # worker 认领(worker 模式下 API 进程不再跑 reconcile_orphans 兜底)。
    job.attempts = 0
    job.claimed_by = None
    job.heartbeat_at = None
    job.queued_at = None
    await session.commit()
    await session.refresh(job)
    job_runner.spawn(job.id)
    return JSONResponse(content=_item(job))


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, session: SessionDep) -> JSONResponse:
    """加工任务详情(含产物版本与输入版本)。级联删除标记的任务同 404。"""
    job = await session.get(Job, job_id)
    if job is None or job.deleted_at is not None:
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
    运行中/排队中的任务不可删(请先停止)→ 409;未知任务 → 404。
    """
    job = await session.get(Job, job_id)
    if job is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "任务不存在"},
        )
    # pending 也一并拒删(不止 running):后台协程可能在本次 get() 之后、级联删除
    # 执行之前把这个排队中的任务捞起来跑(pending→running),届时 job_runner 仍持有
    # 该 Job 行的引用去更新状态/写 job_inputs,而行已被删 → FK/UPDATE 撞空。终态
    # 任务不存在此竞态(job_runner 不会再碰它),故只需堵 pending/running 两态。
    if job.state in ("running", "pending"):
        return JSONResponse(
            status_code=409,
            content={"success": False, "message": "任务运行中或排队中,请先停止再删除"},
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

    删除语义同单条 delete;运行中/排队中或不存在的任务自动跳过(不阻断整批)。
    """
    deleted = 0
    for job_id in body.ids:
        job = await session.get(Job, job_id)
        if job is None or job.state in ("running", "pending"):
            continue
        await _delete_job_cascade(session, job)
        deleted += 1
    await session.commit()
    return JSONResponse(content={"data": {"deleted": deleted}, "success": True})
