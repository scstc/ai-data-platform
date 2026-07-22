"""独立任务 worker 进程(路线 C:执行层拆分)。

``JOB_EXECUTION_MODE=worker`` 时,API 进程只把任务置 ``pending`` + ``queued_at``
(见 ``job_runner.spawn``),不再进程内起协程;由本 worker 进程认领并执行:

- **认领**:``SELECT ... FOR UPDATE SKIP LOCKED`` 取一条 pending 任务,原子
  置 ``running`` + ``attempts+1`` + ``claimed_by`` + ``heartbeat_at``,SKIP LOCKED
  保证多 worker / 多协程并发认领互斥(不会重复拿同一 job)。
- **执行**:复用 ``job_runner._run_job``(与 inline 模式同一条执行路径),运行期
  由并发协程每 ``worker_heartbeat_interval`` 秒续跳 ``heartbeat_at``。
- **崩溃回收**:worker 启动时及每 ``worker_recovery_interval`` 秒扫描 ——
  ``running`` 且心跳超时(``> worker_heartbeat_timeout``)的任务,``attempts <
  max_attempts`` 则重置回 ``pending`` 重试,否则置 ``failed``。inline 模式的孤儿
  回收仍由 API 进程的 ``reconcile_orphans`` 负责(两条路径互不干扰:回收只认
  ``claimed_by IS NOT NULL`` 的 worker 任务)。

启动::

    python -m app.worker

优雅停机:收到 SIGTERM/SIGINT 后停止认领新任务,等待在跑任务收尾
(最长 ``worker_shutdown_grace`` 秒);超时未收尾的任务随进程退出中断,其心跳
不再续跳,下次 worker 启动时由回收逻辑重排,不会丢。

注意(已知限制):pause/stop 端点经 ``engine.terminate_job`` 杀子进程是**进程内**
操作(子进程注册表在 worker 进程),API 进程调用找不到 worker 里的子进程 →
worker 模式下运行中任务的即时暂停/停止不生效(状态仍会随任务自然终态更新)。
跨进程取消需 DB 信号量,属后续工作,不在本路线范围。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import signal
import socket
from datetime import UTC, datetime, timedelta

from sqlalchemy import nulls_first, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.db import async_session_factory
from app.models.job import Job
from app.services import job_runner
from app.services.llm_config import refresh_cache

logger = logging.getLogger(__name__)


def _resolve_worker_id(configured: str) -> str:
    """worker 标识:显式配置优先,否则 hostname-pid(多副本天然区分)。"""
    return configured or f"{socket.gethostname()}-{os.getpid()}"


def _resolve_concurrency() -> int:
    """worker 并发数:worker_concurrency>0 时用它,否则沿用 engine_concurrency。"""
    return settings.worker_concurrency or settings.engine_concurrency


def _aware_now() -> datetime:
    """时区感知的当前 UTC(用于 timestamptz 列:heartbeat_at / queued_at)。"""
    return datetime.now(UTC)


def _naive_now() -> datetime:
    """无时区的当前 UTC(用于 naive 列:started_at / finished_at,与 job_runner 一致)。"""
    return datetime.now(UTC).replace(tzinfo=None)


def _recovery_verdict(attempts: int, max_attempts: int) -> str:
    """崩溃回收判据:仍有尝试预算 → 'requeue' 重试,否则 'fail' 放弃。

    这是回收语义的核心——为什么重试:worker 崩溃/被杀不代表任务本身有问题,
    应在预算内重试;耗尽预算才认定为无法完成。attempts 在**认领**时已 +1,
    故这里比较的是「已消耗的认领次数」是否触顶。
    """
    return "requeue" if attempts < max_attempts else "fail"


async def claim_job(session: AsyncSession, worker_id: str) -> str | None:
    """认领一条 pending 任务(``FOR UPDATE SKIP LOCKED``);返回 job_id 或 None。

    在 ``session`` 当前事务内加行锁并原子更新为 running;**调用方负责 commit**
    (commit 释放行锁)。SKIP LOCKED 让并发认领者跳过已被锁的行,保证同一 job
    不会被两个认领者同时拿到。按 ``queued_at``(NULLS FIRST)+ ``created_at`` 排序
    近似 FIFO;``attempts < max_attempts`` 冗余守护(超预算的任务已是 failed)。
    """
    row = await session.execute(
        select(Job.id)
        .where(Job.state == "pending", Job.attempts < Job.max_attempts)
        .order_by(nulls_first(Job.queued_at.asc()), Job.created_at.asc())
        .limit(1)
        .with_for_update(skip_locked=True)
    )
    job_id = row.scalar_one_or_none()
    if job_id is None:
        return None
    await session.execute(
        update(Job)
        .where(Job.id == job_id)
        .values(
            state="running",
            attempts=Job.attempts + 1,
            claimed_by=worker_id,
            heartbeat_at=_aware_now(),
            started_at=_naive_now(),
        )
    )
    return job_id


async def heartbeat_once(
    session: AsyncSession, job_id: str, worker_id: str | None = None
) -> bool:
    """续跳一次 ``heartbeat_at``(仅当仍 running);**调用方负责 commit**。

    以 ``state=='running'`` 为条件:任务已落终态时续跳自然 no-op,不会复活。

    传入 ``worker_id`` 时额外要求 ``claimed_by == worker_id``(fencing
    token):返回值指示本次续跳是否命中(即所有权是否仍在本 worker)。
    ``recover_stale_jobs`` 只按心跳超时判"僵死"就把任务重排给另一 worker,
    若原 worker 只是暂时被阻塞(未真崩溃),它之后仍会走到自己的终态
    commit,用内存里的旧状态覆盖第二个 worker 产生的更新。这里让
    ``_heartbeat_loop`` 能在所有权丢失的第一时间发现并取消本地执行,收窄
    (而非彻底消除,终态 commit 本身的 fencing 校验属 job_runner 范围)这个
    覆盖窗口。不传 ``worker_id`` 时保持旧行为(仅按 state 过滤)。
    """
    conditions = [Job.id == job_id, Job.state == "running"]
    if worker_id is not None:
        conditions.append(Job.claimed_by == worker_id)
    result = await session.execute(
        update(Job).where(*conditions).values(heartbeat_at=_aware_now())
    )
    return (result.rowcount or 0) > 0


async def recover_stale_jobs(
    session: AsyncSession, *, timeout_seconds: float
) -> tuple[int, int]:
    """回收心跳超时的 worker 任务;返回 (重排数, 失败数)。**本函数内 commit**。

    只认 ``claimed_by IS NOT NULL`` 的 running 任务(worker 认领的),不碰 inline
    模式的运行态,两条回收路径互不干扰。心跳为空亦视为超时(认领后还没来得及
    续跳就崩了)。判据见 ``_recovery_verdict``。
    """
    cutoff = _aware_now() - timedelta(seconds=timeout_seconds)
    rows = await session.execute(
        select(Job)
        .where(
            Job.state == "running",
            Job.claimed_by.is_not(None),
            or_(Job.heartbeat_at.is_(None), Job.heartbeat_at < cutoff),
        )
        .with_for_update(skip_locked=True)
    )
    requeued = failed = 0
    for job in rows.scalars().all():
        if _recovery_verdict(job.attempts, job.max_attempts) == "requeue":
            job.state = "pending"
            job.claimed_by = None
            job.heartbeat_at = None
            job.started_at = None
            job.queued_at = _aware_now()
            requeued += 1
        else:
            job.state = "failed"
            job.error = (
                f"worker 崩溃回收:心跳超时且已尝试 "
                f"{job.attempts}/{job.max_attempts} 次"
            )
            job.claimed_by = None
            job.heartbeat_at = None
            job.finished_at = _naive_now()
            failed += 1
    await session.commit()
    return requeued, failed


# ── worker 主循环编排 ──────────────────────────────────────────────


async def _claim(worker_id: str) -> str | None:
    """认领一条任务(独立会话);DB 异常吞掉并告警,让主循环下轮重试
    (兜住 worker 早于 DB/迁移就绪的启动竞态)。"""
    try:
        async with async_session_factory() as session:
            job_id = await claim_job(session, worker_id)
            await session.commit()
            return job_id
    except Exception:  # noqa: BLE001
        logger.warning("认领任务失败(将重试)", exc_info=True)
        return None


async def _recover_once() -> None:
    """跑一轮心跳回收(独立会话);失败仅告警。"""
    try:
        async with async_session_factory() as session:
            requeued, failed = await recover_stale_jobs(
                session, timeout_seconds=settings.worker_heartbeat_timeout
            )
        if requeued or failed:
            logger.info("心跳回收:重排 %d,置失败 %d", requeued, failed)
    except Exception:  # noqa: BLE001
        logger.warning("心跳回收失败(将重试)", exc_info=True)


async def _heartbeat_loop(
    job_id: str, worker_id: str, stop: asyncio.Event, run_task: asyncio.Task
) -> None:
    """运行期每 heartbeat_interval 秒续跳一次,直到 stop 置位。

    每次续跳都带 ``worker_id`` 校验所有权(见 ``heartbeat_once``):一旦发现
    ``claimed_by`` 已不是本 worker(被 ``recover_stale_jobs`` 判定超时、重排
    给了另一 worker),立即取消本地仍在跑的 ``run_task``,不再等它跑到终态
    commit 才去覆盖第二个 worker 的执行结果。
    """
    interval = settings.worker_heartbeat_interval
    while not stop.is_set():
        await _wait_stop(stop, interval)
        if stop.is_set():
            break
        try:
            async with async_session_factory() as session:
                still_owned = await heartbeat_once(session, job_id, worker_id)
                await session.commit()
            if not still_owned:
                logger.warning(
                    "任务 %s 所有权已丢失(claimed_by 不再是本 worker=%s),"
                    "判定为被崩溃回收误判重排,取消本地执行",
                    job_id,
                    worker_id,
                )
                run_task.cancel()
                return
        except Exception:  # noqa: BLE001
            logger.warning("任务 %s 心跳续跳失败", job_id, exc_info=True)


async def _execute(job_id: str, worker_id: str) -> None:
    """执行一个已认领任务:并发跑心跳续跳 + 复用 job_runner._run_job。

    _run_job 自身兜底所有业务异常并落终态,这里的 try 只防其意外冒泡导致
    整个 worker 崩溃(含被心跳续跳因所有权丢失而主动 cancel 的情形)。"""
    # worker 进程没有 API lifespan,LLM 活跃配置缓存不会自动加载/更新;不刷新
    # 则 needs_api 算子拿不到 key,逐样本失败被 DJ 跳过,空产出还报 success。
    # 每次执行前从 DB 现刷,顺带覆盖运行期间管理员轮换 key 的场景。
    try:
        async with async_session_factory() as session:
            await refresh_cache(session)
    except Exception:  # noqa: BLE001
        logger.exception("刷新 LLM 配置缓存失败,继续执行(回退 env 凭证)")
    hb_stop = asyncio.Event()
    run_task = asyncio.create_task(job_runner._run_job(job_id))
    hb = asyncio.create_task(_heartbeat_loop(job_id, worker_id, hb_stop, run_task))
    try:
        await run_task
    except asyncio.CancelledError:
        logger.warning("任务 %s 因所有权丢失被本地取消", job_id)
    except Exception:  # noqa: BLE001
        logger.exception("任务 %s 执行抛出未捕获异常", job_id)
    finally:
        hb_stop.set()
        with contextlib.suppress(Exception):
            await hb


async def _wait_stop(stop: asyncio.Event, timeout: float) -> None:
    """等 stop 置位,最多 timeout 秒(超时正常返回)——可被停机立即打断的 sleep。"""
    with contextlib.suppress(asyncio.TimeoutError):
        await asyncio.wait_for(stop.wait(), timeout)


async def _recovery_loop(stop: asyncio.Event) -> None:
    """周期性心跳回收,直到 stop 置位。"""
    while not stop.is_set():
        await _wait_stop(stop, settings.worker_recovery_interval)
        if stop.is_set():
            break
        await _recover_once()


def _install_signal_handlers(stop: asyncio.Event) -> None:
    """SIGTERM/SIGINT → 置 stop(优雅停机)。

    Windows 无 add_signal_handler 时退回 signal.signal。"""
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except (NotImplementedError, RuntimeError):
            signal.signal(sig, lambda *_: stop.set())


async def _drain(active: set[asyncio.Task], grace: float) -> None:
    """优雅停机:等在跑任务收尾,最长 grace 秒;超时者留给心跳回收。"""
    if not active:
        return
    logger.info("优雅停机:等待 %d 个在跑任务收尾(最长 %ss)", len(active), grace)
    _done, pending = await asyncio.wait(active, timeout=grace)
    if pending:
        logger.warning(
            "%d 个任务未在宽限期内收尾,随进程退出中断,留给心跳超时回收重排",
            len(pending),
        )


async def run_worker() -> None:
    """worker 主协程:启动回收 → 循环认领并派发 → 优雅停机。"""
    worker_id = _resolve_worker_id(settings.worker_id)
    concurrency = _resolve_concurrency()
    logger.info(
        "job worker 启动:id=%s 并发=%d 轮询=%.1fs 心跳=%.0fs 超时=%.0fs",
        worker_id,
        concurrency,
        settings.worker_poll_interval,
        settings.worker_heartbeat_interval,
        settings.worker_heartbeat_timeout,
    )

    stop = asyncio.Event()
    _install_signal_handlers(stop)

    # 启动即回收上次崩溃残留 + 清理孤儿 staging(worker 的引擎产物)
    await _recover_once()
    job_runner._cleanup_staging_orphans()

    active: set[asyncio.Task] = set()
    recovery_task = asyncio.create_task(_recovery_loop(stop))
    try:
        while not stop.is_set():
            if len(active) >= concurrency:
                # 满载:等任一在跑任务腾出槽位(或到轮询上限再看停机标记)
                await asyncio.wait(
                    active,
                    timeout=settings.worker_poll_interval,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                continue
            job_id = await _claim(worker_id)
            if job_id is None:
                # 无可认领任务:空转轮询(可被停机立即打断)
                await _wait_stop(stop, settings.worker_poll_interval)
                continue
            logger.info("认领任务 %s(worker=%s)", job_id, worker_id)
            task = asyncio.create_task(_execute(job_id, worker_id))
            active.add(task)
            task.add_done_callback(active.discard)
    finally:
        stop.set()
        recovery_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await recovery_task
        await _drain(active, settings.worker_shutdown_grace)
    logger.info("job worker 已停止(id=%s)", worker_id)


def main() -> None:
    """进程入口:python -m app.worker。"""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    if settings.job_execution_mode != "worker":
        logger.warning(
            "JOB_EXECUTION_MODE=%s(非 worker):worker 仍会认领队列,但若 API 进程"
            "同时 inline 执行会导致同一任务双跑,请确认部署配置。",
            settings.job_execution_mode,
        )
    asyncio.run(run_worker())


if __name__ == "__main__":
    main()
