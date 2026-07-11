"""worker 持久化队列契约(路线 C:执行层拆分)。

为什么这些用例重要:worker 模式把任务执行从 API 进程内协程搬到独立 worker 进程,
正确性全靠三条不变量成立:
  1. **认领互斥**:``SELECT ... FOR UPDATE SKIP LOCKED`` 保证同一 pending 任务不会
     被两个并发认领者同时拿到——否则同一任务双跑,产出重复版本、用量重复计费。
  2. **重试语义**:认领即 ``attempts+1``;心跳超时回收时仅在预算内(attempts <
     max_attempts)重排,耗尽则置 failed——保证崩溃可自愈又不会无限重试。
  3. **心跳存活**:running 任务定期续跳 ``heartbeat_at``,回收据此区分「还在跑」
     与「worker 已死」——续跳丢失即被判僵死回收。

均为 DB 级用例(依赖 PG 的 SKIP LOCKED / timestamptz),用真实测试库执行。
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app import worker
from app.models.job import Job


def _pending_job(job_id: str, *, queued_offset: int = 0) -> Job:
    """构造一条 pending 队列任务(queued_at 越早越先被认领)。"""
    return Job(
        id=job_id,
        name=f"任务 {job_id}",
        type="clean",
        state="pending",
        progress=0,
        attempts=0,
        max_attempts=3,
        queued_at=datetime.now(UTC) + timedelta(seconds=queued_offset),
    )


@pytest.mark.asyncio
async def test_claim_is_mutually_exclusive(
    session_factory: async_sessionmaker,
) -> None:
    """两个并发认领者抢同一条 pending 任务:恰好一个拿到,另一个拿到 None。

    两个会话各自开事务、都不提交(claim_job 不 commit,由调用方 commit),此时
    SKIP LOCKED 让后到者跳过被锁的行 → 不会重复认领。"""
    async with session_factory() as s:
        s.add(_pending_job("job-excl"))
        await s.commit()

    async with session_factory() as sa, session_factory() as sb:
        # 并发认领(同一事件循环上真并发:asyncpg 在 await 点交错执行)
        got_a, got_b = await asyncio.gather(
            worker.claim_job(sa, "worker-a"),
            worker.claim_job(sb, "worker-b"),
        )
        # 提交在两个 claim 都返回之后:确保锁在两次 SELECT 期间都持有
        await sa.commit()
        await sb.commit()

    claimed = [x for x in (got_a, got_b) if x is not None]
    assert claimed == ["job-excl"], (got_a, got_b)


@pytest.mark.asyncio
async def test_claim_increments_attempts_and_marks_running(
    session_factory: async_sessionmaker,
) -> None:
    """认领把任务从 pending 原子转 running,attempts +1、记 claimed_by / heartbeat。"""
    async with session_factory() as s:
        s.add(_pending_job("job-clm"))
        await s.commit()

    async with session_factory() as s:
        job_id = await worker.claim_job(s, "worker-x")
        await s.commit()
    assert job_id == "job-clm"

    async with session_factory() as s:
        job = await s.get(Job, "job-clm")
        assert job.state == "running"
        assert job.attempts == 1
        assert job.claimed_by == "worker-x"
        assert job.heartbeat_at is not None
        assert job.started_at is not None


@pytest.mark.asyncio
async def test_claim_skips_exhausted_attempts(
    session_factory: async_sessionmaker,
) -> None:
    """attempts 已达上限的 pending 任务不再被认领(冗余守护,防超预算重跑)。"""
    async with session_factory() as s:
        job = _pending_job("job-exhaust")
        job.attempts = 3  # == max_attempts
        s.add(job)
        await s.commit()

    async with session_factory() as s:
        job_id = await worker.claim_job(s, "worker-x")
        await s.commit()
    assert job_id is None


@pytest.mark.asyncio
async def test_claim_orders_by_queued_at(
    session_factory: async_sessionmaker,
) -> None:
    """近似 FIFO:先入队(queued_at 更早)的任务先被认领。"""
    async with session_factory() as s:
        s.add(_pending_job("job-late", queued_offset=100))
        s.add(_pending_job("job-early", queued_offset=0))
        await s.commit()

    async with session_factory() as s:
        job_id = await worker.claim_job(s, "worker-x")
        await s.commit()
    assert job_id == "job-early"


@pytest.mark.asyncio
async def test_heartbeat_advances_timestamp_only_while_running(
    session_factory: async_sessionmaker,
) -> None:
    """续跳推进 running 任务的 heartbeat_at;已落终态的任务续跳 no-op(不复活)。"""
    async with session_factory() as s:
        running = _pending_job("job-hb")
        running.state = "running"
        running.heartbeat_at = datetime.now(UTC) - timedelta(seconds=300)
        done = _pending_job("job-hb-done")
        done.state = "success"
        done.heartbeat_at = None
        s.add_all([running, done])
        await s.commit()
        old_hb = running.heartbeat_at

    async with session_factory() as s:
        await worker.heartbeat_once(s, "job-hb")
        await worker.heartbeat_once(s, "job-hb-done")
        await s.commit()

    async with session_factory() as s:
        running = await s.get(Job, "job-hb")
        done = await s.get(Job, "job-hb-done")
        assert running.heartbeat_at > old_hb
        assert done.state == "success"
        assert done.heartbeat_at is None  # 终态未被续跳复活


@pytest.mark.asyncio
async def test_heartbeat_once_fencing_rejects_stale_worker_id(
    session_factory: async_sessionmaker,
) -> None:
    """传 worker_id 时,heartbeat_once 是 fencing token 校验:一旦 claimed_by
    已不是调用方自己(被 recover_stale_jobs 判定超时、重排给了另一 worker),
    续跳必须不生效(返回 False、不推进 heartbeat_at)——这是 `_heartbeat_loop`
    能及时发现"所有权已丢失"并取消本地执行的前提,否则原 worker 会继续认为
    自己仍持有该任务,跑到终态时用旧状态覆盖新所有者的执行结果。"""
    async with session_factory() as s:
        job = _pending_job("job-fence")
        job.state = "running"
        job.claimed_by = "worker-a"
        job.heartbeat_at = datetime.now(UTC) - timedelta(seconds=60)
        s.add(job)
        await s.commit()
        old_hb = job.heartbeat_at

    # 另一 worker(非持有者)续跳:fencing 校验不通过,返回 False,不推进心跳
    async with session_factory() as s:
        still_owned = await worker.heartbeat_once(s, "job-fence", "worker-b")
        await s.commit()
    assert still_owned is False
    async with session_factory() as s:
        job = await s.get(Job, "job-fence")
        assert job.heartbeat_at == old_hb  # 未被非持有者续跳

    # 真正持有者续跳:fencing 校验通过,返回 True,正常推进心跳
    async with session_factory() as s:
        still_owned = await worker.heartbeat_once(s, "job-fence", "worker-a")
        await s.commit()
    assert still_owned is True
    async with session_factory() as s:
        job = await s.get(Job, "job-fence")
        assert job.heartbeat_at > old_hb


@pytest.mark.asyncio
async def test_recover_requeues_within_budget_fails_when_exhausted(
    session_factory: async_sessionmaker,
) -> None:
    """心跳超时回收:预算内的重排回 pending,耗尽的置 failed;新鲜心跳的不动。"""
    stale = datetime.now(UTC) - timedelta(seconds=9999)
    fresh = datetime.now(UTC)
    async with session_factory() as s:
        # 超时 + 仍有预算 → 重排
        j1 = _pending_job("job-rq")
        j1.state = "running"
        j1.attempts = 1
        j1.claimed_by = "dead-worker"
        j1.heartbeat_at = stale
        # 超时 + 预算耗尽 → 失败
        j2 = _pending_job("job-fail")
        j2.state = "running"
        j2.attempts = 3
        j2.claimed_by = "dead-worker"
        j2.heartbeat_at = stale
        # 心跳新鲜 → 不动(worker 还活着)
        j3 = _pending_job("job-alive")
        j3.state = "running"
        j3.attempts = 1
        j3.claimed_by = "live-worker"
        j3.heartbeat_at = fresh
        # inline 模式运行态(claimed_by 为空)→ 回收不碰
        j4 = _pending_job("job-inline")
        j4.state = "running"
        j4.attempts = 0
        j4.claimed_by = None
        j4.heartbeat_at = None
        s.add_all([j1, j2, j3, j4])
        await s.commit()

    async with session_factory() as s:
        requeued, failed = await worker.recover_stale_jobs(s, timeout_seconds=120)
    assert (requeued, failed) == (1, 1)

    async with session_factory() as s:
        j1 = await s.get(Job, "job-rq")
        j2 = await s.get(Job, "job-fail")
        j3 = await s.get(Job, "job-alive")
        j4 = await s.get(Job, "job-inline")
        # 重排:回 pending、清认领/心跳、attempts 保留(下次认领再 +1)
        assert j1.state == "pending"
        assert j1.claimed_by is None
        assert j1.heartbeat_at is None
        assert j1.attempts == 1
        assert j1.queued_at is not None
        # 失败:终态 + 原因注明崩溃回收
        assert j2.state == "failed"
        assert "崩溃回收" in (j2.error or "")
        # 存活 / inline:纹丝不动
        assert j3.state == "running"
        assert j4.state == "running"


@pytest.mark.asyncio
async def test_claim_then_recover_roundtrip_bounds_retries(
    session_factory: async_sessionmaker,
) -> None:
    """端到端重试上界:认领→崩溃→回收 反复,attempts 单调递增,达上限后终以 failed
    收口——保证崩溃自愈不会退化为无限重排。"""
    async with session_factory() as s:
        s.add(_pending_job("job-loop"))
        await s.commit()

    async def _claim_and_stale() -> str | None:
        # 认领(attempts+1、running)后把心跳打到远古,模拟 worker 崩溃
        async with session_factory() as s:
            jid = await worker.claim_job(s, "flaky-worker")
            await s.commit()
        if jid is None:
            return None
        async with session_factory() as s:
            job = await s.get(Job, jid)
            job.heartbeat_at = datetime.now(UTC) - timedelta(seconds=9999)
            await s.commit()
        async with session_factory() as s:
            await worker.recover_stale_jobs(s, timeout_seconds=120)
        return jid

    # max_attempts=3:三次认领后回收应置 failed
    for _ in range(3):
        await _claim_and_stale()

    async with session_factory() as s:
        job = await s.get(Job, "job-loop")
        assert job.attempts == 3
        assert job.state == "failed"
        # 已 failed,不再可认领
        rows = await s.execute(select(Job).where(Job.state == "pending"))
        assert rows.scalars().all() == []
