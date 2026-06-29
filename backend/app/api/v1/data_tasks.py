"""数据任务统一控制台:跨类型(数据治理 + 数据评估)聚合的任务列表。

定位为运维控制台(见 docs/superpowers/specs/2026-06-23-data-task-manager-design.md):
只提供「跨类型统一列表 + 检索」,任务的暂停/继续/停止/重跑/删除等写操作走通用
``/jobs/{id}/*`` 端点(已支持 pending/running/paused/cancelled 全状态)。新建任务
仍回各类型 editor,本端点不负责创建。

受管类型 = 异步可管控的 7 类:process/clean/distillation/synthesis/augmentation/
quality/review(ingest/annotate 不入控制台)。列表项复用 jobs._build_input/_build_output
挂上输入/产出版本概要,供前端在详情抽屉里做多版本按文件预览。
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Query
from pydantic import Field
from sqlalchemy import func, select, text

from app.api.deps import require_perm
from app.api.v1.jobs import (
    SessionDep,
    _build_input,
    _build_output,
    _dataset_job_filter,
)
from app.models.job import Job
from app.schemas.common import CamelModel, PageResponse
from app.schemas.job import JobRead

router = APIRouter(tags=["data-tasks"])

# 异步可管控的任务类型(治理 + 评估);ingest/annotate 等不入统一控制台
_TASK_TYPES: tuple[str, ...] = (
    "process",
    "clean",
    "distillation",
    "synthesis",
    "augmentation",
    "quality",
    "review",
)

# 任务生命周期阶段(与前端 STATE_VALUE_ENUM 对齐);dashboard 按此分桶计数
_TASK_STATES: tuple[str, ...] = (
    "pending",
    "running",
    "paused",
    "success",
    "failed",
    "cancelled",
)
# 终态(已结束的任务):用于「近 24h 完成」口径
_TERMINAL_STATES: tuple[str, ...] = ("success", "failed", "cancelled")
# 趋势 / 当日口径按北京时区(UTC+8)对齐——库内时间戳为 naive UTC,展示在北京时区
_BEIJING_OFFSET = timedelta(hours=8)
_TREND_DAYS = 14


class TypeStateCount(CamelModel):
    """某类型某阶段的任务数(仅非零组合;前端按需补零绘堆叠柱)。"""

    type: str
    state: str
    count: int


class TrendPoint(CamelModel):
    """趋势单日:按北京日(YYYY-MM-DD)统计的创建数 + 当日完成的成功/失败数。"""

    date: str
    created: int
    success: int
    failed: int


class DataTaskStats(CamelModel):
    """数据任务概览统计,驱动页顶 dashboard。范围 = 受管 7 类任务。

    - byState:各生命周期阶段计数(含全部 6 阶段,无则 0);成功率由前端从此派生。
    - byTypeState:类型 × 阶段计数(仅非零组合),供堆叠柱状图。
    - completedLast24h:近 24h 进入终态的任务数。
    - avgDurationSec:成功任务的平均处理时长(finished-started,秒);无样本为 None。
    - trend14d:近 14 天(北京日)创建 / 完成趋势,升序。
    """

    total: int
    by_state: dict[str, int]
    by_type_state: list[TypeStateCount]
    # 显式 camelCase 别名:to_camel 用 str.title(),数字会切词,默认会得到
    # completedLast24H / trend14D(末字母被大写),故这两个字段钉死干净的别名。
    completed_last24h: int = Field(serialization_alias="completedLast24h")
    avg_duration_sec: float | None
    trend14d: list[TrendPoint] = Field(serialization_alias="trend14d")
    success: bool = True


def _coerce_date(value: object) -> date:
    """把 DB 返回的日期(asyncpg 通常给 date,亦兼容 datetime / ISO 字符串)规整为 date。"""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


async def _build_trend14d(session: SessionDep, now_utc: datetime) -> list[TrendPoint]:
    """近 14 天(北京日)趋势:创建数 + 当日完成的成功 / 失败数。

    库内时间戳为 naive UTC,按 ``ts + interval '8 hours'`` 取北京日分桶,
    与列表 / UI 的北京时区展示对齐(否则傍晚的任务会被算到错误的日期)。
    """
    base = Job.type.in_(_TASK_TYPES)
    bj_today = (now_utc + _BEIJING_OFFSET).date()
    days = [bj_today - timedelta(days=i) for i in range(_TREND_DAYS - 1, -1, -1)]
    # 窗口起点(UTC):最早北京日 00:00 - 8h
    since_utc = datetime.combine(days[0], datetime.min.time()) - _BEIJING_OFFSET

    bj_created = func.date(Job.created_at + text("interval '8 hours'"))
    created_rows = (
        await session.execute(
            select(bj_created.label("d"), func.count())
            .where(base, Job.created_at >= since_utc)
            .group_by(bj_created)
        )
    ).all()
    created_map = {_coerce_date(d): n for d, n in created_rows}

    bj_finished = func.date(Job.finished_at + text("interval '8 hours'"))
    fin_rows = (
        await session.execute(
            select(bj_finished.label("d"), Job.state, func.count())
            .where(
                base,
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
        TrendPoint(
            date=day.isoformat(),
            created=created_map.get(day, 0),
            success=succ_map.get(day, 0),
            failed=fail_map.get(day, 0),
        )
        for day in days
    ]


@router.get(
    "/data-tasks/stats",
    response_model=DataTaskStats,
    dependencies=[Depends(require_perm("ops:datatask:list"))],
)
async def data_tasks_stats(session: SessionDep) -> DataTaskStats:
    """数据任务概览:状态 / 类型分布 + 近 24h 完成 + 平均时长 + 近 14 天趋势。

    范围与列表一致——仅受管的 7 类任务。均为廉价的 GROUP BY 聚合;随列表一同刷新
    (含运行时 3s 轮询)。若 jobs 表显著增大,建议给 created_at / finished_at 加索引。
    """
    base = Job.type.in_(_TASK_TYPES)
    now_utc = datetime.now(UTC).replace(tzinfo=None)  # naive UTC,与库内列对齐

    state_rows = (
        await session.execute(
            select(Job.state, func.count()).where(base).group_by(Job.state)
        )
    ).all()
    by_state = dict.fromkeys(_TASK_STATES, 0)
    total = 0
    for st, count in state_rows:
        total += count
        if st in by_state:
            by_state[st] = count

    ts_rows = (
        await session.execute(
            select(Job.type, Job.state, func.count())
            .where(base)
            .group_by(Job.type, Job.state)
        )
    ).all()
    by_type_state = [
        TypeStateCount(type=t, state=s, count=n) for t, s, n in ts_rows
    ]

    completed_last24h = (
        await session.scalar(
            select(func.count()).where(
                base,
                Job.state.in_(_TERMINAL_STATES),
                Job.finished_at >= now_utc - timedelta(hours=24),
            )
        )
        or 0
    )

    avg_raw = await session.scalar(
        select(
            func.avg(func.extract("epoch", Job.finished_at - Job.started_at))
        ).where(
            base,
            Job.state == "success",
            Job.started_at.is_not(None),
            Job.finished_at.is_not(None),
        )
    )
    avg_duration_sec = float(avg_raw) if avg_raw is not None else None

    trend14d = await _build_trend14d(session, now_utc)

    return DataTaskStats(
        total=total,
        by_state=by_state,
        by_type_state=by_type_state,
        completed_last24h=completed_last24h,
        avg_duration_sec=avg_duration_sec,
        trend14d=trend14d,
    )


@router.get(
    "/data-tasks",
    response_model=PageResponse[JobRead],
    dependencies=[Depends(require_perm("ops:datatask:list"))],
)
async def list_data_tasks(
    session: SessionDep,
    current: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(ge=1, le=100, alias="pageSize")] = 10,
    types: Annotated[str | None, Query()] = None,
    state: Annotated[str | None, Query()] = None,
    keyword: Annotated[str | None, Query()] = None,
    dataset_id: Annotated[str | None, Query(alias="datasetId")] = None,
) -> PageResponse[JobRead]:
    """跨类型统一列出数据任务(治理+评估),按创建时间倒序,带输入/产出版本概要。

    - types:逗号分隔的类型白名单(缺省=全部受管类型);非法类型静默忽略。
    - state:单值状态过滤(pending/running/paused/success/failed/cancelled)。
    - keyword:任务名模糊匹配(name ilike)。
    - datasetId:按数据集过滤(输入或产物版本属于该数据集)。
    """
    requested = {t.strip() for t in types.split(",") if t.strip()} if types else None
    sel_types = requested & set(_TASK_TYPES) if requested else set(_TASK_TYPES)
    conds = [Job.type.in_(sel_types)] if sel_types else []
    if not sel_types:
        # 全是非法类型 → 返回空集(用永假条件),而非全表
        conds = [Job.id == ""]
    if state:
        conds.append(Job.state == state)
    if keyword:
        conds.append(Job.name.ilike(f"%{keyword}%"))
    if dataset_id:
        conds.append(_dataset_job_filter(dataset_id))

    count_stmt = select(func.count()).select_from(Job).where(*conds)
    list_stmt = select(Job).where(*conds)
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
        read.output = await _build_output(session, r.id)  # 评估类无产物版本 → None
        data.append(read)
    return PageResponse[JobRead](data=data, total=total)
