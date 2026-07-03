"""采集任务路由：/ingest-tasks 系列端点与进度状态机。

状态机要点（rerun 同步执行，返回即终态；不再凭 GET 轮询伪造进度/成功）：
- create        → status=pending、progress=0、logs=["[INFO] 任务已创建"]，
                  datasource_name 从数据源表冗余（数据源不存在 → 404）。
                  若 schedule.mode=cron,成功后 best-effort upsert 调度作业。
- GET detail    → 只读：如实返回当前状态/进度，附产物列表（不修改任何字段）。
- rerun         → PG+采集对象：真实拉取，success/failed；
                  PG 未配采集对象 / 非 PG 源：如实 failed（不产出数据集）。
- stop          → 转 failed、追加"[WARN] 任务被手动停止"。
- update        → schedule.mode 变更:改 cron → upsert;改 once → remove。
- delete        → 删除前先 remove 调度作业(best-effort),再删记录。

调度器接线遵循「采集主流程不依赖调度器在线」:scheduler 未启用 / 未启动 /
upsert/remove 抛错均 try/except + log,不阻断 HTTP 请求。

单对象响应统一 {data:{...}, success:true}；未命中 404 + {success:false, message:str}。
"""

from __future__ import annotations

import logging
import secrets
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response, status
from fastapi.responses import JSONResponse
from pydantic import Field
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import current_user, require_perm
from app.api.v1.categories import build_category_name_map
from app.core.config import settings
from app.core.db import get_session
from app.models.data_lake import DataLake
from app.models.dataset import Dataset
from app.models.dataset_version import DatasetVersion
from app.models.datasource import DataSource
from app.models.ingest_task import IngestTask
from app.models.job import Job
from app.models.user import User
from app.schemas.common import CamelModel, PageResponse, format_version_label
from app.schemas.ingest_task import (
    IngestExtract,
    IngestRunRead,
    IngestTaskCreate,
    IngestTaskRead,
    IngestTaskUpdate,
)
from app.services import operator_catalog as oc
from app.services import scheduler as scheduler_mod
from app.services.connectors import resolve
from app.services.connectors.base import ConnectorNotReady, IngestError
from app.services.external_store import (
    ExternalStoreError,
    upload_jsonl_to_uploads,
)
from app.services.landing import (
    _new_dataset_id,
    _new_version_id,
    records_to_jsonl_bytes,
)
from app.services.llm_config import get_active_llm_config

_logger = logging.getLogger(__name__)

# 可直连拉取记录的数据库品牌:PG 族走 asyncpg,MySQL 族(goldendb/gaussdb_mysql)走 asyncmy
_PG_KINDS = {"postgresql", "hologres", "kingbase", "gaussdb"}
_MYSQL_KINDS = {"goldendb", "gaussdb_mysql"}
_CSV_DATASET_KINDS = _PG_KINDS | _MYSQL_KINDS


async def _fetch_db_records(datasource: DataSource, task: IngestTask) -> list[dict]:
    """按数据库品牌派发,拉取记录(不落地)。仅 PG 族 / MySQL 族支持。"""
    db_kind = (datasource.db_kind or "").lower()
    if db_kind in _MYSQL_KINDS:
        from app.services.connectors.mysql import fetch_records
    else:
        from app.services.connectors.pg import fetch_records
    return await fetch_records(datasource, task)

