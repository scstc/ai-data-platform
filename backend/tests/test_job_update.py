"""加工任务编辑(update)契约:PUT /jobs/{id}。

为什么:任务列表的「编辑」= 覆盖原任务配置并**原地重跑**(沿用任务 id,不新建
记录)——区别于 rerun(新建记录)。编辑前置条件:任务不在运行/排队中(409)。
血缘边 job_inputs 联合主键 (job_id, dataset_version_id),原地重跑同一输入版本
会撞主键 → 更新时必须先清旧边,这里靠打桩引擎真实写 JobInput 来验证。
编辑器回填依赖详情接口的 editSpec(camelCase 化的 spec),一并断言。
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

DATASET_ID = "dset-u1"
VERSION_ID = "dsv-u1"
OPERATORS = [{"name": "text_length_filter", "params": {"min_len": 5}}]
NEW_OPERATORS = [{"name": "text_length_filter", "params": {"min_len": 10}}]


@pytest_asyncio.fixture(autouse=True)
async def _admin_session(client: AsyncClient, seed_users: None) -> None:
    """加工任务写端点 require_admin:这些用例默认以 admin 身份请求。"""
    from app.services.auth import sign_token

    client.cookies.set("adp_session", sign_token("admin"))


async def _seed_version(session_factory: async_sessionmaker) -> None:
    async with session_factory() as session:
        session.add(Dataset(id=DATASET_ID, name="编辑测试集"))
        session.add(
            DatasetVersion(
                id=VERSION_ID,
                dataset_id=DATASET_ID,
                version_no=1,
                storage_uri="/data/datasets/u1.jsonl",
                format="jsonl",
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_update_overwrites_spec_and_reruns_in_place(
    client: AsyncClient,
    session_factory: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """编辑成功任务:覆盖 spec 后同一条记录原地重跑,血缘边不撞主键。"""
    await _seed_version(session_factory)

    calls: list[tuple[str, str, list]] = []

    async def fake_run(session, *, job_id, input_version, operators, **_):
        calls.append((job_id, input_version.id, operators))
        # 镜像真实引擎:成功时写 job_inputs 血缘边(联合主键)
        session.add(JobInput(job_id=job_id, dataset_version_id=input_version.id))
        await session.commit()
        return None, "process: []", "/tmp/run.log"

    monkeypatch.setattr("app.services.job_runner.run_process_job", fake_run)

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
    await job_runner.drain()

    # 详情带 editSpec(camelCase),供编辑器回填
    detail = (await client.get(f"/api/v1/jobs/{job_id}")).json()["data"]
    assert detail["state"] == "success"
    assert detail["editSpec"]["datasetVersionId"] == VERSION_ID
    assert detail["editSpec"]["operators"] == OPERATORS

    # 编辑:改名 + 改算子参数 → 同一条记录复位 pending 重跑
    resp = await client.put(
        f"/api/v1/jobs/{job_id}",
        json={
            "name": "清洗任务(改)",
            "type": "clean",
            "datasetVersionId": VERSION_ID,
            "operators": NEW_OPERATORS,
        },
    )
    assert resp.status_code == 200, resp.text
    updated = resp.json()["data"]
    assert updated["id"] == job_id  # 原地更新,不新建记录
    assert updated["state"] == "pending"
    assert updated["name"] == "清洗任务(改)"

    await job_runner.drain()
    detail2 = (await client.get(f"/api/v1/jobs/{job_id}")).json()["data"]
    # 旧血缘边已清,重跑同一输入版本不撞 (job_id, dataset_version_id) 主键
    assert detail2["state"] == "success"
    assert detail2["editSpec"]["operators"] == NEW_OPERATORS

    # 引擎两次调用,第二次用的是新算子;列表仍只有一条记录
    assert len(calls) == 2
    assert calls[1][2] == NEW_OPERATORS
    resp = await client.get("/api/v1/jobs", params={"type": "clean"})
    assert resp.json()["total"] == 1


@pytest.mark.asyncio
async def test_update_running_job_409(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    """运行中的任务不可编辑 → 409(先停止)。"""
    await _seed_version(session_factory)
    async with session_factory() as session:
        session.add(
            Job(
                id="job-upd-running",
                name="跑着的任务",
                type="clean",
                state="running",
                progress=50,
                spec={"name": "跑着的任务", "dataset_version_id": VERSION_ID},
            )
        )
        await session.commit()

    resp = await client.put(
        "/api/v1/jobs/job-upd-running",
        json={
            "name": "改名",
            "type": "clean",
            "datasetVersionId": VERSION_ID,
            "operators": OPERATORS,
        },
    )
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_update_unknown_or_other_type_404(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    """未知任务/非加工类型(如 quality)不走本端点 → 404。"""
    await _seed_version(session_factory)
    async with session_factory() as session:
        session.add(
            Job(
                id="job-upd-quality",
                name="质量任务",
                type="quality",
                state="success",
                progress=100,
                spec=None,
            )
        )
        await session.commit()

    body = {
        "name": "x",
        "type": "clean",
        "datasetVersionId": VERSION_ID,
        "operators": OPERATORS,
    }
    resp = await client.put("/api/v1/jobs/job-nope", json=body)
    assert resp.status_code == 404
    resp = await client.put("/api/v1/jobs/job-upd-quality", json=body)
    assert resp.status_code == 404
