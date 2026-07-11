"""worker 纯逻辑单测(不连库):模式分派 + 回收判据 + 配置解析。

与 test_worker_queue.py 互补:那边验 DB 级不变量,这边固化不依赖 DB 的判定逻辑
(为什么这样判),跑得快、可在任意环境执行。
"""

from __future__ import annotations

import asyncio

import pytest

from app import worker
from app.core.config import settings
from app.services import job_runner


def test_recovery_verdict_retries_within_budget() -> None:
    """回收判据:认领次数未触顶就重试,触顶才放弃——这是崩溃自愈的核心语义。"""
    assert worker._recovery_verdict(1, 3) == "requeue"
    assert worker._recovery_verdict(2, 3) == "requeue"
    # attempts 在认领时已 +1,故 == max 即视为预算耗尽
    assert worker._recovery_verdict(3, 3) == "fail"
    assert worker._recovery_verdict(4, 3) == "fail"


def test_resolve_worker_id_prefers_explicit() -> None:
    """显式配置的 worker_id 原样用;留空时回退 hostname-pid(多副本可区分)。"""
    assert worker._resolve_worker_id("edge-01") == "edge-01"
    auto = worker._resolve_worker_id("")
    assert auto and "-" in auto  # hostname-pid 形态
    assert str(__import__("os").getpid()) in auto


def test_resolve_concurrency_falls_back_to_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """worker_concurrency>0 时用它;为 0 时沿用 engine_concurrency(单机对齐)。"""
    monkeypatch.setattr(settings, "worker_concurrency", 0)
    monkeypatch.setattr(settings, "engine_concurrency", 5)
    assert worker._resolve_concurrency() == 5
    monkeypatch.setattr(settings, "worker_concurrency", 2)
    assert worker._resolve_concurrency() == 2


@pytest.mark.asyncio
async def test_spawn_worker_mode_enqueues_not_executes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """worker 模式:spawn 只入队(_mark_queued),绝不进程内执行(_run_job)。

    这是路线 C 的行为分界——API 进程一旦误在 worker 模式跑 _run_job,就会与独立
    worker 双跑同一任务。"""
    calls: list[str] = []

    async def _fake_mark_queued(job_id: str) -> None:
        calls.append(f"queued:{job_id}")

    async def _fake_run_job(job_id: str) -> None:
        calls.append(f"ran:{job_id}")

    monkeypatch.setattr(job_runner, "_mark_queued", _fake_mark_queued)
    monkeypatch.setattr(job_runner, "_run_job", _fake_run_job)
    monkeypatch.setattr(settings, "job_execution_mode", "worker")

    job_runner.spawn("job-w1")
    # 让被调度的后台任务跑一拍
    for _ in range(3):
        await asyncio.sleep(0)

    assert calls == ["queued:job-w1"]


@pytest.mark.asyncio
async def test_spawn_inline_mode_executes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """inline 模式(默认):spawn 进程内起 _run_job,行为与历史一致。"""
    calls: list[str] = []

    async def _fake_mark_queued(job_id: str) -> None:
        calls.append(f"queued:{job_id}")

    async def _fake_run_job(job_id: str) -> None:
        calls.append(f"ran:{job_id}")

    monkeypatch.setattr(job_runner, "_mark_queued", _fake_mark_queued)
    monkeypatch.setattr(job_runner, "_run_job", _fake_run_job)
    monkeypatch.setattr(settings, "job_execution_mode", "inline")

    job_runner.spawn("job-i1")
    for _ in range(3):
        await asyncio.sleep(0)

    assert calls == ["ran:job-i1"]


class _FakeSessionCtx:
    """最小 `async with async_session_factory() as s` 占位:heartbeat_once 本身
    被打桩,不真的用这个 session 执行 SQL,只需形状能过 `async with`。"""

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def commit(self) -> None:
        pass


@pytest.mark.asyncio
async def test_heartbeat_loop_cancels_run_task_on_lost_ownership(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """所有权丢失(heartbeat_once 返回 False,即 claimed_by 已不是本 worker)时,
    心跳循环必须立即 cancel 本地仍在跑的 run_task 并返回,而不是继续放任它跑
    到终态 commit——那样会用本 worker 内存里的旧状态覆盖第二个 worker(真正
    认领者)已产生的执行结果。这是收窄"两个 worker 同跑一个 job"覆盖窗口的
    核心动作(完整根治仍需 job_runner 侧的终态 fencing,不在本模块范围)。"""
    monkeypatch.setattr(worker, "async_session_factory", _FakeSessionCtx)
    monkeypatch.setattr(settings, "worker_heartbeat_interval", 0.01)

    heartbeat_calls: list[str] = []

    async def fake_heartbeat_once(session, job_id, worker_id) -> bool:
        heartbeat_calls.append(worker_id)
        return False  # 模拟所有权已丢失

    monkeypatch.setattr(worker, "heartbeat_once", fake_heartbeat_once)

    async def _forever() -> None:
        await asyncio.sleep(10)

    run_task = asyncio.create_task(_forever())
    stop = asyncio.Event()

    await worker._heartbeat_loop("job-x", "worker-1", stop, run_task)

    assert heartbeat_calls == ["worker-1"]
    # run_task 已被请求取消:等它把 CancelledError 实际抛出来
    with pytest.raises(asyncio.CancelledError):
        await run_task
    assert run_task.cancelled()


@pytest.mark.asyncio
async def test_heartbeat_loop_keeps_running_while_ownership_held(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """对照组:仍持有所有权(heartbeat_once 返回 True)时,心跳循环应持续续跳、
    不取消 run_task——不能因为引入 fencing 就误伤正常运行中的任务。"""
    monkeypatch.setattr(worker, "async_session_factory", _FakeSessionCtx)
    monkeypatch.setattr(settings, "worker_heartbeat_interval", 0.01)

    heartbeat_calls: list[str] = []

    async def fake_heartbeat_once(session, job_id, worker_id) -> bool:
        heartbeat_calls.append(worker_id)
        return True  # 所有权仍在本 worker

    monkeypatch.setattr(worker, "heartbeat_once", fake_heartbeat_once)

    async def _forever() -> None:
        await asyncio.sleep(10)

    run_task = asyncio.create_task(_forever())
    stop = asyncio.Event()

    async def _stop_after_a_few_beats() -> None:
        while len(heartbeat_calls) < 3:
            await asyncio.sleep(0.01)
        stop.set()

    await asyncio.gather(
        worker._heartbeat_loop("job-x", "worker-1", stop, run_task),
        _stop_after_a_few_beats(),
    )

    assert len(heartbeat_calls) >= 3
    assert not run_task.cancelled()
    run_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await run_task