router = APIRouter(tags=["ingest-tasks"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _invalid_operator_reason(extract: IngestExtract | None) -> str | None:
    """校验 extract.operators 在当前环境均可运行;返回首个不可用原因,全可用→None。

    采集落地为结构化记录(非媒体),media 类算子也拒绝(默认 media_ok=False)。
    """
    if extract is None or not extract.operators:
        return None
    llm_configured = bool(get_active_llm_config().api_key)
    for step in extract.operators:
        reason = oc.runnable_reason(step.name, llm_configured=llm_configured)
        if reason:
            return reason
    return None

PROGRESS_DONE = 100


class IngestTaskItemResponse(CamelModel):
    """单个采集任务响应：{data:{...}, success:true}。"""

    data: IngestTaskRead
    success: bool = True


def _new_task_id() -> str:
    """生成 "task-" + 6 位 hex 主键。"""
    return f"task-{secrets.token_hex(3)}"


def _new_job_id() -> str:
    """生成 "job-" + 6 位 hex 主键(每次运行一条 type=ingest 的 job)。"""
    return f"job-{secrets.token_hex(3)}"


def _now() -> datetime:
    """当前 UTC 时间（naive，与 last_run_at 列 TIMESTAMP WITHOUT TIME ZONE 对齐）。"""
    return datetime.now(UTC).replace(tzinfo=None)


def _not_found() -> JSONResponse:
    """统一 404：{success:false, message:str}。"""
    return JSONResponse(
        status_code=status.HTTP_404_NOT_FOUND,
        content={"success": False, "message": "任务不存在"},
    )


def _sync_cron_job(task: IngestTask) -> None:
    """best-effort upsert 调度作业(切片 C / Task 3)。

    仅当 ``settings.scheduler_enabled`` 且 ``scheduler_mod.get_scheduler()``
    返回实例时才尝试 upsert;任何异常(调度器未启动 / jobstore 不可达 /
    cron 表达式异常)均 try/except + log,不抛给调用方——采集主流程不依赖
    调度器在线(用户可手工 rerun)。
    """
    if not settings.scheduler_enabled:
        return
    # 仅 cron 模式任务建调度作业;once 任务即便携带历史 cron 字段也不调度
    schedule = getattr(task, "schedule", None)
    if not isinstance(schedule, dict) or schedule.get("mode") != "cron":
        return
    scheduler = scheduler_mod.get_scheduler()
    if scheduler is None:
        return
    try:
        scheduler_mod.upsert_cron_job(scheduler, task)
    except Exception:  # noqa: BLE001
        _logger.warning(
            "调度作业 upsert 失败(已忽略,采集不依赖调度器) task_id=%s",
            getattr(task, "id", "?"),
            exc_info=True,
        )


def _unsync_cron_job(task_id: str) -> None:
    """best-effort remove 调度作业(切片 C / Task 3)。

    与 ``_sync_cron_job`` 对称:``scheduler_enabled=False`` / scheduler 未启动 /
    remove 抛错均 try/except + log,绝不阻断 delete/update 主流程。
    """
    if not settings.scheduler_enabled:
        return
    scheduler = scheduler_mod.get_scheduler()
    if scheduler is None:
        return
    try:
        scheduler_mod.remove_cron_job(scheduler, task_id)
    except Exception:  # noqa: BLE001
        _logger.warning(
            "调度作业 remove 失败(已忽略) task_id=%s", task_id, exc_info=True
        )


def _item(
    task: IngestTask,
    output: list[dict] | None = None,
    category_name: str | None = None,
    lake_name: str | None = None,
) -> dict:
    """把 ORM 任务序列化为 camelCase 单对象响应体（output 仅详情接口填充）。"""
    read = IngestTaskRead.model_validate(task)
    if output:
        read.output = output
    if category_name is not None:
        read.category_name = category_name
    if lake_name is not None:
        read.lake_name = lake_name
    return IngestTaskItemResponse(data=read).model_dump(by_alias=True, mode="json")


async def _category_name(session: AsyncSession, task: IngestTask) -> str | None:
    """取任务分类名(分类可空时返回 None)。"""
    if not task.category_id:
        return None
    names = await build_category_name_map(session, [task.category_id])
    return names.get(task.category_id)


async def _lake_name(session: AsyncSession, task: IngestTask) -> str | None:
    """取任务目标湖名(无湖/湖已删返回 None)。

    详情接口也须回填:列表对运行中任务会用 GET 单任务响应整行替换,
    缺湖名会让「数据湖」列在运行期间闪回 "-"。
    """
    if not task.lake_id:
        return None
    return await session.scalar(
        select(DataLake.name).where(DataLake.id == task.lake_id)
    )


async def _build_output(session: AsyncSession, task_id: str) -> list[dict]:
    """查该任务的全部产物数据集(经各次运行 job 的 produced_by_job_id 反查)。"""
    stmt = (
        select(DatasetVersion, Dataset)
        .join(Dataset, Dataset.id == DatasetVersion.dataset_id)
        .join(Job, Job.id == DatasetVersion.produced_by_job_id)
        .where(Job.ingest_task_id == task_id)
        .order_by(DatasetVersion.created_at)
    )
    rows = (await session.execute(stmt)).all()
    return [
        {
            "datasetId": dataset.id,
            "datasetName": dataset.name,
            "versionId": version.id,
            "versionNo": version.version_no,
            "versionLabel": format_version_label(
                version.version_no, version.created_at
            ),
            "rows": version.rows,
            # 切片 B / B6:透传版本级质量字段,前端详情 Drawer 据此渲染结论 + 列空值率
            # + 表结构快照。键名 camelCase 对齐前端 IngestOutput typings;缺省值由
            # 模型 server_default / nullable 兜底(verdict=skipped、stats/snapshot 可空)。
            "qualityVerdict": version.quality_verdict,
            "qualityStats": version.quality_stats,
            "schemaSnapshot": version.schema_snapshot,
        }
        for version, dataset in rows
    ]


@router.get(
    "/ingest-tasks",
    response_model=PageResponse[IngestTaskRead],
    dependencies=[Depends(require_perm("ingest:task:list"))],
)
async def list_ingest_tasks(
    session: SessionDep,
    current: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, alias="pageSize")] = 10,
    name: Annotated[str | None, Query()] = None,
    status_: Annotated[str | None, Query(alias="status")] = None,
    category_id: Annotated[str | None, Query(alias="categoryId")] = None,
) -> PageResponse[IngestTaskRead]:
    """分页列出采集任务，支持 name 模糊、status 精确、categoryId 精确筛选。"""
    stmt = select(IngestTask)
    count_stmt = select(func.count()).select_from(IngestTask)
    if name:
        stmt = stmt.where(IngestTask.name.ilike(f"%{name}%"))
        count_stmt = count_stmt.where(IngestTask.name.ilike(f"%{name}%"))
    if status_:
        stmt = stmt.where(IngestTask.status == status_)
        count_stmt = count_stmt.where(IngestTask.status == status_)
    if category_id:
        stmt = stmt.where(IngestTask.category_id == category_id)
        count_stmt = count_stmt.where(IngestTask.category_id == category_id)

    total = await session.scalar(count_stmt) or 0
    offset = (current - 1) * page_size
    stmt = stmt.order_by(IngestTask.created_at.desc()).offset(offset).limit(page_size)
    rows = (await session.scalars(stmt)).all()
    # 批量取分类名(避免 N+1),回填 categoryName
    cat_names = await build_category_name_map(
        session, [r.category_id for r in rows]
    )
    # 批量取湖名(同分类名模式),回填 lakeName
    lake_ids = {r.lake_id for r in rows if r.lake_id}
    lake_names: dict[str, str] = {}
    if lake_ids:
        lake_rows = await session.execute(
            select(DataLake.id, DataLake.name).where(DataLake.id.in_(lake_ids))
        )
        lake_names = dict(lake_rows.all())
    data = []
    for r in rows:
        item = IngestTaskRead.model_validate(r)
        if r.category_id:
            item.category_name = cat_names.get(r.category_id)
        if r.lake_id:
            item.lake_name = lake_names.get(r.lake_id)
        data.append(item)
    return PageResponse[IngestTaskRead](data=data, total=total)


