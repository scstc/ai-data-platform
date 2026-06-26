"""_execute_ingest 共享执行核 + _trigger_ingest 触发函数测试(切片 C / Task 4)。

锁的意图:
- ``_execute_ingest(trigger="manual"|"cron")`` → 建的 Job 带 ``trigger`` 标签
  (manual: rerun 路径;cron: 调度器路径),其余状态机/质量门/日志行为与原 rerun
  一致。
- ``_trigger_ingest(task_id)`` 重叠跳过:该任务最近一条 Job 仍 ``running`` 时,
  **不创建新 Job**,日志「上次未完成,跳过本次调度」;否则调
  ``_execute_ingest(trigger="cron")``。

不依赖真 PG(`.60` DOWN):用 FakeSession + monkeypatch connector,经
``app.api.v1.ingest_tasks`` 模块级 ``resolve`` 注入。重叠跳过测试 spy
``_execute_ingest`` 的调用次数,无需真 DB 事务。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pytest

from app.models.datasource import DataSource
from app.models.dataset import Dataset
from app.models.dataset_version import DatasetVersion
from app.models.ingest_task import IngestTask
from app.models.job import Job


# ---------------------------------------------------------------------------
# fakes(纯内存,避开 PG)
# ---------------------------------------------------------------------------


@dataclass
class FakeTask:
    """仿 IngestTask ORM 行,暴露 _execute_ingest 读写的字段。"""

    id: str = "task-fake01"
    name: str = "测试任务"
    datasource_id: str = "ds-fake01"
    last_run_at: datetime | None = None
    logs: list[str] = field(default_factory=lambda: ["[INFO] 任务已创建"])
    status: str = "pending"
    progress: int = 0
    run_count: int = 0
    quality_policy: dict | None = None


@dataclass
class FakeDatasource:
    """仿 DataSource 行,仅 _execute_ingest 读取 name/type/db_kind。"""

    id: str = "ds-fake01"
    name: str = "测试源"
    type: str = "database"
    db_kind: str = "postgresql"


class _FakeConnector:
    """假连接器:跳过真实拉取,run_ingest 直接返回空结果列表(无版本)。

    空结果 → quality_policy 循环不执行 → 无质量门违例 → task.status=success。
    用于隔离 _execute_ingest 的状态机与 trigger 透传,不关心拉取本身。
    """

    def __init__(self, results=None):
        self._results = results or []
        self.calls: list[dict[str, Any]] = []

    async def run_ingest(self, session, task, datasource, *, job_id):
        self.calls.append(
            {"task_id": task.id, "datasource_id": datasource.id, "job_id": job_id}
        )
        return self._results


class FakeSession:
    """仿 AsyncSession,记录 add() 调用 + 配置 get()/scalar() 返回值。

    - ``add(obj)`` 累积到 ``added``,供断言 Job 创建/trigger 标签;
    - ``await get(IngestTask|DataSource, id)`` 返回构造时注入的对应对象;
    - ``await scalar(...)`` 返回注入的 ``latest_job``(_trigger_ingest 重叠跳过判定);
    - ``commit/refresh`` 为 no-op。
    """

    def __init__(
        self,
        *,
        task: FakeTask | IngestTask | None = None,
        datasource: FakeDatasource | DataSource | None = None,
        latest_job: Job | None = None,
    ) -> None:
        self.added: list[Any] = []
        self._task = task
        self._datasource = datasource
        self._latest_job = latest_job
        self.commit_count = 0

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        self.commit_count += 1

    async def refresh(self, _obj: Any) -> None:
        pass

    async def get(self, model_cls, id_):
        if model_cls is IngestTask and self._task is not None and self._task.id == id_:
            return self._task
        if (
            model_cls is DataSource
            and self._datasource is not None
            and self._datasource.id == id_
        ):
            return self._datasource
        return None

    async def scalar(self, _stmt):
        return self._latest_job


def _patch_resolve_returns(
    monkeypatch: pytest.MonkeyPatch, connector: _FakeConnector
) -> None:
    """让 ingest_tasks.resolve(...) 返回注入的 connector(同 rerun 测试)。"""
    import app.api.v1.ingest_tasks as route_mod

    monkeypatch.setattr(route_mod, "resolve", lambda *a, **kw: connector)


# ---------------------------------------------------------------------------
# _execute_ingest(trigger=...) — trigger 标签透传到 Job
# ---------------------------------------------------------------------------


class TestExecuteIngestTriggerTag:
    async def test_manual_trigger_tags_created_job(self, monkeypatch) -> None:
        """trigger="manual" → 建的 Job.trigger=="manual"。"""
        from app.api.v1.ingest_tasks import _execute_ingest

        connector = _FakeConnector(results=[])
        _patch_resolve_returns(monkeypatch, connector)

        task = FakeTask()
        ds = FakeDatasource()
        session = FakeSession(task=task, datasource=ds)

        await _execute_ingest(session, task, ds, trigger="manual")

        jobs = [j for j in session.added if isinstance(j, Job)]
        assert len(jobs) == 1, "应创建恰好一条 Job"
        assert jobs[0].trigger == "manual", "manual rerun 路径必须标记 trigger=manual"
        assert jobs[0].type == "ingest"
        assert jobs[0].state == "success"  # 空结果 → 无质量违例 → success
        assert task.status == "success"
        assert task.progress == 100

    async def test_cron_trigger_tags_created_job(self, monkeypatch) -> None:
        """trigger="cron" → 建的 Job.trigger=="cron"。"""
        from app.api.v1.ingest_tasks import _execute_ingest

        connector = _FakeConnector(results=[])
        _patch_resolve_returns(monkeypatch, connector)

        task = FakeTask()
        ds = FakeDatasource()
        session = FakeSession(task=task, datasource=ds)

        await _execute_ingest(session, task, ds, trigger="cron")

        jobs = [j for j in session.added if isinstance(j, Job)]
        assert len(jobs) == 1
        assert jobs[0].trigger == "cron", "调度器路径必须标记 trigger=cron"
        assert task.status == "success"

    async def test_returns_results_list_from_connector(self, monkeypatch) -> None:
        """_execute_ingest 返回 connector.run_ingest 的 results 列表。"""
        from app.api.v1.ingest_tasks import _execute_ingest

        # 构造一个落地版本(带 quality_stats 触发 policy 分支)
        ds_model = Dataset(
            id="dset-x1",
            name="X",
            data_type="sql",
            semantic_type="structured",
            creator="admin",
        )
        ver = DatasetVersion(
            id="dsv-x1",
            dataset_id=ds_model.id,
            version_no=1,
            storage_uri="file://fake",
            format="jsonl",
            rows=5,
            size=50,
            origin="managed",
            semantic_type="structured",
            quality_stats={"rows": 5, "columns": []},
        )
        connector = _FakeConnector(results=[(ds_model, ver)])
        _patch_resolve_returns(monkeypatch, connector)

        task = FakeTask()
        ds = FakeDatasource()
        session = FakeSession(task=task, datasource=ds)

        results = await _execute_ingest(session, task, ds, trigger="manual")

        assert len(results) == 1
        assert results[0][0].id == "dset-x1"
        assert results[0][1].rows == 5
        # 质量门默认 skipped(quality_policy=None) → success
        assert task.status == "success"

    async def test_state_machine_runs_count_and_finished_at(self, monkeypatch) -> None:
        """_execute_ingest 推进 run_count + 1、job.finished_at 被设。"""
        from app.api.v1.ingest_tasks import _execute_ingest

        connector = _FakeConnector(results=[])
        _patch_resolve_returns(monkeypatch, connector)

        task = FakeTask(run_count=3)
        ds = FakeDatasource()
        session = FakeSession(task=task, datasource=ds)

        await _execute_ingest(session, task, ds, trigger="cron")

        assert task.run_count == 4
        jobs = [j for j in session.added if isinstance(j, Job)]
        assert jobs[0].finished_at is not None


# ---------------------------------------------------------------------------
# _trigger_ingest(task_id) — 重叠跳过 + cron trigger 透传
# ---------------------------------------------------------------------------


def _patch_trigger_ingest_session_factory(
    monkeypatch: pytest.MonkeyPatch, session: FakeSession
) -> None:
    """让 scheduler 模块内 _trigger_ingest 用我们的 FakeSession(经 async with)。

    scheduler.py 在模块级 ``from app.core.db import async_session_factory`` —— 测试
    时 setattr 该模块属性即可注入(避免真连 PG)。
    """

    class _Factory:
        def __call__(self):
            return _AsyncCtxSession(session)

    class _AsyncCtxSession:
        def __init__(self, inner):
            self._inner = inner

        async def __aenter__(self):
            return self._inner

        async def __aexit__(self, *exc):
            return False

    import app.services.scheduler as sched_mod

    monkeypatch.setattr(sched_mod, "async_session_factory", _Factory())


def _patch_execute_ingest_spy(
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[list[dict[str, Any]], Any]:
    """用 spy 替换 scheduler 看到的 _execute_ingest(避免拉真连接器)。"""
    import app.api.v1.ingest_tasks as route_mod

    calls: list[dict[str, Any]] = []

    async def _spy(session, task, datasource, *, trigger):
        calls.append(
            {
                "task_id": task.id,
                "datasource_id": datasource.id,
                "trigger": trigger,
            }
        )
        return []

    # scheduler 内 lazy import 会从 route_mod 取符号 → 真模块上 setattr 即生效
    monkeypatch.setattr(route_mod, "_execute_ingest", _spy)
    return calls, _spy


class TestTriggerIngestOverlapSkip:
    async def test_skips_when_latest_job_running(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """任务最近一条 Job.state=running → 不调 _execute_ingest、不建新 Job。"""
        from app.services.scheduler import _trigger_ingest

        running_job = Job(
            id="job-running",
            name="t",
            type="ingest",
            ingest_task_id="task-fake01",
            state="running",
            trigger="cron",
            created_by="admin",
        )
        task = FakeTask()
        ds = FakeDatasource()
        session = FakeSession(task=task, datasource=ds, latest_job=running_job)
        _patch_trigger_ingest_session_factory(monkeypatch, session)
        calls, _ = _patch_execute_ingest_spy(monkeypatch)

        # _trigger_ingest 是 async(由 AsyncIOExecutor 在事件循环内 await)
        await _trigger_ingest("task-fake01")

        assert calls == [], "running 中应跳过,不调 _execute_ingest"
        # 不应创建任何 Job(spy 不创建,session 也无 add 调用)
        assert session.added == []

    async def test_proceeds_when_latest_job_success(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """任务最近一条 Job.state=success → 调 _execute_ingest(trigger=cron)。"""
        from app.services.scheduler import _trigger_ingest

        prior_job = Job(
            id="job-prior",
            name="t",
            type="ingest",
            ingest_task_id="task-fake01",
            state="success",
            trigger="cron",
            created_by="admin",
        )
        task = FakeTask()
        ds = FakeDatasource()
        session = FakeSession(task=task, datasource=ds, latest_job=prior_job)
        _patch_trigger_ingest_session_factory(monkeypatch, session)
        calls, _ = _patch_execute_ingest_spy(monkeypatch)

        await _trigger_ingest("task-fake01")

        assert len(calls) == 1
        assert calls[0]["trigger"] == "cron"
        assert calls[0]["task_id"] == "task-fake01"

    async def test_proceeds_when_no_prior_job(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """任务无历史 Job → 调 _execute_ingest(trigger=cron)。"""
        from app.services.scheduler import _trigger_ingest

        task = FakeTask()
        ds = FakeDatasource()
        session = FakeSession(task=task, datasource=ds, latest_job=None)
        _patch_trigger_ingest_session_factory(monkeypatch, session)
        calls, _ = _patch_execute_ingest_spy(monkeypatch)

        await _trigger_ingest("task-fake01")

        assert len(calls) == 1
        assert calls[0]["trigger"] == "cron"

    async def test_skips_task_not_found(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """task_id 在 DB 中不存在 → 安全跳过(不抛、不调 _execute_ingest)。"""
        from app.services.scheduler import _trigger_ingest

        # FakeSession.get 返回 None(task/datasource 都没注入)
        session = FakeSession(task=None, datasource=None, latest_job=None)
        _patch_trigger_ingest_session_factory(monkeypatch, session)
        calls, _ = _patch_execute_ingest_spy(monkeypatch)

        # 不应抛
        await _trigger_ingest("task-nope")
        assert calls == []
