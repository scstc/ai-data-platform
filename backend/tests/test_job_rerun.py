"""加工任务重跑(rerun)契约:POST /jobs/{id}/rerun。

为什么:加工任务原本是一次性执行记录,无法重复执行。补「重跑」的做法是建任务时
把原始执行规格(算子 + 输出去向 + 输入版本)存进 jobs.spec,rerun 据此对**原输入
版本**再跑一次、产出新版本,并**新建一条任务记录**(保留每次运行的血缘)。早于本
特性、无 spec 的旧任务不可重跑 → 400(而非 500)。

执行已改为**后台异步**(见 job_runner):POST 立即返回 pending,跑完才转 success。
子进程层(run_process_job)打桩,只验证编排与状态流转;测试用 job_runner.drain()
等后台任务落定后再断言。
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.dataset import Dataset
from app.models.dataset_version import DatasetVersion
from app.models.job import Job
from app.models.job_input import JobInput
from app.services import job_runner

DATASET_ID = "dset-r1"
VERSION_ID = "dsv-r1"
OPERATORS = [{"name": "text_length_filter", "params": {"min_len": 5}}]


@pytest_asyncio.fixture(autouse=True)
async def _admin_session(client: AsyncClient, seed_users: None) -> None:
    """加工任务写端点 require_admin:这些用例默认以 admin 身份请求。"""
    from app.services.auth import sign_token

    client.cookies.set("adp_session", sign_token("admin"))


async def _seed_version(session_factory: async_sessionmaker) -> None:
    async with session_factory() as session:
        session.add(Dataset(id=DATASET_ID, name="重跑测试集"))
        session.add(
            DatasetVersion(
                id=VERSION_ID,
                dataset_id=DATASET_ID,
                version_no=1,
                storage_uri="/data/datasets/r1.jsonl",
                format="jsonl",
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_rerun_reexecutes_with_saved_spec(
    client: AsyncClient,
    session_factory: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """建任务存 spec;rerun 用它对原输入版本再跑一次,新建任务、引擎再被调用。"""
    await _seed_version(session_factory)

    calls: list[tuple[str, str, list]] = []

    async def fake_run(
        session, *, job_id, input_version, operators
    ):
        calls.append((job_id, input_version.id, operators))
        session.add(JobInput(job_id=job_id, dataset_version_id=input_version.id))
        await session.commit()
        return None, "process: []", "/tmp/run.log"

    monkeypatch.setattr("app.services.job_runner.run_process_job", fake_run)

    # 建任务(后台执行)→ 立即返回 pending
    resp = await client.post(
        "/api/v1/jobs",
        json={
            "name": "清洗任务",
            "type": "clean",
            "datasetVersionId": VERSION_ID,
            "operators": OPERATORS,
        },
    )
    assert resp.status_code == 200, resp.text
    job1 = resp.json()["data"]
    assert job1["state"] == "pending"
    assert job1["canRerun"] is True
    job1_id = job1["id"]

    await job_runner.drain()  # 等后台跑完
    detail = (await client.get(f"/api/v1/jobs/{job1_id}")).json()["data"]
    assert detail["state"] == "success"

    # 重跑 → 又是 pending,新建一条记录
    resp = await client.post(f"/api/v1/jobs/{job1_id}/rerun")
    assert resp.status_code == 200, resp.text
    job2 = resp.json()["data"]
    assert job2["state"] == "pending"
    assert job2["canRerun"] is True
    assert job2["id"] != job1_id  # 新建记录,不改动原记录

    await job_runner.drain()
    detail2 = (await client.get(f"/api/v1/jobs/{job2['id']}")).json()["data"]
    assert detail2["state"] == "success"

    # 引擎被调用两次;第二次仍针对「原」输入版本、用原算子(可复现)
    assert len(calls) == 2
    assert calls[1][1] == VERSION_ID
    assert calls[1][2] == OPERATORS

    # 列表里现在有两条
    resp = await client.get("/api/v1/jobs", params={"type": "clean"})
    assert resp.json()["total"] == 2


@pytest.mark.asyncio
async def test_rerun_unknown_job_404(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/jobs/job-nope/rerun")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_rerun_job_without_spec_400(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    """早于本特性、无 spec 的旧任务不可重跑 → 400(给出可读原因,而非 500)。"""
    async with session_factory() as session:
        session.add(
            Job(
                id="job-legacy",
                name="旧任务",
                type="clean",
                state="success",
                progress=100,
                spec=None,
            )
        )
        await session.commit()

    resp = await client.post("/api/v1/jobs/job-legacy/rerun")
    assert resp.status_code == 400
    assert "无可重跑" in resp.json()["message"]
