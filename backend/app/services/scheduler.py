"""APScheduler 调度器:PG 持久化 jobstore + 启动对账(切片 C / Task 2)。

为何独立成模块:
- AsyncIOScheduler + SQLAlchemyJobStore(同步 SQLAlchemy)的初始化、对账、
  优雅关闭集中在一处,避免散落到 lifespan。
- SQLAlchemyJobStore 走同步 SQLAlchemy,不能用 app 的 async DATABASE_URL
  (``postgresql+asyncpg://``);本模块负责把它派生为同步驱动 URL
  (``postgresql+psycopg2://``,依赖 psycopg2-binary),不污染 Settings。

对账语义(``reconcile``):
- 扫 ``IngestTask.schedule.mode='cron'`` 的任务集 ↔ jobstore 现存 ``ingest:*`` 作业;
- 缺则 upsert、多则 remove、匹配的不动(避免无谓重写 trigger);
- 返回 ``(added, removed)`` 计数供运维观测。

触发函数 ``_trigger_ingest``(切片 C / Task 4 注入真实执行逻辑):
- async:经 ``AsyncIOExecutor`` 在事件循环内 await(见 ``init_scheduler``),
  直接调 ``app.api.v1.ingest_tasks._execute_ingest(trigger="cron")``——无需
  sync/async bridge。
- 重叠跳过:该任务最近一条 Job.state="running" → 日志 + 跳过,不建重复 Job。
- 水位推进(增量采集)留 ``# TODO(C5)`` 钩子,Task 5 填。

DEFERRED:真实 ``AsyncIOScheduler.start()`` / ``SQLAlchemyJobStore`` 建表 /
对真 DB 扫描需 PG 可达——当前 .60 测试库 ConnectionRefused,留待回归。
"""

from __future__ import annotations

import logging
from urllib.parse import urlsplit, urlunsplit

from apscheduler.executors.asyncio import AsyncIOExecutor
from apscheduler.jobstores.base import JobLookupError
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import async_session_factory
from app.models.datasource import DataSource
from app.models.ingest_task import IngestTask
from app.models.job import Job

_logger = logging.getLogger(__name__)

# 作业 id 前缀:reconcile 据此识别「ingest 调度」作业,与其他子系统解耦。
JOB_ID_PREFIX = "ingest:"

# 模块级运行期 scheduler 引用(由 _lifespan 注册 / 清空)。
# Routes 通过 ``get_scheduler()`` best-effort 读取;None 时静默跳过——
# 采集主流程不依赖调度器在线(可手工 rerun)。
_running_scheduler: AsyncIOScheduler | None = None


def set_scheduler(scheduler: AsyncIOScheduler | None) -> None:
    """注册 / 清空模块级运行期 scheduler 引用(由 ``_lifespan`` 调用)。

    - 启动成功:``set_scheduler(scheduler)``
    - 启动失败 / 关闭:``set_scheduler(None)``
    """
    global _running_scheduler
    _running_scheduler = scheduler


def get_scheduler() -> AsyncIOScheduler | None:
    """读取运行期 scheduler 实例(未启动 / 未启用 / 启动失败时返回 None)。

    Routes 据此决定是否 upsert/remove 调度作业;None 时静默跳过,
    采集主流程不依赖调度器在线。
    """
    return _running_scheduler


def job_id_for(task_id: str) -> str:
    """构造调度器作业 id:``ingest:{task_id}``。"""
    return f"{JOB_ID_PREFIX}{task_id}"


def _sync_database_url(async_url: str) -> str:
    """把 app 的 async DATABASE_URL 派生为同步驱动 URL。

    SQLAlchemyJobStore 走同步 SQLAlchemy,``postgresql+asyncpg://`` 不能直接用;
    统一改写为 ``postgresql+psycopg2://``(psycopg2-binary 已是项目依赖)。
    非 postgresql scheme 原样返回(mysql/sqlite 各自的同步驱动不变)。
    """
    parts = urlsplit(async_url)
    if parts.scheme == "postgresql+asyncpg":
        parts = parts._replace(scheme="postgresql+psycopg2")
    return urlunsplit(parts)


def init_scheduler() -> AsyncIOScheduler:
    """构造 AsyncIOScheduler,挂一个指向 PG ``apscheduler_jobs`` 的 SQLAlchemyJobStore。

    SQLAlchemyJobStore 在首次连接时会自动建表(CREATE TABLE IF NOT EXISTS),
    无需 alembic 迁移。timezone=UTC 让 cron 在多 tz 部署下确定istic。

    切片 C / Task 4:显式指定 ``AsyncIOExecutor`` 为默认 executor——这样
    ``_trigger_ingest``(async)能在事件循环内被 await,无需 sync/async bridge。
    AsyncIOScheduler 3.10+ 文档说默认就是 AsyncIOExecutor,但跨版本显式声明更稳妥。
    """
    sync_url = _sync_database_url(settings.database_url)
    jobstore = SQLAlchemyJobStore(url=sync_url, tablename="apscheduler_jobs")
    return AsyncIOScheduler(
        jobstores={"default": jobstore},
        executors={"default": AsyncIOExecutor()},
        timezone="UTC",
    )


