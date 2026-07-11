"""job_runner._run_job 终态提交兜底(纯单测,FakeSession,不连 DB)。

锁的意图:业务异常(如某 runner 内部 flush 冲突)可能让当前会话的事务处于
aborted 态;此时即便已把 ``job.state = "failed"`` 写进 ORM 对象,最终
``await session.commit()`` 仍会因事务失效而再次抛错(真实场景是
``PendingRollbackError``,这里用 ``IntegrityError`` 模拟同一类"提交失败")。
若没有兜底,这次异常会从 ``_run_job`` 冒泡出去——终态永远没有落库,任务卡在
running 且没人能再把它捞回来(重启回收只认 pending/running,不会主动重跑)。

修复要求:终态提交失败时,不能让异常继续冒泡;必须 rollback 失效会话,换一个
全新会话把终态(尤其是 error,原始业务异常信息)重新写一遍。本测试通过注入
两个不同的 session(主 session commit 第二次调用故意失败,新 session commit
正常)来断言:即使主 session 的终态提交失败,fresh session 里的 Job 行也必须
被改写为 failed + 原始错误文本,且 `_run_job` 本身不能把异常抛给调用方
(它是 fire-and-forget 的后台协程,抛出去只会变成"任务异常从未被处理"的日志,
调用方永远等不到结果)。
"""

from __future__ import annotations

from datetime import datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.dataset_version import DatasetVersion
from app.models.job import Job
from app.services import job_runner
from app.services.capabilities import Capabilities
from app.services.engine import EngineError

JOB_ID = "job-term1"
VERSION_ID = "dsv-term1"


class _AsyncCtx:
    """把一个假 session 包成 `async with async_session_factory() as s` 期望的形状。"""

    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb):
        return False


class FakeSession:
    """仿 AsyncSession:get 按 model 类型分流;commit 可按序注入失败。"""

    def __init__(self, *, job=None, input_version=None, commit_effects=None):
        self._job = job
        self._input_version = input_version
        self._commit_effects = list(commit_effects or [])
        self.commit_calls = 0
        self.rollback_calls = 0

    def add(self, _obj) -> None:
        pass

    async def get(self, model_cls, _ident):
        if model_cls is Job:
            return self._job
        if model_cls is DatasetVersion:
            return self._input_version
        return None

    async def commit(self) -> None:
        self.commit_calls += 1
        if self._commit_effects:
            effect = self._commit_effects.pop(0)
            if effect is not None:
                raise effect

    async def rollback(self) -> None:
        self.rollback_calls += 1

    async def refresh(self, _obj) -> None:
        pass


def _make_job() -> Job:
    return Job(
        id=JOB_ID,
        name="终态提交兜底测试",
        type="clean",
        state="pending",
        created_by="alice",
        spec={
            "name": "终态提交兜底测试",
            "dataset_version_id": VERSION_ID,
            "operators": [{"name": "text_length_filter", "params": {"min_len": 5}}],
        },
    )


def _make_input_version() -> DatasetVersion:
    return DatasetVersion(
        id=VERSION_ID,
        dataset_id="dset-term1",
        version_no=1,
        storage_uri="s3://bucket/dset-term1/v1/data.jsonl",
        format="jsonl",
        rows=10,
        size=100,
    )


@pytest.mark.asyncio
async def test_terminal_state_survives_aborted_commit(monkeypatch) -> None:
    """runner 抛业务异常 → 首次(running)commit 成功,终态 commit 失败(模拟事务
    aborted)→ 必须 rollback 主 session 并换新 session 把 failed + 原始错误文本
    重写进去,且 _run_job 不得把这次提交失败异常继续往外抛。
    """
    job = _make_job()
    input_version = _make_input_version()
    # 首次 commit(落 running 态 + llm_snapshot)成功;终态 commit 失败一次。
    main_session = FakeSession(
        job=job,
        input_version=input_version,
        commit_effects=[None, IntegrityError("stmt", {}, Exception("aborted"))],
    )
    fresh_job = _make_job()  # 模拟数据库里现存的行,与内存中 job 是不同对象
    fresh_session = FakeSession(job=fresh_job)

    sessions = [main_session, fresh_session]

    def _factory():
        return _AsyncCtx(sessions.pop(0))

    monkeypatch.setattr(job_runner, "async_session_factory", _factory)
    monkeypatch.setattr(
        job_runner,
        "get_capabilities",
        lambda: Capabilities(cuda=False, vllm=False, ray=False, llm=False),
    )
    monkeypatch.setattr(job_runner, "get_dj_version", lambda: None)

    async def _fake_run_process_job(_session, **_kwargs):
        raise EngineError("处理失败:模拟版本冲突")

    monkeypatch.setattr(job_runner, "run_process_job", _fake_run_process_job)

    # 不抛:_run_job 是 fire-and-forget 后台协程,提交失败绝不能冒泡给调用方
    await job_runner._run_job(JOB_ID)

    # 主 session:业务异常已 rollback(为后续换新 session 铺路)
    assert main_session.rollback_calls == 1
    # 新 session:终态被重写为 failed,且带上原始业务异常文本(不是 commit 失败的文本)
    assert fresh_session.commit_calls == 1
    assert fresh_job.state == "failed"
    assert fresh_job.error == "处理失败:模拟版本冲突"
    assert isinstance(fresh_job.finished_at, datetime)


@pytest.mark.asyncio
async def test_terminal_commit_succeeds_without_fresh_session(monkeypatch) -> None:
    """对照组:终态 commit 一次成功时,不应该多此一举再开新 session 重写。"""
    job = _make_job()
    input_version = _make_input_version()
    main_session = FakeSession(job=job, input_version=input_version)

    sessions = [main_session]

    def _factory():
        return _AsyncCtx(sessions.pop(0))

    monkeypatch.setattr(job_runner, "async_session_factory", _factory)
    monkeypatch.setattr(
        job_runner,
        "get_capabilities",
        lambda: Capabilities(cuda=False, vllm=False, ray=False, llm=False),
    )
    monkeypatch.setattr(job_runner, "get_dj_version", lambda: None)

    async def _fake_run_process_job(_session, **_kwargs):
        return None, "process: []", "/tmp/run.log"

    monkeypatch.setattr(job_runner, "run_process_job", _fake_run_process_job)

    await job_runner._run_job(JOB_ID)

    assert job.state == "success"
    # 两次 commit:running 态一次 + 终态一次,均落在同一个 session 上
    assert main_session.commit_calls == 2
    assert not sessions  # factory 只被调用一次,没有换新 session
