"""加工任务删除(delete)契约:DELETE /jobs/{id}。

为什么:加工任务原本只能新建/重跑,任务记录无法清理。补「删除」时的红线是
**只删任务记录,绝不连带删产物**——产出的数据集版本是独立资产(可能已发布 /
被下游引用,有独立删除入口)。故删除时:删任务本身 + 清血缘边(job_inputs)+
把产出版本的 produced_by_job_id 置空(版本保留)。运行中的任务不可删 → 409。

子进程层(run_process_job)打桩,产出一条带 produced_by_job_id 的版本与一条
job_inputs 血缘边,以便验证删除后的清理与保留语义。
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.dataset import Dataset
from app.models.dataset_version import DatasetVersion
from app.models.job import Job
from app.models.job_input import JobInput
from app.services import job_runner

DATASET_ID = "dset-d1"
VERSION_ID = "dsv-d1"
OPERATORS = [{"name": "text_length_filter", "params": {"min_len": 5}}]


@pytest_asyncio.fixture(autouse=True)
async def _admin_session(client: AsyncClient, seed_users: None) -> None:
    """加工任务写端点 require_admin:这些用例默认以 admin 身份请求。"""
    from app.services.auth import sign_token

    client.cookies.set("adp_session", sign_token("admin"))


async def _seed_version(session_factory: async_sessionmaker) -> None:
    async with session_factory() as session:
        session.add(Dataset(id=DATASET_ID, name="删除测试集"))
        session.add(
            DatasetVersion(
                id=VERSION_ID,
                dataset_id=DATASET_ID,
                version_no=1,
                storage_uri="/data/datasets/d1.jsonl",
                format="jsonl",
            )
        )
        await session.commit()


def _stub_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    """打桩 run_process_job:产出一条带 produced_by_job_id 的版本 + 一条血缘边。"""

    async def fake_run(
        session, *, job_id, input_version, operators
    ):
        version = DatasetVersion(
            id=f"dsv-out-{job_id}",
            dataset_id=input_version.dataset_id,
            version_no=2,
            storage_uri=f"/data/datasets/{job_id}.jsonl",
            format="jsonl",
            rows=3,
            produced_by_job_id=job_id,
        )
        session.add(version)
        session.add(JobInput(job_id=job_id, dataset_version_id=input_version.id))
        await session.commit()
        return version, "process: []", "/tmp/run.log"

    monkeypatch.setattr("app.services.job_runner.run_process_job", fake_run)


@pytest.mark.asyncio
async def test_delete_removes_job_keeps_produced_version(
    client: AsyncClient,
    session_factory: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """删任务:任务与血缘边清掉,产出的版本保留且 produced_by_job_id 置空。"""
    await _seed_version(session_factory)
    _stub_engine(monkeypatch)

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
    job_id = resp.json()["data"]["id"]
    # 后台跑完后:产出版本与血缘边均已落库
    await job_runner.drain()
    out_version_id = f"dsv-out-{job_id}"
    detail = (await client.get(f"/api/v1/jobs/{job_id}")).json()["data"]
    assert detail["state"] == "success"
    assert detail["output"]["versionId"] == out_version_id

    # 删除
    resp = await client.delete(f"/api/v1/jobs/{job_id}")
    assert resp.status_code == 200, resp.text
    assert resp.json()["success"] is True

    # 任务消失
    assert (await client.get(f"/api/v1/jobs/{job_id}")).status_code == 404
    assert (await client.get("/api/v1/jobs")).json()["total"] == 0

    async with session_factory() as session:
        # 血缘边清掉
        edges = (
            await session.scalars(
                select(JobInput).where(JobInput.job_id == job_id)
            )
        ).all()
        assert edges == []
        # 产出版本保留,但上游指针置空(不连带删数据)
        out = await session.get(DatasetVersion, out_version_id)
        assert out is not None
        assert out.produced_by_job_id is None
        # 输入版本不受影响
        assert await session.get(DatasetVersion, VERSION_ID) is not None


@pytest.mark.asyncio
async def test_delete_unknown_job_404(client: AsyncClient) -> None:
    resp = await client.delete("/api/v1/jobs/job-nope")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_batch_delete_skips_running_keeps_versions(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    """批量删除:删可删的、跳过 running/不存在,返回实际删除数;产物版本保留、血缘清掉。"""
    async with session_factory() as session:
        session.add(Dataset(id=DATASET_ID, name="批量删除集"))
        session.add(
            DatasetVersion(
                id=VERSION_ID,
                dataset_id=DATASET_ID,
                version_no=1,
                storage_uri="/data/datasets/in.jsonl",
                format="jsonl",
            )
        )
        # 可删任务 job-ok:有产出版本 + 一条输入血缘边
        session.add(
            DatasetVersion(
                id="dsv-out-ok",
                dataset_id=DATASET_ID,
                version_no=2,
                storage_uri="/data/datasets/out.jsonl",
                format="jsonl",
                produced_by_job_id="job-ok",
            )
        )
        session.add(
            Job(id="job-ok", name="ok", type="clean", state="success", progress=100)
        )
        session.add(
            Job(id="job-run", name="run", type="clean", state="running", progress=50)
        )
        session.add(JobInput(job_id="job-ok", dataset_version_id=VERSION_ID))
        await session.commit()

    resp = await client.post(
        "/api/v1/jobs/batch-delete",
        json={"ids": ["job-ok", "job-run", "job-missing"]},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["deleted"] == 1  # 只删了 job-ok

    async with session_factory() as session:
        assert await session.get(Job, "job-ok") is None
        # running 与不存在的 id 均跳过,不阻断整批
        assert await session.get(Job, "job-run") is not None
        # 产出版本保留,上游指针置空
        out = await session.get(DatasetVersion, "dsv-out-ok")
        assert out is not None
        assert out.produced_by_job_id is None
        # 血缘边清掉
        edges = (
            await session.scalars(
                select(JobInput).where(JobInput.job_id == "job-ok")
            )
        ).all()
        assert edges == []


@pytest.mark.asyncio
async def test_delete_running_job_409(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    """运行中的任务不可删 → 409(而非静默删掉执行中的记录)。"""
    async with session_factory() as session:
        session.add(
            Job(
                id="job-running",
                name="运行中任务",
                type="clean",
                state="running",
                progress=42,
            )
        )
        await session.commit()

    resp = await client.delete("/api/v1/jobs/job-running")
    assert resp.status_code == 409
    assert "运行中" in resp.json()["message"]
    # 仍在库里,未被删
    async with session_factory() as session:
        assert await session.get(Job, "job-running") is not None