# ---------------------------------------------------------------------------
# 页顶概览 dashboard 统计(/ingest-tasks/stats)
# 镜像 data-tasks/stats 的形态:状态/数据源类型分布 + 近 24h 完成 + 平均时长 + 14 天趋势。
# - by_state 来自 IngestTask.status(任务级「当前态」)
# - by_ds_type_state 来自 IngestTask JOIN DataSource(数据源类型 × 状态,堆叠柱)
# - completed_last24h / avg_duration_sec / trend14d 来自 Job.type='ingest'(运行级)
# ---------------------------------------------------------------------------

# 采集任务生命周期阶段(与 IngestTask.status 对齐;比数据任务少 paused/cancelled)
_INGEST_STATES: tuple[str, ...] = ("pending", "running", "success", "failed")
# 采集终态(success/failed 都是「完成」)
_INGEST_TERMINAL_STATES: tuple[str, ...] = ("success", "failed")
# 趋势按北京日,与 data-tasks 对齐
_BEIJING_OFFSET = timedelta(hours=8)
_TREND_DAYS = 14


class DsTypeStateCount(CamelModel):
    """某数据源类型 × 某状态的采集任务数(仅非零组合)。"""

    ds_type: str = Field(serialization_alias="dsType")
    state: str
    count: int


class IngestTrendPoint(CamelModel):
    """近 14 天(北京日)采集运行趋势:创建数 + 当日成功的成功/失败数。"""

    date: str
    created: int
    success: int
    failed: int


class IngestTaskStats(CamelModel):
    """采集任务概览统计,驱动 ingest/tasks 页顶 dashboard。

    - byState: 各状态计数(4 种全量,无则 0);成功率由前端派生。
    - byDsTypeState: 数据源类型 × 状态计数(仅非零组合),供堆叠柱状图。
    - completedLast24h: 近 24h 进入终态的运行数(Job.type=ingest)。
    - avgDurationSec: 成功运行的平均处理时长(秒);无样本为 None。
    - trend14d: 近 14 天(北京日)创建 / 完成趋势,升序。
    """

    total: int
    by_state: dict[str, int]
    by_ds_type_state: list[DsTypeStateCount] = Field(
        serialization_alias="byDsTypeState"
    )
    completed_last24h: int = Field(serialization_alias="completedLast24h")
    avg_duration_sec: float | None
    trend14d: list[IngestTrendPoint] = Field(serialization_alias="trend14d")
    success: bool = True


def _coerce_date(value: object) -> date:
    """把 DB 返回的日期(asyncpg 通常给 date,亦兼容 datetime / ISO 字符串)规整为 date。"""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


