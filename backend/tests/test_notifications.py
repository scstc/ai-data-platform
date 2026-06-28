"""站内通知中心:服务查询 / 标记已读 / 越权隔离 / API + Job 终态通知(DB-backed)。

为什么放在 tests/ 顶层而非 tests/unit/:这些用例需要真实 SQL(list/count/mark)与
ASGI client + 鉴权 cookie,依赖 TEST_DATABASE_URL;tests/unit/ 按既有约定只放纯单测
(FakeSession,见 tests/unit/test_notifications.py 覆盖 emit / 采集终态埋点)。

锁的意图:
- ``list_for`` 按创建时间倒序 + 分页;``unread_count`` / ``mark_read`` 幂等 /
  ``mark_all_read`` 只影响目标 recipient。
- 越权:用户 A 看不到 / 标不了用户 B 的通知(recipient 强隔离)。
- Job 终态:success / failed 各产**恰好一条**通知(recipient=created_by、level 正确);
  cancelled / paused **不产**通知(主动操作不打扰自己)。
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.dataset import Dataset
from app.models.dataset_version import DatasetVersion
from app.models.job import Job
from app.models.notification import Notification
from app.services import job_runner, notifications
from app.services.engine import EngineError

pytestmark = pytest.mark.asyncio

VERSION_ID = "dsv-ntf1"
DATASET_ID = "dset-ntf1"
OPERATORS = [{"name": "text_length_filter", "params": {"min_len": 5}}]


async def _count(session_factory: async_sessionmaker, recipient: str) -> int:
    async with session_factory() as s:
        return (
            await s.scalar(
                select(func.count())
                .select_from(Notification)
                .where(Notification.recipient == recipient)
            )
        ) or 0


# ---------------------------------------------------------------------------
# service:list_for / unread_count / mark_read / mark_all_read / 隔离
# ---------------------------------------------------------------------------


class TestNotificationService:
    async def test_list_newest_first_and_paginates(
        self, session_factory: async_sessionmaker
    ) -> None:
        base = datetime(2026, 6, 28, 10, 0, 0)
        async with session_factory() as s:
            for i in range(3):
                s.add(
                    Notification(
                        id=f"ntf-a{i}",
                        recipient="alice",
                        level="success",
                        source_type="job",
                        source_id=f"job-{i}",
                        title=f"t{i}",
                        read=False,
                        created_at=base + timedelta(minutes=i),
                    )
                )
            await s.commit()

        async with session_factory() as s:
            rows, total = await notifications.list_for(
                s, "alice", only_unread=False, page=1, page_size=2
            )
        assert total == 3
        assert [r.id for r in rows] == ["ntf-a2", "ntf-a1"]  # 倒序 + 分页

    async def test_unread_count_and_only_unread_filter(
        self, session_factory: async_sessionmaker
    ) -> None:
        async with session_factory() as s:
            s.add_all(
                [
                    Notification(
                        id="ntf-u1", recipient="alice", level="success",
                        source_type="job", source_id="j1", title="t", read=False,
                    ),
                    Notification(
                        id="ntf-u2", recipient="alice", level="error",
                        source_type="job", source_id="j2", title="t", read=True,
                    ),
                ]
            )
            await s.commit()

        async with session_factory() as s:
            assert await notifications.unread_count(s, "alice") == 1
            rows, total = await notifications.list_for(
                s, "alice", only_unread=True, page=1, page_size=20
            )
        assert total == 1 and [r.id for r in rows] == ["ntf-u1"]

    async def test_mark_read_idempotent(
        self, session_factory: async_sessionmaker
    ) -> None:
        async with session_factory() as s:
            s.add(
                Notification(
                    id="ntf-m1", recipient="alice", level="success",
                    source_type="job", source_id="j1", title="t", read=False,
                )
            )
            await s.commit()

        async with session_factory() as s:
            assert await notifications.mark_read(s, "alice", "ntf-m1") is True
        async with session_factory() as s:
            n = await s.get(Notification, "ntf-m1")
            assert n.read is True and n.read_at is not None
            first_read_at = n.read_at
        # 再标一次:仍 True,read_at 不被重置(幂等)
        async with session_factory() as s:
            assert await notifications.mark_read(s, "alice", "ntf-m1") is True
        async with session_factory() as s:
            assert (await s.get(Notification, "ntf-m1")).read_at == first_read_at

    async def test_mark_all_read_only_target_recipient(
        self, session_factory: async_sessionmaker
    ) -> None:
        async with session_factory() as s:
            s.add_all(
                [
                    Notification(
                        id="ntf-x1", recipient="alice", level="success",
                        source_type="job", source_id="j1", title="t", read=False,
                    ),
                    Notification(
                        id="ntf-x2", recipient="alice", level="success",
                        source_type="job", source_id="j2", title="t", read=False,
                    ),
                    Notification(
                        id="ntf-y1", recipient="bob", level="success",
                        source_type="job", source_id="j3", title="t", read=False,
                    ),
                ]
            )
            await s.commit()

        async with session_factory() as s:
            updated = await notifications.mark_all_read(s, "alice")
        assert updated == 2
        async with session_factory() as s:
            assert await notifications.unread_count(s, "alice") == 0
            assert await notifications.unread_count(s, "bob") == 1  # bob 不受影响

    async def test_cross_user_isolation_in_service(
        self, session_factory: async_sessionmaker
    ) -> None:
        """A 标 B 的通知 → 返回 False 且不改 B 的行(recipient 强隔离)。"""
        async with session_factory() as s:
            s.add(
                Notification(
                    id="ntf-b1", recipient="bob", level="success",
                    source_type="job", source_id="j1", title="t", read=False,
                )
            )
            await s.commit()

        async with session_factory() as s:
            assert await notifications.mark_read(s, "alice", "ntf-b1") is False
        async with session_factory() as s:
            assert (await s.get(Notification, "ntf-b1")).read is False
            # A 的列表里看不到 B 的通知
            rows, total = await notifications.list_for(
                s, "alice", only_unread=False, page=1, page_size=20
            )
        assert total == 0 and rows == []


# ---------------------------------------------------------------------------
# API:list / unread-count / read / read-all + 越权
# ---------------------------------------------------------------------------


class TestNotificationApi:
    @pytest_asyncio.fixture(autouse=True)
    async def _seed_two_users(
        self, client: AsyncClient, seed_users: None, session_factory: async_sessionmaker
    ) -> None:
        """两个种子用户 admin/user,各一条通知,用于隔离断言。"""
        async with session_factory() as s:
            s.add_all(
                [
                    Notification(
                        id="ntf-adm", recipient="admin", level="success",
                        source_type="job", source_id="job-a", title="管理员的",
                        read=False,
                    ),
                    Notification(
                        id="ntf-usr", recipient="user", level="error",
                        source_type="ingest_task", source_id="task-u",
                        title="普通用户的", body="err", read=False,
                    ),
                ]
            )
            await s.commit()

    def _login(self, client: AsyncClient, username: str) -> None:
        from app.services.auth import sign_token

        client.cookies.set("adp_session", sign_token(username))

    async def test_list_returns_only_own(self, client: AsyncClient) -> None:
        self._login(client, "admin")
        resp = await client.get("/api/v1/notifications")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["success"] is True and body["total"] == 1
        item = body["data"][0]
        assert item["id"] == "ntf-adm"
        # camelCase 输出 + 不暴露 recipient
        assert item["sourceType"] == "job" and item["sourceId"] == "job-a"
        assert "recipient" not in item
        assert "createdAt" in item and item["read"] is False

    async def test_unread_count_endpoint(self, client: AsyncClient) -> None:
        self._login(client, "user")
        resp = await client.get("/api/v1/notifications/unread-count")
        assert resp.status_code == 200
        assert resp.json() == {"count": 1, "success": True}

    async def test_read_other_users_notification_404(
        self, client: AsyncClient
    ) -> None:
        """admin 标 user 的通知 → 404(越权隔离),且该通知仍未读。"""
        self._login(client, "admin")
        resp = await client.post("/api/v1/notifications/ntf-usr/read")
        assert resp.status_code == 404
        # 仍未读
        self._login(client, "user")
        assert (
            await client.get("/api/v1/notifications/unread-count")
        ).json()["count"] == 1

    async def test_read_and_read_all(self, client: AsyncClient) -> None:
        self._login(client, "admin")
        r1 = await client.post("/api/v1/notifications/ntf-adm/read")
        assert r1.status_code == 200 and r1.json() == {"success": True}
        assert (
            await client.get("/api/v1/notifications/unread-count")
        ).json()["count"] == 0

        r2 = await client.post("/api/v1/notifications/read-all")
        assert r2.status_code == 200
        assert r2.json()["success"] is True and r2.json()["updated"] == 0

    async def test_requires_login(self, client: AsyncClient) -> None:
        client.cookies.delete("adp_session")
        assert (await client.get("/api/v1/notifications")).status_code == 401


# ---------------------------------------------------------------------------
# Job 终态通知:success / failed 产一条;cancelled / paused 不产
# ---------------------------------------------------------------------------


async def _seed_version(session_factory: async_sessionmaker) -> None:
    async with session_factory() as s:
        s.add(Dataset(id=DATASET_ID, name="通知测试集"))
        s.add(
            DatasetVersion(
                id=VERSION_ID,
                dataset_id=DATASET_ID,
                version_no=1,
                storage_uri="/data/x.jsonl",
                format="jsonl",
            )
        )
        await s.commit()


async def _seed_job(
    session_factory: async_sessionmaker, job_id: str, created_by: str
) -> None:
    async with session_factory() as s:
        s.add(
            Job(
                id=job_id,
                name="清洗任务",
                type="clean",
                state="pending",
                progress=0,
                created_by=created_by,
                spec={
                    "name": "清洗任务",
                    "type": "clean",
                    "datasetVersionId": VERSION_ID,
                    "operators": OPERATORS,
                },
            )
        )
        await s.commit()


class TestJobTerminalNotification:
    async def test_success_emits_one_notification(
        self, session_factory: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await _seed_version(session_factory)
        await _seed_job(session_factory, "job-ok", created_by="alice")

        async def fake_run(session, *, job_id, input_version, operators):
            return None, "process: []", "/tmp/run.log"

        monkeypatch.setattr(job_runner, "run_process_job", fake_run)
        await job_runner._run_job("job-ok")

        async with session_factory() as s:
            rows = (
                await s.scalars(
                    select(Notification).where(Notification.source_id == "job-ok")
                )
            ).all()
        assert len(rows) == 1
        assert rows[0].recipient == "alice"
        assert rows[0].level == "success"
        assert rows[0].source_type == "job"
        assert rows[0].body is None

    async def test_failed_emits_error_notification(
        self, session_factory: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await _seed_version(session_factory)
        await _seed_job(session_factory, "job-bad", created_by="alice")

        async def boom_run(session, *, job_id, input_version, operators):
            raise EngineError("子进程崩了")

        monkeypatch.setattr(job_runner, "run_process_job", boom_run)
        await job_runner._run_job("job-bad")

        async with session_factory() as s:
            job = await s.get(Job, "job-bad")
            rows = (
                await s.scalars(
                    select(Notification).where(Notification.source_id == "job-bad")
                )
            ).all()
        assert job.state == "failed"
        assert len(rows) == 1
        assert rows[0].level == "error"
        assert "子进程崩了" in (rows[0].body or "")

    async def test_cancelled_emits_no_notification(
        self, session_factory: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await _seed_version(session_factory)
        await _seed_job(session_factory, "job-cxl", created_by="alice")

        async def cancel_then_fail(session, *, job_id, input_version, operators):
            job_runner.request_cancel(job_id)  # 运行中被请求停止
            raise EngineError("killed")

        monkeypatch.setattr(job_runner, "run_process_job", cancel_then_fail)
        await job_runner._run_job("job-cxl")

        async with session_factory() as s:
            job = await s.get(Job, "job-cxl")
        assert job.state == "cancelled"
        assert await _count(session_factory, "alice") == 0  # 不通知主动取消

    async def test_paused_emits_no_notification(
        self, session_factory: async_sessionmaker, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        await _seed_version(session_factory)
        await _seed_job(session_factory, "job-pause", created_by="alice")

        async def pause_then_fail(session, *, job_id, input_version, operators):
            job_runner.request_pause(job_id)  # 运行中被请求暂停
            raise EngineError("killed")

        monkeypatch.setattr(job_runner, "run_process_job", pause_then_fail)
        await job_runner._run_job("job-pause")

        async with session_factory() as s:
            job = await s.get(Job, "job-pause")
        assert job.state == "paused"
        assert await _count(session_factory, "alice") == 0
