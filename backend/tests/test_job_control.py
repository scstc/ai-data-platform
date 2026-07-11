"""加工任务执行控制契约:后台执行 + 停止(POST /jobs/{id}/stop)+ 超时 + 重启回收。

为什么:加工任务改为后台异步执行后,才可能在「运行中」对它做停止。停止 = 杀掉
dj-process 子进程并把任务记为 cancelled(非 failed);超时是防卡死的安全网;进程
重启会丢失内存里的后台任务,残留 running 的任务须在启动时回收为 failed。

子进程层打桩,不跑真实 data-juicer;用 job_runner.drain() 等后台任务落定后断言。
"""

from __future__ import annotations

import asyncio
import signal
from pathlib import Path

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import settings
from app.models.dataset import Dataset
from app.models.dataset_version import DatasetVersion
from app.models.job import Job
from app.services import engine, job_runner
from app.services.engine import EngineError

DATASET_ID = "dset-c1"
VERSION_ID = "dsv-c1"
OPERATORS = [{"name": "text_length_filter", "params": {"min_len": 5}}]


@pytest_asyncio.fixture(autouse=True)
async def _admin_session(client: AsyncClient, seed_users: None) -> None:
    """加工任务写端点 require_admin:这些用例默认以 admin 身份请求。"""
    from app.services.auth import sign_token

    client.cookies.set("adp_session", sign_token("admin"))