async def _build_ingest_trend14d(session: SessionDep, now_utc: datetime) -> list[IngestTrendPoint]:
    """近 14 天(北京日)采集运行趋势:Job.type='ingest' 的创建数 + 当日完成的成功/失败数。

    库内时间戳为 naive UTC,按 ``ts + interval '8 hours'`` 取北京日分桶,
    与列表 / UI 的北京时区展示对齐(否则傍晚的运行会被算到错误的日期)。
    """
    bj_today = (now_utc + _BEIJING_OFFSET).date()
    days = [bj_today - timedelta(days=i) for i in range(_TREND_DAYS - 1, -1, -1)]
    # 窗口起点(UTC):最早北京日 00:00 - 8h
    since_utc = datetime.combine(days[0], datetime.min.time()) - _BEIJING_OFFSET

    bj_created = func.date(Job.created_at + text("interval '8 hours'"))
    created_rows = (
        await session.execute(
            select(bj_created.label("d"), func.count())
            .where(Job.type == "ingest", Job.created_at >= since_utc)
            .group_by(bj_created)
        )
    ).all()
    created_map = {_coerce_date(d): n for d, n in created_rows}

    bj_finished = func.date(Job.finished_at + text("interval '8 hours'"))
    fin_rows = (
        await session.execute(
            select(bj_finished.label("d"), Job.state, func.count())
            .where(
                Job.type == "ingest",
                Job.state.in_(("success", "failed")),
                Job.finished_at >= since_utc,
            )
            .group_by(bj_finished, Job.state)
        )
    ).all()
    succ_map: dict[date, int] = {}
    fail_map: dict[date, int] = {}
    for d, st, n in fin_rows:
        (succ_map if st == "success" else fail_map)[_coerce_date(d)] = n

    return [
        IngestTrendPoint(
            date=day.isoformat(),
            created=created_map.get(day, 0),
            success=succ_map.get(day, 0),
            failed=fail_map.get(day, 0),
        )
        for day in days
    ]


@router.get(
    "/ingest-tasks/stats",
    response_model=IngestTaskStats,
    dependencies=[Depends(require_perm("ingest:task:list"))],
)
async def ingest_tasks_stats(session: SessionDep) -> IngestTaskStats:
    """采集任务概览:状态 / 数据源类型分布 + 近 24h 完成 + 平均时长 + 14 天趋势。

    范围与列表一致——所有采集任务。均为廉价的 GROUP BY 聚合;随列表一同刷新
    (含运行时 5s 轮询)。若 jobs 表显著增大,建议给 created_at / finished_at 加索引。
    """
    now_utc = datetime.now(UTC).replace(tzinfo=None)  # naive UTC,与库内列对齐

    # 1) 任务级:总任务 + 状态分桶
    state_rows = (
        await session.execute(
            select(IngestTask.status, func.count()).group_by(IngestTask.status)
        )
    ).all()
    by_state = dict.fromkeys(_INGEST_STATES, 0)
    total = 0
    for st, count in state_rows:
        total += count
        if st in by_state:
            by_state[st] = count

    # 2) 任务级:数据源类型 × 状态(JOIN datasources 表)
    ds_rows = (
        await session.execute(
            select(DataSource.type, IngestTask.status, func.count())
            .join(IngestTask, IngestTask.datasource_id == DataSource.id)
            .group_by(DataSource.type, IngestTask.status)
        )
    ).all()
    by_ds_type_state = [
        DsTypeStateCount(ds_type=t, state=s, count=n) for t, s, n in ds_rows
    ]

    # 3) 运行级:近 24h 完成 + 平均时长 + 14 天趋势(全从 Job.type='ingest' 算)
    completed_last24h = (
        await session.scalar(
            select(func.count()).where(
                Job.type == "ingest",
                Job.state.in_(_INGEST_TERMINAL_STATES),
                Job.finished_at >= now_utc - timedelta(hours=24),
            )
        )
        or 0
    )

    avg_raw = await session.scalar(
        select(
            func.avg(func.extract("epoch", Job.finished_at - Job.started_at))
        ).where(
            Job.type == "ingest",
            Job.state == "success",
            Job.started_at.is_not(None),
            Job.finished_at.is_not(None),
        )
    )
    avg_duration_sec = float(avg_raw) if avg_raw is not None else None

    trend14d = await _build_ingest_trend14d(session, now_utc)

    return IngestTaskStats(
        total=total,
        by_state=by_state,
        by_ds_type_state=by_ds_type_state,
        completed_last24h=completed_last24h,
        avg_duration_sec=avg_duration_sec,
        trend14d=trend14d,
    )


