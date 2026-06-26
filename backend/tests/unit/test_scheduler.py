"""scheduler.py 纯函数测试(切片 C / Task 2)。

覆盖:
- ``job_id_for``:task_id → ``ingest:{id}`` 映射(纯函数)。
- ``_sync_database_url``:``+asyncpg`` → ``+psycopg2`` URL 派生(纯字符串变换)。
- ``upsert_cron_job`` / ``remove_cron_job``:在 fake scheduler 上验证调用契约
  (id/trigger/args/replace_existing、容忍缺失)。
- ``reconcile``:用 fake scheduler + fake session 验证 add-missing /
  remove-orphan / leave-matched 三态对账,不启真实 AsyncIOScheduler。

DEFERRED(留待 .60 PG 恢复后回归):
- 真实 ``AsyncIOScheduler.start()``、``SQLAlchemyJobStore`` 建表、
  ``reconcile`` 对真 DB 扫描——当前 .60 测试库 ConnectionRefused,
  本地无法启动带 PG jobstore 的调度器。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services.scheduler import (
    JOB_ID_PREFIX,
    _sync_database_url,
    job_id_for,
    reconcile,
    remove_cron_job,
    upsert_cron_job,
)


# ---------- fakes ----------


@dataclass
class FakeJob:
    """仿 APScheduler Job,只暴露 reconcile 关心的字段(id)。"""

    id: str


@dataclass
class FakeScheduler:
    """仿 AsyncIOScheduler,记录 add_job/remove_job 调用。

    不构造真实 AsyncIOScheduler(那会需要 PG jobstore)。get_jobs() 返回
    当前作业集合,reconcile 据此 diff。
    """

    jobs: dict[str, FakeJob] = field(default_factory=dict)
    added: list[tuple[str, Any, tuple]] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)

    def get_jobs(self) -> list[FakeJob]:
        return list(self.jobs.values())

    def add_job(
        self,
        func,
        *,
        trigger=None,
        args=None,
        id=None,
        replace_existing=False,  # noqa: ARG002
    ) -> None:
        self.added.append((id, trigger, tuple(args or ())))
        self.jobs[id] = FakeJob(id=id)

    def remove_job(self, job_id: str) -> None:
        if job_id not in self.jobs:
            # 与 APScheduler 3.x 一致的错误类型,reconcile 据此 tolerant skip
            from apscheduler.jobstores.base import JobLookupError

            raise JobLookupError(job_id)
        self.removed.append(job_id)
        del self.jobs[job_id]


@dataclass
class FakeTask:
    """仿 IngestTask ORM 行(只暴露 reconcile/upsert 读取的字段)。"""

    id: str
    schedule: dict[str, Any] | None = None


class _FakeScalars:
    """仿 SQLAlchemy ScalarResult:可迭代(生产代码直接 for t in result.scalars())
    也有 .all()(部分路径用)。"""

    def __init__(self, tasks: list[FakeTask]) -> None:
        self._tasks = tasks

    def __iter__(self):
        return iter(self._tasks)

    def all(self) -> list[FakeTask]:
        return list(self._tasks)


class _FakeResult:
    def __init__(self, tasks: list[FakeTask]) -> None:
        self._tasks = tasks

    def scalars(self) -> _FakeScalars:
        return _FakeScalars(self._tasks)


class FakeSession:
    """仿 AsyncSession:execute() 忽略 SQL,直接返回构造时注入的 task 列表。"""

    def __init__(self, tasks: list[FakeTask]) -> None:
        self._tasks = tasks

    async def execute(self, *_args, **_kw) -> _FakeResult:
        return _FakeResult(self._tasks)


# ---------- job_id_for(纯函数) ----------


class TestJobIdFor:
    def test_plain_id_gets_ingest_prefix(self) -> None:
        assert job_id_for("task-abc") == "ingest:task-abc"

    def test_id_with_special_chars_preserved(self) -> None:
        assert job_id_for("task-1_2.3") == "ingest:task-1_2.3"

    def test_prefix_constant_value(self) -> None:
        assert JOB_ID_PREFIX == "ingest:"


# ---------- _sync_database_url(纯字符串变换) ----------


class TestSyncDatabaseUrl:
    def test_strips_asyncpg_to_psycopg2(self) -> None:
        url = _sync_database_url("postgresql+asyncpg://u:p@host:5432/db")
        assert url == "postgresql+psycopg2://u:p@host:5432/db"

    def test_passes_through_plain_postgres(self) -> None:
        url = _sync_database_url("postgresql://u:p@h/db")
        assert url == "postgresql://u:p@h/db"

    def test_preserves_query(self) -> None:
        url = _sync_database_url(
            "postgresql+asyncpg://u:p@h/db?sslmode=require"
        )
        assert url == "postgresql+psycopg2://u:p@h/db?sslmode=require"

    def test_non_pg_scheme_unchanged(self) -> None:
        # 非 postgresql scheme 不应误改(sqlite/mysql 走各自的同步驱动)
        url = _sync_database_url("mysql+pymysql://u:p@h/db")
        assert url == "mysql+pymysql://u:p@h/db"


# ---------- upsert_cron_job ----------


class TestUpsertCronJob:
    def test_adds_job_with_correct_id_trigger_and_args(self) -> None:
        sched = FakeScheduler()
        task = FakeTask(id="t1", schedule={"mode": "cron", "cron": "*/5 * * * *"})
        upsert_cron_job(sched, task)
        assert len(sched.added) == 1
        jid, trigger, args = sched.added[0]
        assert jid == "ingest:t1"
        assert args == ("t1",)
        from apscheduler.triggers.cron import CronTrigger

        assert isinstance(trigger, CronTrigger)

    def test_missing_cron_expr_skips_silently(self) -> None:
        sched = FakeScheduler()
        task = FakeTask(id="t2", schedule={"mode": "cron"})  # 无 cron 表达式
        upsert_cron_job(sched, task)
        assert sched.added == []
        assert sched.jobs == {}

    def test_replace_existing_semantics_idempotent(self) -> None:
        sched = FakeScheduler()
        task = FakeTask(id="t3", schedule={"mode": "cron", "cron": "0 * * * *"})
        upsert_cron_job(sched, task)
        upsert_cron_job(sched, task)
        # 第二次同样是 add_job(replace_existing=True):契约稳定性
        assert len(sched.added) == 2
        assert [a[0] for a in sched.added] == ["ingest:t3", "ingest:t3"]


# ---------- remove_cron_job ----------


class TestRemoveCronJob:
    def test_removes_existing_job(self) -> None:
        sched = FakeScheduler()
        sched.jobs["ingest:t1"] = FakeJob(id="ingest:t1")
        remove_cron_job(sched, "t1")
        assert "ingest:t1" not in sched.jobs
        assert sched.removed == ["ingest:t1"]

    def test_missing_job_tolerated_no_raise(self) -> None:
        sched = FakeScheduler()
        # 不抛:缺失 = 无操作
        remove_cron_job(sched, "never-existed")
        assert sched.removed == []


# ---------- reconcile diff ----------


class TestReconcile:
    async def test_adds_missing_jobs_for_cron_tasks(self) -> None:
        sched = FakeScheduler()
        session = FakeSession(
            [FakeTask(id="t1", schedule={"mode": "cron", "cron": "*/5 * * * *"})]
        )
        added, removed = await reconcile(session, sched)
        assert added == 1
        assert removed == 0
        assert "ingest:t1" in sched.jobs

    async def test_removes_orphan_jobs(self) -> None:
        sched = FakeScheduler()
        sched.jobs["ingest:orphan"] = FakeJob(id="ingest:orphan")
        session = FakeSession([])  # DB 里无 cron 任务
        added, removed = await reconcile(session, sched)
        assert added == 0
        assert removed == 1
        assert "ingest:orphan" not in sched.jobs

    async def test_matched_jobs_left_untouched(self) -> None:
        """matched:既不重复 add_job(避免无谓重写 trigger),也不 remove。"""
        sched = FakeScheduler()
        sched.jobs["ingest:t1"] = FakeJob(id="ingest:t1")
        session = FakeSession(
            [FakeTask(id="t1", schedule={"mode": "cron", "cron": "*/5 * * * *"})]
        )
        added, removed = await reconcile(session, sched)
        assert added == 0
        assert removed == 0
        assert sched.added == []  # 没有重复 add_job
        assert sched.removed == []
        assert "ingest:t1" in sched.jobs  # 原作业保留

    async def test_once_mode_tasks_ignored(self) -> None:
        sched = FakeScheduler()
        session = FakeSession([FakeTask(id="t1", schedule={"mode": "once"})])
        added, removed = await reconcile(session, sched)
        assert added == 0
        assert removed == 0
        assert sched.added == []

    async def test_mixed_scenario(self) -> None:
        sched = FakeScheduler()
        sched.jobs["ingest:matched"] = FakeJob(id="ingest:matched")
        sched.jobs["ingest:orphan"] = FakeJob(id="ingest:orphan")
        session = FakeSession(
            [
                FakeTask(id="matched", schedule={"mode": "cron", "cron": "0 * * * *"}),
                FakeTask(id="new", schedule={"mode": "cron", "cron": "0 0 * * *"}),
                FakeTask(id="once", schedule={"mode": "once"}),
            ]
        )
        added, removed = await reconcile(session, sched)
        assert added == 1  # 仅 new
        assert removed == 1  # 仅 orphan
        assert "ingest:matched" in sched.jobs
        assert "ingest:new" in sched.jobs
        assert "ingest:orphan" not in sched.jobs
        assert "ingest:once" not in sched.jobs  # once 不应被建作业

    async def test_null_or_malformed_schedule_skipped(self) -> None:
        sched = FakeScheduler()
        session = FakeSession(
            [
                FakeTask(id="t-null", schedule=None),
                FakeTask(id="t-str", schedule="not-a-dict"),  # type: ignore[arg-type]
                FakeTask(id="t-empty", schedule={}),
            ]
        )
        added, removed = await reconcile(session, sched)
        assert added == 0
        assert removed == 0

    async def test_returns_summary_counts(self) -> None:
        """reconcile 返回 (added, removed) 计数元组,供运维观测。"""
        sched = FakeScheduler()
        sched.jobs["ingest:gone"] = FakeJob(id="ingest:gone")
        session = FakeSession(
            [FakeTask(id="fresh", schedule={"mode": "cron", "cron": "* * * * *"})]
        )
        result = await reconcile(session, sched)
        assert isinstance(result, tuple)
        assert len(result) == 2
        added, removed = result
        assert added == 1
        assert removed == 1