async def _seed(session_factory: async_sessionmaker) -> None:
    async with session_factory() as session:
        session.add(Dataset(id=DATASET_ID, name="控制测试集"))
        session.add(
            DatasetVersion(
                id=VERSION_ID,
                dataset_id=DATASET_ID,
                version_no=1,
                storage_uri="/data/datasets/c1.jsonl",
                format="jsonl",
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_create_returns_pending_runs_in_background(
    client: AsyncClient,
    session_factory: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """POST 立即返回 pending(不阻塞到跑完);后台跑完后转 success。"""
    await _seed(session_factory)

    async def fake_run(
        session, *, job_id, input_version, operators, **kwargs
    ):
        return None, "process: []", "/tmp/run.log"

    monkeypatch.setattr("app.services.job_runner.run_process_job", fake_run)

    resp = await client.post(
        "/api/v1/jobs",
        json={
            "name": "清洗",
            "type": "clean",
            "datasetVersionId": VERSION_ID,
            "operators": OPERATORS,
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["state"] == "pending"
    job_id = resp.json()["data"]["id"]

    await job_runner.drain()
    detail = (await client.get(f"/api/v1/jobs/{job_id}")).json()["data"]
    assert detail["state"] == "success"


@pytest.mark.asyncio
async def test_stop_running_job_cancels(
    client: AsyncClient,
    session_factory: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """运行中的任务可停:状态转 cancelled(被杀的子进程非零退出,按 cancelled 记)。"""
    await _seed(session_factory)
    started = asyncio.Event()
    release = asyncio.Event()

    async def blocking_run(
        session, *, job_id, input_version, operators, **kwargs
    ):
        started.set()  # 已进入执行(running 已提交)
        await release.wait()  # 挂住,模拟子进程在跑
        raise EngineError("killed")  # 模拟被 terminate_job 杀掉:非零退出

    monkeypatch.setattr("app.services.job_runner.run_process_job", blocking_run)

    resp = await client.post(
        "/api/v1/jobs",
        json={
            "name": "长任务",
            "type": "clean",
            "datasetVersionId": VERSION_ID,
            "operators": OPERATORS,
        },
    )
    assert resp.status_code == 200, resp.text
    job_id = resp.json()["data"]["id"]

    await asyncio.wait_for(started.wait(), timeout=5)  # 等后台进入 running
    detail = (await client.get(f"/api/v1/jobs/{job_id}")).json()["data"]
    assert detail["state"] == "running"

    resp = await client.post(f"/api/v1/jobs/{job_id}/stop")
    assert resp.status_code == 200, resp.text

    release.set()  # 放行「被杀」的子进程,让后台任务收尾
    await job_runner.drain()
    detail = (await client.get(f"/api/v1/jobs/{job_id}")).json()["data"]
    assert detail["state"] == "cancelled"


@pytest.mark.asyncio
async def test_stop_job_toctou_does_not_overwrite_finished_state(
    client: AsyncClient,
    session_factory: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """TOCTOU 防护:stop 拿到 job(running)之后、真正落地 cancelled 之前,若后台
    协程已抢先把任务收敛为终态(如 success),不能无条件覆写——用条件 UPDATE
    (CAS),丢竞态的一方回 409,绝不覆盖真实产出信息(state/progress)。"""
    async with session_factory() as session:
        session.add(
            Job(id="job-race", name="竞态", type="clean", state="running", progress=50)
        )
        await session.commit()

    def _finish_concurrently(job_id: str) -> bool:
        # 模拟 stop 端点走到"杀子进程"这一步时(terminate_job 是**同步**调用,
        # 见 jobs.py 的 `if not terminate_job(job_id)`),后台协程恰好完成并落地
        # success——复现"另一条执行路径先一步改了这行"的竞态。terminate_job 同步,
        # 故这里必须同步提交(用独立 psycopg2 连接,确保对随后的 CAS UPDATE 可见);
        # 用 async session 会返回未 await 的协程,提交不生效,竞态无法复现。
        import os

        import psycopg2

        url = os.environ.get(
            "TEST_DATABASE_URL",
            "postgresql+asyncpg://adp:adp_dev_pw@127.0.0.1:55433/adp_test",
        )
        conn = psycopg2.connect(url.replace("postgresql+asyncpg://", "postgresql://"))
        try:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE jobs SET state='success', progress=100 WHERE id=%s",
                    (job_id,),
                )
            conn.commit()
        finally:
            conn.close()
        return False  # 仿 terminate_job:无子进程可杀(走 cancel_running_task 分支)

    monkeypatch.setattr("app.api.v1.jobs.terminate_job", _finish_concurrently)

    resp = await client.post("/api/v1/jobs/job-race/stop")
    assert resp.status_code == 409, resp.text

    async with session_factory() as session:
        job = await session.get(Job, "job-race")
        # 真实终态与产出信息未被覆盖为 cancelled
        assert job.state == "success"
        assert job.progress == 100


@pytest.mark.asyncio
async def test_stop_finished_job_409(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    """终态任务不可停 → 409。"""
    async with session_factory() as session:
        session.add(
            Job(id="job-done", name="done", type="clean", state="success", progress=100)
        )
        await session.commit()
    resp = await client.post("/api/v1/jobs/job-done/stop")
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_stop_unknown_job_404(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/jobs/job-nope/stop")
    assert resp.status_code == 404


def _spec_dict() -> dict:
    return {
        "name": "队列字段回归测试",
        "type": "clean",
        "dataset_version_id": VERSION_ID,
        "operators": OPERATORS,
    }


@pytest.mark.asyncio
async def test_resume_job_resets_attempts_and_claim(
    client: AsyncClient,
    session_factory: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """继续(resume)复位为 pending 时必须清零 attempts/claimed_by/heartbeat_at/
    queued_at:worker 模式下 claim_job 只认领 `attempts < max_attempts` 的
    pending 任务。若该任务此前已被 worker 心跳超时反复重排耗尽 attempts(此处
    直接构造耗尽态,模拟"暂停前已耗尽预算"这一edge)、复位时不清零,复位后
    的 pending 任务会因 attempts>=max_attempts 永远无 worker 认领——且 worker
    模式下 API 进程不再跑 reconcile_orphans 兜底,任务将静默永远卡在 pending。
    """
    await _seed(session_factory)

    async def fake_run(session, *, job_id, input_version, operators, **kwargs):
        return None, "process: []", "/tmp/run.log"

    monkeypatch.setattr("app.services.job_runner.run_process_job", fake_run)

    async with session_factory() as session:
        session.add(
            Job(
                id="job-resume-attempts",
                name="继续测试",
                type="clean",
                state="paused",
                created_by="admin",
                attempts=3,
                max_attempts=3,
                claimed_by="dead-worker",
                spec=_spec_dict(),
            )
        )
        await session.commit()

    # worker 执行模式:spawn 只入队(_mark_queued)不进程内执行,复位后的任务保持
    # pending 供 worker 认领——本测锁的正是 worker 认领语义(attempts 清零才可被认领);
    # inline 默认模式下 spawn 立即起协程跑完,state 会竞速到 running 使断言飘。
    monkeypatch.setattr(settings, "job_execution_mode", "worker")

    resp = await client.post("/api/v1/jobs/job-resume-attempts/resume")
    assert resp.status_code == 200, resp.text

    async with session_factory() as session:
        job = await session.get(Job, "job-resume-attempts")
        assert job.state == "pending"
        assert job.attempts == 0
        assert job.claimed_by is None

    await job_runner.drain()


@pytest.mark.asyncio
async def test_edit_job_resets_attempts_and_claim(
    client: AsyncClient,
    session_factory: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """编辑任务(PUT /jobs/{id},走 `_reset_for_edit_rerun`)同理必须清零
    attempts/claimed_by/heartbeat_at/queued_at——否则一个此前耗尽 attempts 才
    失败的任务,编辑后原地重跑同样会因 attempts>=max_attempts 永远无 worker
    认领,静默卡死。"""
    await _seed(session_factory)

    async def fake_run(session, *, job_id, input_version, operators, **kwargs):
        return None, "process: []", "/tmp/run.log"

    monkeypatch.setattr("app.services.job_runner.run_process_job", fake_run)

    async with session_factory() as session:
        session.add(
            Job(
                id="job-edit-attempts",
                name="编辑测试",
                type="clean",
                state="failed",
                created_by="admin",
                attempts=3,
                max_attempts=3,
                claimed_by="dead-worker",
                error="worker 崩溃回收:心跳超时且已尝试 3/3 次",
                spec=_spec_dict(),
            )
        )
        await session.commit()

    # worker 执行模式:同 resume,spawn 只入队不进程内执行,编辑重跑后任务保持 pending
    # 供 worker 认领;inline 默认模式下会竞速到 running 使断言飘。
    monkeypatch.setattr(settings, "job_execution_mode", "worker")

    resp = await client.put(
        "/api/v1/jobs/job-edit-attempts",
        json={
            "name": "编辑测试(改后)",
            "type": "clean",
            "datasetVersionId": VERSION_ID,
            "operators": OPERATORS,
        },
    )
    assert resp.status_code == 200, resp.text

    async with session_factory() as session:
        job = await session.get(Job, "job-edit-attempts")
        assert job.state == "pending"
        assert job.attempts == 0
        assert job.claimed_by is None

    await job_runner.drain()


@pytest.mark.asyncio
async def test_reconcile_orphans_marks_running_failed(
    session_factory: async_sessionmaker,
) -> None:
    """启动回收:残留 pending/running 的任务统一标失败;终态任务不动。"""
    async with session_factory() as session:
        session.add(
            Job(id="job-run", name="r", type="clean", state="running", progress=30)
        )
        session.add(
            Job(id="job-pend", name="p", type="clean", state="pending", progress=0)
        )
        session.add(
            Job(id="job-ok", name="o", type="clean", state="success", progress=100)
        )
        await session.commit()

    async with session_factory() as session:
        n = await job_runner.reconcile_orphans(session)
    assert n == 2

    async with session_factory() as session:
        run = await session.get(Job, "job-run")
        pend = await session.get(Job, "job-pend")
        ok = await session.get(Job, "job-ok")
        assert run.state == "failed" and "重启" in (run.error or "")
        assert pend.state == "failed"
        assert ok.state == "success"  # 终态不动


@pytest.mark.asyncio
async def test_run_dj_uses_new_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """子进程以 start_new_session 起,才能在停止/超时时整组杀(连带 dj fork 的子孙)。"""
    captured: dict = {}

    class _P:
        pid = 4242
        returncode = None

        async def communicate(self):
            self.returncode = 0
            return b"", b""

        async def wait(self):
            return 0

    async def _fake_create(*args, **kwargs):
        captured.update(kwargs)
        return _P()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create)
    code, _ = await engine._run_dj(Path("/tmp/x.yaml"))
    assert code == 0
    assert captured.get("start_new_session") is True


def test_terminate_job_kills_process_group(monkeypatch: pytest.MonkeyPatch) -> None:
    """terminate_job 对进程组发 SIGKILL(连带子孙),而非只杀直接子进程。"""
    calls: list = []

    class _P:
        pid = 4242
        returncode = None

    engine._running_procs["job-grp"] = _P()
    monkeypatch.setattr("os.getpgid", lambda pid: pid)
    monkeypatch.setattr("os.killpg", lambda pgid, sig: calls.append((pgid, sig)))
    try:
        assert engine.terminate_job("job-grp") is True
        assert calls == [(4242, signal.SIGKILL)]
    finally:
        engine._running_procs.pop("job-grp", None)


@pytest.mark.asyncio
async def test_run_dj_timeout_kills_tree_and_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """_run_dj 超时:整组杀子进程树并抛 EngineError(防卡死安全网)。"""
    killed: list = []

    class _HangProc:
        pid = 4242
        returncode = None

        async def communicate(self):
            await asyncio.sleep(60)
            return b"", b""

        async def wait(self):
            return self.returncode

    async def _fake_create(*args, **kwargs):
        return _HangProc()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create)
    monkeypatch.setattr("os.getpgid", lambda pid: pid)
    monkeypatch.setattr("os.killpg", lambda pgid, sig: killed.append((pgid, sig)))
    monkeypatch.setattr(settings, "engine_job_timeout", 1)

    with pytest.raises(EngineError, match="超时"):
        await engine._run_dj(Path("/tmp/none.yaml"))
    assert killed == [(4242, signal.SIGKILL)]


@pytest.mark.asyncio
async def test_manifest_job_blocked_without_multimodal(
    client: AsyncClient,
    session_factory: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """没装多模态引擎(torch)时,manifest 数据集加工提前 400,不起后台任务。"""
    async with session_factory() as session:
        session.add(Dataset(id="dset-m1", name="媒体集"))
        session.add(
            DatasetVersion(
                id="dsv-m1",
                dataset_id="dset-m1",
                version_no=1,
                storage_uri="s3://uploads/dset-m1/manifest.jsonl",
                format="manifest",
                origin="managed",
                rows=2,
            )
        )
        await session.commit()

    async def _not_ready():
        return False

    monkeypatch.setattr("app.api.v1.jobs.multimodal_ready", _not_ready)

    resp = await client.post(
        "/api/v1/jobs",
        json={
            "name": "媒体加工",
            "type": "clean",
            "datasetVersionId": "dsv-m1",
            "operators": [{"name": "text_length_filter", "params": {"min_len": 1}}],
        },
    )
    assert resp.status_code == 400, resp.text
    assert "多模态" in resp.json()["message"]
    # 前置拦截 → 没有建任何任务
    assert (await client.get("/api/v1/jobs")).json()["total"] == 0


@pytest.mark.asyncio
async def test_manifest_job_allowed_with_multimodal(
    client: AsyncClient,
    session_factory: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """装了多模态引擎时,manifest 加工放行(进入后台执行)。"""
    async with session_factory() as session:
        session.add(Dataset(id="dset-m2", name="媒体集2"))
        session.add(
            DatasetVersion(
                id="dsv-m2",
                dataset_id="dset-m2",
                version_no=1,
                storage_uri="s3://uploads/dset-m2/manifest.jsonl",
                format="manifest",
                origin="managed",
                rows=1,
            )
        )
        await session.commit()

    async def _ready():
        return True

    async def _fake_run(
        session, *, job_id, input_version, operators, **kwargs
    ):
        return None, "process: []", "/tmp/run.log"

    monkeypatch.setattr("app.api.v1.jobs.multimodal_ready", _ready)
    monkeypatch.setattr("app.services.job_runner.run_process_job", _fake_run)

    resp = await client.post(
        "/api/v1/jobs",
        json={
            "name": "媒体加工2",
            "type": "clean",
            "datasetVersionId": "dsv-m2",
            "operators": [{"name": "text_length_filter", "params": {"min_len": 1}}],
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["state"] == "pending"
    await job_runner.drain()


@pytest.mark.asyncio
async def test_job_write_requires_login_and_acl(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    """加工写端点门控(ACL 化):登录但对输入数据集无 ACL → 403;匿名 → 401。

    为什么:建任务已从"仅超管"放宽为"登录 + 数据集 ACL ≥ edit"——普通用户
    能加工自己有 edit 授权的数据集,但对别人的私有集(本例 owner=admin)仍 403。
    """
    from app.services.auth import sign_token

    await _seed(session_factory)
    body = {
        "name": "x",
        "type": "clean",
        "datasetVersionId": VERSION_ID,
        "operators": OPERATORS,
    }
    client.cookies.set("adp_session", sign_token("user"))
    assert (await client.post("/api/v1/jobs", json=body)).status_code == 403

    client.cookies.delete("adp_session")
    assert (await client.post("/api/v1/jobs", json=body)).status_code == 401