@router.post("/ingest-tasks")
async def create_ingest_task(
    payload: IngestTaskCreate,
    session: SessionDep,
    user: Annotated[User | None, Depends(current_user)] = None,
) -> Response:
    """新建采集任务：校验数据源 + 目标数据湖存在，冗余数据源名，初始 pending/0。"""
    datasource = await session.get(DataSource, payload.datasource_id)
    if datasource is None:
        return _not_found()

    # 治理改造:采集任务绑定数据湖,采集结果入湖归档(数据集经「湖抽取」单独产生)
    from app.services.data_lake import get_lake_by_id  # noqa: PLC0415

    lake = await get_lake_by_id(session, payload.lake_id)
    if lake is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "目标数据湖不存在"},
        )

    if reason := _invalid_operator_reason(payload.extract):
        return JSONResponse(
            status_code=400, content={"success": False, "message": reason}
        )

    task = IngestTask(
        id=_new_task_id(),
        name=payload.name,
        datasource_id=payload.datasource_id,
        datasource_name=datasource.name,
        lake_id=payload.lake_id,
        schedule=payload.schedule.model_dump(),
        extract=payload.extract.model_dump() if payload.extract else None,
        status="pending",
        progress=0,
        logs=["[INFO] 任务已创建"],
        category_id=payload.category_id,
        # 切片 B:quality_policy 以 snake_case dict 存(JSONB),
        # 与 evaluate_policy 期望的键名一致(max_null_rate / block_on_schema_drift)。
        quality_policy=(
            payload.quality_policy.model_dump()
            if payload.quality_policy
            else None
        ),
    )
    session.add(task)
    await session.commit()
    await session.refresh(task)
    # 切片 C / Task 3:cron 任务 best-effort upsert 调度作业(scheduler 未启用
    # / 未启动 / upsert 抛错均静默跳过,采集主流程不依赖调度器在线)
    _sync_cron_job(task)
    return JSONResponse(
        content=_item(task, category_name=await _category_name(session, task))
    )


@router.put("/ingest-tasks/{task_id}")
async def update_ingest_task(
    task_id: str,
    body: IngestTaskUpdate,
    session: SessionDep,
) -> Response:
    """编辑采集任务:仅更新显式传入的字段(名称/数据源/调度/采集对象)。"""
    task = await session.get(IngestTask, task_id)
    if task is None:
        return _not_found()

    if body.extract is not None and (
        reason := _invalid_operator_reason(body.extract)
    ):
        return JSONResponse(
            status_code=400, content={"success": False, "message": reason}
        )

    if body.name is not None:
        task.name = body.name
    # 切片 C / Task 3:schedule 变更时记录新旧 mode,用于 commit 后 best-effort
    # 同步调度作业(cron→upsert;once→remove)。读取在赋值前,避免覆盖判断。
    prev_mode: str | None = None
    new_mode: str | None = None
    if body.schedule is not None:
        prev_schedule = (
            task.schedule if isinstance(task.schedule, dict) else {}
        )
        prev_mode = prev_schedule.get("mode")
        task.schedule = body.schedule.model_dump()
        new_mode = body.schedule.mode
    if body.extract is not None:
        task.extract = body.extract.model_dump()
    if body.datasource_id is not None and body.datasource_id != task.datasource_id:
        datasource = await session.get(DataSource, body.datasource_id)
        if datasource is None:
            return JSONResponse(
                status_code=404,
                content={"success": False, "message": "数据源不存在"},
            )
        task.datasource_id = body.datasource_id
        task.datasource_name = datasource.name
    if body.lake_id is not None and body.lake_id != task.lake_id:
        from app.services.data_lake import get_lake_by_id  # noqa: PLC0415

        if await get_lake_by_id(session, body.lake_id) is None:
            return JSONResponse(
                status_code=404,
                content={"success": False, "message": "目标数据湖不存在"},
            )
        task.lake_id = body.lake_id
    if body.category_id is not None:
        task.category_id = body.category_id
    # 切片 B:quality_policy 仅在显式传入时更新(None/缺省 = 不变,与现有字段一致)。
    # 以 snake_case dict 存(JSONB),供 rerun 时 evaluate_policy 直接消费。
    if body.quality_policy is not None:
        task.quality_policy = body.quality_policy.model_dump()

    await session.commit()
    await session.refresh(task)
    # 切片 C / Task 3:best-effort 同步调度作业——
    # - schedule 改为 cron:upsert(覆盖旧作业)
    # - schedule 改为 once(从 cron 切回):remove 旧 cron 作业
    # - schedule 未变 / 改 once→once / 改 cron→cron 同表达式:仍按上面规则幂等执行
    #   (upsert_cron_job 是 replace_existing=True;once 模式 remove 容忍 JobLookupError)
    # scheduler 未启用 / 未启动 / 抛错均静默跳过,采集主流程不依赖调度器在线。
    if new_mode == "cron":
        _sync_cron_job(task)
    elif new_mode == "once" and prev_mode == "cron":
        _unsync_cron_job(task.id)
    return JSONResponse(
        content=_item(task, category_name=await _category_name(session, task))
    )


@router.get("/ingest-tasks/{task_id}")
async def get_ingest_task(
    task_id: str,
    session: SessionDep,
) -> Response:
    """获取任务详情。状态如实反映运行结果(rerun 为同步执行,返回即终态),
    不再凭轮询伪造进度/成功——避免「任务成功却没有产出数据集」的假象。"""
    task = await session.get(IngestTask, task_id)
    if task is None:
        return _not_found()

    output = await _build_output(session, task.id)
    return JSONResponse(
        content=_item(
            task,
            output,
            category_name=await _category_name(session, task),
            lake_name=await _lake_name(session, task),
        )
    )


