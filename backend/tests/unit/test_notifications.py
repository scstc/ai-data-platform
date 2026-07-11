"""站内通知:emit 埋点 + 采集任务终态通知(纯单测,FakeSession,不连 DB)。

锁的意图:
- ``emit`` 构造恰好一行 Notification 并 ``add`` 到传入 session(不 commit),字段映射正确。
- ``emit`` 内部异常**只 loud log、不向上抛**——通知是旁路,绝不能让已成功的任务被判失败。
- ``_execute_ingest`` 在 success / 质量门 failed / ingest_error 三条终态汇合点
  **各发一条**通知(source_type=ingest_task、recipient=task.creator、level 正确),
  且该 Job(type=ingest)不经 job_runner,不会重复发。

DB 相关的查询 / 标记已读 / 越权隔离 / Job 终态通知见 ``tests/test_notifications.py``
(需 TEST_DATABASE_URL,本目录按既有约定只放纯单测)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app.models.dataset import Dataset
from app.models.dataset_version import DatasetVersion
from app.models.datasource import DataSource
from app.models.ingest_task import IngestTask
from app.models.job import Job
from app.models.notification import Notification

# ---------------------------------------------------------------------------
# fakes(纯内存,避开 PG;与 test_execute_ingest 同构)
# ---------------------------------------------------------------------------


@dataclass
class FakeTask:
    id: str = "task-fake01"
    name: str = "测试任务"
    datasource_id: str = "ds-fake01"
    creator: str = "alice"
    last_run_at: datetime | None = None
    logs: list[str] = field(default_factory=lambda: ["[INFO] 任务已创建"])
    status: str = "pending"
    progress: int = 0
    run_count: int = 0
    quality_policy: dict | None = None
    # §4 整改后 _execute_ingest 读 task.lake_id 判定「入湖任务质量门跳过」;
    # 数据集直采任务(本 fake 场景)lake_id=None,不触发跳过分支。
    lake_id: str | None = None


@dataclass
class FakeDatasource:
    id: str = "ds-fake01"
    name: str = "测试源"
    type: str = "database"
    db_kind: str = "postgresql"


class _FakeConnector:
    """假连接器:run_ingest 返回注入的 results(默认空→成功路径)。"""

    def __init__(self, results=None, exc: Exception | None = None):
        self._results = results or []
        self._exc = exc

    async def run_ingest(self, session, task, datasource, *, job_id):
        if self._exc is not None:
            raise self._exc
        return self._results


class FakeSession:
    """仿 AsyncSession:记录 add();get() 按注入返回;commit/refresh no-op。"""

    def __init__(self, *, task=None, datasource=None) -> None:
        self.added: list[Any] = []
        self._task = task
        self._datasource = datasource

    def add(self, obj: Any) -> None:
        self.added.append(obj)

    async def commit(self) -> None:
        pass

    async def refresh(self, _obj: Any) -> None:
        pass

    async def get(self, model_cls, id_):
        if model_cls is IngestTask and self._task is not None:
            return self._task
        if model_cls is DataSource and self._datasource is not None:
            return self._datasource
        return None


def _patch_resolve(monkeypatch, connector: _FakeConnector) -> None:
    import app.api.v1.ingest_tasks as route_mod

    monkeypatch.setattr(route_mod, "resolve", lambda *a, **kw: connector)


def _notifs(session: FakeSession) -> list[Notification]:
    return [o for o in session.added if isinstance(o, Notification)]


# ---------------------------------------------------------------------------
# emit:字段映射 + 不 commit + 旁路吞异常
# ---------------------------------------------------------------------------


class TestEmit:
    def test_builds_one_notification_with_fields(self) -> None:
        """emit 构造恰好一行,字段按 kwargs 映射,read 初始 False,不 commit。"""
        from app.services import notifications

        session = FakeSession()
        notifications.emit(
            session,
            recipient="bob",
            level="error",
            source_type="job",
            source_id="job-x1",
            title="清洗任务 失败",
            body="boom",
        )
        rows = _notifs(session)
        assert len(rows) == 1
        n = rows[0]
        assert n.id.startswith("ntf-")
        assert n.recipient == "bob"
        assert n.level == "error"
        assert n.source_type == "job"
        assert n.source_id == "job-x1"
        assert n.title == "清洗任务 失败"
        assert n.body == "boom"
        assert n.read is False

    def test_swallows_internal_error_loudly(self, monkeypatch, caplog) -> None:
        """emit 内部异常被吞(不向上抛)且 loud log——通知失败不得污染任务终态。"""
        from app.services import notifications

        def _boom(*_a, **_kw):
            raise RuntimeError("db exploded")

        monkeypatch.setattr(notifications, "Notification", _boom)
        session = FakeSession()
        with caplog.at_level("ERROR"):
            # 不抛即为通过
            notifications.emit(
                session,
                recipient="bob",
                level="success",
                source_type="job",
                source_id="job-x1",
                title="t",
            )
        assert _notifs(session) == []
        assert any("站内通知" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# _execute_ingest 终态通知(success / quality-fail / ingest-error)
# ---------------------------------------------------------------------------


def _make_version(quality_stats: dict) -> tuple[Dataset, DatasetVersion]:
    ds = Dataset(
        id="dset-n1",
        name="N",
        data_type="sql",
        semantic_type="structured",
        creator="alice",
    )
    ver = DatasetVersion(
        id="dsv-n1",
        dataset_id=ds.id,
        version_no=1,
        storage_uri="file://fake",
        format="jsonl",
        rows=5,
        size=50,
        origin="managed",
        semantic_type="structured",
        quality_stats=quality_stats,
    )
    return ds, ver


class TestIngestTerminalNotification:
    async def test_success_emits_one_success_notification(self, monkeypatch) -> None:
        """采集成功 → 一条 ingest_task 通知,recipient=creator、level=success。"""
        from app.api.v1.ingest_tasks import _execute_ingest

        _patch_resolve(monkeypatch, _FakeConnector(results=[]))
        task = FakeTask()
        ds = FakeDatasource()
        session = FakeSession(task=task, datasource=ds)

        await _execute_ingest(session, task, ds, trigger="manual")

        rows = _notifs(session)
        assert len(rows) == 1
        assert rows[0].source_type == "ingest_task"
        assert rows[0].source_id == task.id
        assert rows[0].recipient == "alice"
        assert rows[0].level == "success"
        assert rows[0].body is None
        assert task.status == "success"

    async def test_quality_fail_emits_error_notification(self, monkeypatch) -> None:
        """质量门未过 → task.status=failed,通知 level=error 且 body 带原因。"""
        from app.api.v1.ingest_tasks import _execute_ingest

        ds_model, ver = _make_version({"rows": 5, "columns": []})
        _patch_resolve(monkeypatch, _FakeConnector(results=[(ds_model, ver)]))

        # 强制质量门判 failed
        import app.services.ingest_quality as iq

        monkeypatch.setattr(
            iq, "evaluate_policy", lambda *a, **kw: ("failed", "空值率超阈值")
        )
        task = FakeTask(quality_policy={"maxNullRate": 0.1})
        ds = FakeDatasource()
        session = FakeSession(task=task, datasource=ds)

        await _execute_ingest(session, task, ds, trigger="manual")

        rows = _notifs(session)
        assert len(rows) == 1
        assert task.status == "failed"
        assert rows[0].level == "error"
        assert rows[0].recipient == "alice"
        assert "空值率超阈值" in (rows[0].body or "")

    async def test_ingest_error_emits_error_notification(self, monkeypatch) -> None:
        """连接器抛 IngestError → task.status=failed,通知 level=error、body=错误串。"""
        from app.api.v1.ingest_tasks import _execute_ingest
        from app.services.connectors.base import IngestError

        _patch_resolve(
            monkeypatch, _FakeConnector(exc=IngestError("连接超时"))
        )
        task = FakeTask()
        ds = FakeDatasource()
        session = FakeSession(task=task, datasource=ds)

        await _execute_ingest(session, task, ds, trigger="cron")

        rows = _notifs(session)
        assert len(rows) == 1
        assert task.status == "failed"
        assert rows[0].level == "error"
        assert "连接超时" in (rows[0].body or "")

    async def test_emit_failure_does_not_break_terminal_state(
        self, monkeypatch
    ) -> None:
        """emit 内部炸了也不影响采集终态:task.status 仍 success 且不抛。"""
        from app.api.v1.ingest_tasks import _execute_ingest

        _patch_resolve(monkeypatch, _FakeConnector(results=[]))

        # 让 emit 内部构造 Notification 时抛 → emit 自吞,调用方无感
        import app.services.notifications as notif_mod

        def _boom(*_a, **_kw):
            raise RuntimeError("notify exploded")

        monkeypatch.setattr(notif_mod, "Notification", _boom)
        task = FakeTask()
        ds = FakeDatasource()
        session = FakeSession(task=task, datasource=ds)

        # 不抛
        await _execute_ingest(session, task, ds, trigger="manual")
        assert task.status == "success"
        assert _notifs(session) == []  # 通知没落,但任务终态完好

    async def test_single_notification_per_run(self, monkeypatch) -> None:
        """一次采集运行只产一条通知(防与 job_runner 重复埋点的回归)。"""
        from app.api.v1.ingest_tasks import _execute_ingest

        _patch_resolve(monkeypatch, _FakeConnector(results=[]))
        task = FakeTask()
        ds = FakeDatasource()
        session = FakeSession(task=task, datasource=ds)

        await _execute_ingest(session, task, ds, trigger="manual")
        assert len(_notifs(session)) == 1
        # 也只建了一条 type=ingest 的 Job(该 Job 不经 job_runner)
        assert len([o for o in session.added if isinstance(o, Job)]) == 1