async def _trigger_ingest(task_id: str) -> None:
    """调度器触发的采集执行入口(async,经 AsyncIOExecutor 在事件循环内 await)。

    重叠跳过:若该任务最近一条 Job 仍 ``state="running"`` → 日志 + 跳过,不建
    重复 Job。否则调 ``_execute_ingest(trigger="cron")`` 并 commit。

    session 由模块级 ``async_session_factory`` 提供(调度器在请求上下文外运行,
    不复用请求会话)。Task 5 在此函数内推进水位(增量采集)。

    lazy import ``_execute_ingest`` 以避免 scheduler ↔ ingest_tasks 循环导入
    (ingest_tasks 顶部 ``from app.services import scheduler as scheduler_mod``
    会先于 ``_execute_ingest`` 定义完成)。
    """
    # lazy import:避免与 ingest_tasks 路由模块顶部互相 import 时序问题
    from app.api.v1.ingest_tasks import _execute_ingest  # noqa: PLC0415

    async with async_session_factory() as session:
        task = await session.get(IngestTask, task_id)
        if task is None:
            _logger.warning("调度触发找不到任务 task_id=%s,跳过", task_id)
            return
        datasource = await session.get(DataSource, task.datasource_id)
        if datasource is None:
            _logger.warning(
                "任务 %s 的数据源不存在(datasource_id=%s),跳过",
                task_id,
                task.datasource_id,
            )
            return

        # 重叠跳过:该任务最近一条 Job 仍 running → 跳过本次调度
        latest_job = await session.scalar(
            select(Job)
            .where(Job.ingest_task_id == task_id)
            .order_by(Job.created_at.desc())
            .limit(1)
        )
        if latest_job is not None and latest_job.state == "running":
            _logger.info(
                "任务 %s 上次未完成,跳过本次调度(latest_job=%s state=running)",
                task_id,
                latest_job.id,
            )
            return

        # TODO(C5): advance watermark —— 增量采集水位推进在此处插入,
        # 先于 _execute_ingest 执行(把上次成功 Job 的 watermark 投影到 task.extract)。

        await _execute_ingest(session, task, datasource, trigger="cron")
        await session.commit()


def upsert_cron_job(scheduler: AsyncIOScheduler, task: IngestTask) -> None:
    """为 cron 任务 upsert 调度作业(``replace_existing=True``)。

    作业 id = ``ingest:{task.id}``;trigger 由 ``task.schedule['cron']`` 解析。
    任务 schedule 缺 cron 表达式(mode=cron 但 cron 字段缺失/空)时静默跳过——
    数据异常不应让启动 / 路由层抛错。
    """
    schedule = task.schedule if isinstance(task.schedule, dict) else {}
    cron_expr = schedule.get("cron")
    if not cron_expr:
        _logger.warning(
            "任务 %s 标记为 cron 但无 cron 表达式,跳过调度",
            getattr(task, "id", "?"),
        )
        return
    trigger = CronTrigger.from_crontab(cron_expr)
    scheduler.add_job(
        _trigger_ingest,
        trigger=trigger,
        args=[task.id],
        id=job_id_for(task.id),
        replace_existing=True,
    )


def remove_cron_job(scheduler: AsyncIOScheduler, task_id: str) -> None:
    """移除 ``ingest:{task_id}`` 作业;缺失则无操作(容忍 JobLookupError)。"""
    try:
        scheduler.remove_job(job_id_for(task_id))
    except JobLookupError:
        _logger.debug("调度器里找不到作业 %s,跳过移除", job_id_for(task_id))


async def reconcile(
    session: AsyncSession, scheduler: AsyncIOScheduler
) -> tuple[int, int]:
    """启动对账:把 cron 任务的作业落到 jobstore,清理孤立的调度器作业。

    - 缺则 upsert、多则 remove、匹配的不动;
    - 返回 ``(added, removed)`` 计数。

    「匹配的不动」:matched job 不重复 add_job,避免每次启动都重写 trigger
    (cron 表达式变更由路由层显式调 upsert_cron_job,启动 reconcile 只负责
    修复状态漂移)。
    """
    # 现有 ingest:* 作业(APScheduler 3.x scheduler.get_jobs() 同步可调)
    existing = {
        j.id for j in scheduler.get_jobs() if j.id.startswith(JOB_ID_PREFIX)
    }

    # DB 里的 cron 任务(mode=cron 且携带 cron 表达式)
    result = await session.execute(select(IngestTask))
    cron_tasks = [
        t
        for t in result.scalars()
        if isinstance(t.schedule, dict)
        and t.schedule.get("mode") == "cron"
        and t.schedule.get("cron")
    ]
    expected_ids = {job_id_for(t.id) for t in cron_tasks}

    # 缺则 upsert
    added = 0
    for t in cron_tasks:
        jid = job_id_for(t.id)
        if jid not in existing:
            # 单条 cron 非法不拖垮整个 reconcile/调度器:跳过该任务并告警
            try:
                upsert_cron_job(scheduler, t)
                added += 1
            except (ValueError, TypeError):
                _logger.warning(
                    "任务 %s 的 cron 表达式无效,跳过", t.id, exc_info=True
                )

    # 多则 remove(调度器里有但 DB 里没有对应 cron 任务 = 孤儿)
    removed = 0
    for orphan_id in existing - expected_ids:
        try:
            scheduler.remove_job(orphan_id)
            removed += 1
        except JobLookupError:
            # race:刚才 get_jobs 有,现在没了——按现状跳过
            _logger.debug("孤儿作业 %s 已被并发移除,跳过", orphan_id)

    if added or removed:
        _logger.info(
            "调度器对账完成:added=%d removed=%d", added, removed
        )
    return added, removed


def shutdown_scheduler(scheduler: AsyncIOScheduler | None) -> None:
    """关闭调度器,容忍 None / 关闭失败(关闭路径不应抛)。

    ``wait=False``:不等当前作业跑完——lifespan 关闭阶段应快速返回,
    避免被一个长跑作业卡住整个 shutdown。
    """
    if scheduler is None:
        return
    try:
        scheduler.shutdown(wait=False)
    except Exception:  # noqa: BLE001
        _logger.warning("关闭调度器失败(已忽略)", exc_info=True)