async def _execute_ingest(
    session: AsyncSession,
    task: IngestTask,
    datasource: DataSource,
    *,
    trigger: Literal["manual", "cron"],
) -> list[tuple[Dataset, DatasetVersion]]:
    """采集执行共享核(rerun + scheduler 触发共用)。

    签名固定为 ``(session, task, datasource, *, trigger)``——前三个位置参数是
    请求/调度两条路径都已查到的对象;``trigger`` 标签透传到新建的 Job(``manual``
    或 ``cron``),供运维区分触发来源。 ``datasource`` 已由调用方校验非 None,
    且其类型/采集对象已通过早退 guard(无数据源 / api 推送 / 未配 extract 等),
    本函数**只负责执行路径**:建 Job → 连接器拉取 → 应用任务级 quality_policy
    → 推进 task/job 状态机。

    - success:任务 quality_policy 全过(或无策略=skipped)→ task.status=success、
      job.state=success、返回 results 列表。
    - quality_fail:任一版本 quality_verdict=failed → task.status=failed、
      job.state=failed,reason 进 task.logs + job.error,仍返回 results(版本落地)。
    - ingest_error:连接器抛 IngestError/ConnectorNotReady → task/job 均 failed,
      返回空 results(未产出)。

    不 commit(调用方负责:rerun 在路由层 commit,scheduler 在 _trigger_ingest 内
    commit),但内部会 commit 一次——为了 connector.run_ingest 在其内部已 commit
    产出的 Dataset/Version(原 rerun 逻辑保留,行为零变化)。
    """
    # --- 正常路径:建 job → 连接器真实拉取 ---
    task.status = "running"
    started = task.last_run_at or _now()
    task.logs = [*task.logs, f"[INFO] 开始采集({datasource.name} 真实拉取)"]
    # 每次运行一条 type=ingest 的 job(收编后 ingest_runs 的替代)
    # trigger 标签(切片 C):manual=rerun 手工触发、cron=调度器定时触发
    job = Job(
        id=_new_job_id(),
        name=task.name,
        type="ingest",
        ingest_task_id=task.id,
        state="running",
        progress=0,
        created_by="admin",
        started_at=started,
        trigger=trigger,
    )
    session.add(job)
    await session.commit()
    results: list[tuple[Dataset, DatasetVersion]] = []
    try:
        connector = resolve(datasource.type, datasource.db_kind)
        if connector is None:
            # 理论不可达(调用方已 guard),防御性诚实 failed
            raise ConnectorNotReady(
                f"数据源类型 {datasource.type}/{datasource.db_kind} 无可用连接器"
            )
        results = await connector.run_ingest(
            session, task, datasource, job_id=job.id
        )
        total_rows = sum(v.rows or 0 for _, v in results)

        # 切片 B / Task 4:对每个落地版本应用任务级 quality_policy(空值率阈值
        # 阻断发布门)。drift=None:_execute_ingest 总是经 land_records 新建首版,
        # 无历史 schema 快照可比;schema 漂移检查在此处为 N/A。
        from app.services.ingest_quality import evaluate_policy  # noqa: PLC0415

        quality_failures: list[str] = []
        for _ds, ver in results:
            verdict, reason = evaluate_policy(
                task.quality_policy, ver.quality_stats or {}, drift=None
            )
            ver.quality_verdict = verdict
            if verdict == "failed" and reason:
                quality_failures.append(reason)

        if quality_failures:
            # 任一版本质量门未通过 → 任务/job 标 failed,不进入正常成功路径
            reason_text = "; ".join(quality_failures)
            task.status = "failed"
            task.progress = PROGRESS_DONE
            task.logs = [
                *task.logs,
                f"[ERROR] 质量门未通过:{reason_text}",
            ]
            job.state = "failed"
            job.error = f"质量门未通过:{reason_text}"
        else:
            task.status = "success"
            task.progress = PROGRESS_DONE
            # 入湖任务(task.lake_id):连接器落湖时逐快照写日志,此处只收尾;
            # 存量数据集任务保持原「产出 N 个数据集」摘要。
            if results:
                summary = (
                    f"[INFO] 采集 {len(results)} 项,共 {total_rows} 条 → 产出 "
                    f"{len(results)} 个数据集:"
                    + "、".join(
                        f"{ds.name}({v.rows or 0}行)" for ds, v in results
                    )
                )
            else:
                summary = "[INFO] 采集入湖完成(快照明细见上方日志)"
            task.logs = [*task.logs, summary, "[INFO] 任务完成"]
            job.state = "success"
            job.progress = PROGRESS_DONE
    except (IngestError, ConnectorNotReady) as exc:
        task.status = "failed"
        task.logs = [*task.logs, f"[ERROR] 采集失败:{exc}"]
        job.state = "failed"
        job.error = str(exc)
    # 终态通知:success/failed 各发一条给创建者(task.creator)。此处是采集运行的
    # 唯一终态汇合点(success / 质量门 failed / ingest_error failed 都到这),故恰好
    # 一条;且该 Job(type=ingest)不经 job_runner._run_job,二者不会重复发。
    # 惰性引入 + emit 内部 loud-swallow:通知失败不污染任务终态(随调用方事务提交)。
    from app.services import notifications  # noqa: PLC0415

    notifications.emit(
        session,
        recipient=task.creator,
        level="success" if task.status == "success" else "error",
        source_type="ingest_task",
        source_id=task.id,
        title=(
            f"采集任务 {task.name} 已完成"
            if task.status == "success"
            else f"采集任务 {task.name} 失败"
        ),
        body=job.error if task.status == "failed" else None,
    )
    job.finished_at = _now()
    task.run_count += 1
    return results


@router.post("/ingest-tasks/{task_id}/rerun")
async def rerun_ingest_task(
    task_id: str,
    session: SessionDep,
) -> Response:
    """运行/重跑任务。

    经连接器注册表派发(§4.8):resolve(ds.type, ds.db_kind) →
    - PG 族 / goldendb / S3 + 已配采集对象 → 真实拉取并落地 DatasetVersion(同步);
    - 未配采集对象 / 不支持类型 / 未装驱动 / 无集群 → 诚实 failed(不伪造成功)。
    Job(type=ingest)创建逻辑不变。

    切片 C / Task 4:成功路径的执行逻辑抽到共享核 ``_execute_ingest``(
    供 scheduler._trigger_ingest 复用),rerun 调用时传 ``trigger="manual"``。
    早退 guard(无数据源 / api / 未配 extract)是 HTTP 响应塑形,留在路由层。
    """
    task = await session.get(IngestTask, task_id)
    if task is None:
        return _not_found()

    datasource = await session.get(DataSource, task.datasource_id)
    connector = (
        resolve(datasource.type, datasource.db_kind)
        if datasource is not None
        else None
    )

    task.progress = 0
    task.last_run_at = _now()

    # --- 诚实早退(不创建 job):无数据源 / 不支持类型 / api 推送 / 未配采集对象 ---
    # 分支顺序与抽取前完全一致(零行为变化):
    # 1) datasource 缺失 OR 连接器未注册 → 「不支持自动采集」
    # 2) api 类型 → 「API 推送不走 rerun」
    # 3) 未配采集对象 → 「请先配置」
    # 否则 → 共享核 _execute_ingest(trigger=manual)
    if datasource is None or connector is None:
        kind = "?" if datasource is None else (
            f"{datasource.type}/{datasource.db_kind}"
            if datasource.db_kind
            else datasource.type
        )
        task.status = "failed"
        task.logs = [
            *task.logs,
            f"[ERROR] 数据源类型「{kind}」暂不支持自动采集,未产出任何数据集",
        ]
    elif datasource.type == "api":
        # API 推送数据通过 POST /ingest/push/{token} 端点入站,不走采集任务 rerun
        task.status = "failed"
        task.logs = [
            *task.logs,
            "[ERROR] API 推送数据源不支持采集任务运行;"
            "请由外部系统调用推送地址(config.url)入站",
        ]
    elif not task.extract:
        # 诚实失败:已配连接器但没配采集对象 → 不可能产出数据集,不假装成功
        task.status = "failed"
        task.logs = [
            *task.logs,
            "[ERROR] 未配置采集对象,请先在任务中选择表/对象或填写 SQL 后再运行",
        ]
    else:
        # --- 正常路径:调共享核 _execute_ingest(trigger=manual) ---
        await _execute_ingest(session, task, datasource, trigger="manual")

    await session.commit()
    await session.refresh(task)
    return JSONResponse(
        content=_item(task, category_name=await _category_name(session, task))
    )


@router.post("/ingest-tasks/preview")
async def preview_ingest_source(payload: dict, session: SessionDep) -> Response:
    """源数据预览(切片 A):无副作用采样,不建任务/不落地/不建 job。

    - database(PG族/GoldenDB):SELECT * FROM (查询) LIMIT 50;多表取首张
    - s3/hdfs:首个匹配文件按 jsonl/csv/parquet 解析头部(parquet 走 DuckDB)
    - api / 不支持类型 / 未配采集对象 → 4xx 诚实失败
    """
    datasource_id = payload.get("datasourceId") or payload.get("datasource_id")
    extract = payload.get("extract") or {}
    datasource = await session.get(DataSource, datasource_id or "")
    if datasource is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "数据源不存在"},
        )

    from app.services.preview import preview_db, preview_file  # noqa: PLC0415

    try:
        if datasource.type == "database":
            db_kind = (datasource.db_kind or "").lower()
            if db_kind in _PG_KINDS:
                from app.services.connectors.pg import _connect  # noqa: PLC0415
            elif db_kind in _MYSQL_KINDS:
                from app.services.connectors.mysql import _connect  # noqa: PLC0415
            else:
                return JSONResponse(
                    status_code=400,
                    content={
                        "success": False,
                        "message": f"数据库品牌「{datasource.db_kind}」暂不支持预览",
                    },
                )
            data = await preview_db(_connect, datasource.config or {}, extract)
        elif datasource.type in ("s3", "hdfs"):
            data = await preview_file(datasource, extract)
        else:
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "message": f"数据源类型「{datasource.type}」暂不支持预览",
                },
            )
    except ConnectorNotReady as exc:
        return JSONResponse(
            status_code=400, content={"success": False, "message": str(exc)}
        )
    except (IngestError, ValueError, ExternalStoreError) as exc:
        return JSONResponse(
            status_code=400, content={"success": False, "message": str(exc)}
        )

    return JSONResponse(content={"data": data, "success": True})


@router.get(
    "/ingest-tasks/{task_id}/runs", response_model=PageResponse[IngestRunRead]
)
async def list_ingest_runs(
    task_id: str,
    session: SessionDep,
    current: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, alias="pageSize")] = 20,
) -> PageResponse[IngestRunRead]:
    """某采集任务的运行记录(jobs 表 type=ingest),按开始时间倒序。

    wire 形态与收编前的 ingest_runs 保持一致;rows/outputs 由产物版本
    (produced_by_job_id)反查推导。
    """
    where = Job.ingest_task_id == task_id
    total = (
        await session.scalar(
            select(func.count()).select_from(Job).where(where)
        )
        or 0
    )
    jobs = (
        await session.scalars(
            select(Job)
            .where(where)
            .order_by(Job.started_at.desc())
            .offset((current - 1) * page_size)
            .limit(page_size)
        )
    ).all()

    # 一次查出本页 job 的全部产物,按 job 分组
    outputs_by_job: dict[str, list[dict]] = {}
    if jobs:
        stmt = (
            select(DatasetVersion, Dataset)
            .join(Dataset, Dataset.id == DatasetVersion.dataset_id)
            .where(
                DatasetVersion.produced_by_job_id.in_([j.id for j in jobs])
            )
            .order_by(DatasetVersion.created_at)
        )
        for version, dataset in (await session.execute(stmt)).all():
            outputs_by_job.setdefault(version.produced_by_job_id, []).append(
                {
                    "datasetId": dataset.id,
                    "datasetName": dataset.name,
                    "versionId": version.id,
                    "versionNo": version.version_no,
                    "versionLabel": format_version_label(
                        version.version_no, version.created_at
                    ),
                    "rows": version.rows,
                }
            )

    data = []
    for job in jobs:
        outputs = outputs_by_job.get(job.id, [])
        data.append(
            IngestRunRead(
                id=job.id,
                task_id=task_id,
                status=job.state,
                rows=sum(o["rows"] or 0 for o in outputs),
                dataset_count=len(outputs),
                outputs=outputs or None,
                error=job.error,
                started_at=job.started_at or job.created_at,
                finished_at=job.finished_at,
                # 切片 C6:Job.trigger(manual|cron)透传到读模型,前端「触发来源」
                # column 据此渲染。存量 job 经迁移 0025 server_default='manual' 安全回填。
                trigger=job.trigger,
            )
        )
    return PageResponse[IngestRunRead](data=data, total=total)


@router.post("/ingest-tasks/{task_id}/stop")
async def stop_ingest_task(
    task_id: str,
    session: SessionDep,
) -> Response:
    """停止：转 failed 并追加手动停止日志。"""
    task = await session.get(IngestTask, task_id)
    if task is None:
        return _not_found()

    task.status = "failed"
    task.last_run_at = _now()
    task.logs = [*task.logs, "[WARN] 任务被手动停止"]
    await session.commit()
    await session.refresh(task)
    return JSONResponse(
        content=_item(task, category_name=await _category_name(session, task))
    )


@router.delete("/ingest-tasks/{task_id}")
async def delete_ingest_task(
    task_id: str,
    session: SessionDep,
) -> Response:
    """删除采集任务。

    切片 C / Task 3:删除前先 best-effort remove 调度作业(scheduler 未启用 /
    未启动 / 抛错均静默跳过);再删 DB 记录。即便 remove 失败也继续删记录,
    避免 jobstore 残留拖累 DB 清理——孤儿作业由 reconcile 在下次启动兜底清理。
    """
    task = await session.get(IngestTask, task_id)
    if task is None:
        return _not_found()

    _unsync_cron_job(task.id)
    await session.delete(task)
    await session.commit()
    return JSONResponse(content={"success": True})
