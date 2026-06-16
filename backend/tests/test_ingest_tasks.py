"""采集任务路由测试：诚实状态机 + 列表筛选。

状态机：create(pending) → rerun。rerun 为同步执行,返回即终态——
PG+采集对象 → success(见 PG 用例);非 PG 源 / PG 未配采集对象 → 如实 failed
(不再凭轮询 GET 伪造进度/成功)。另覆盖:数据源不存在 → 404、单条 404、
列表 name/status 筛选、stop(failed)、delete。

数据源通过 session_factory 直接落库，避免依赖 datasources 路由。
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.datasource import DataSource


async def _seed_datasource(
    session_factory: async_sessionmaker,
    ds_id: str = "ds-aaa111",
    name: str = "测试对象存储",
) -> DataSource:
    """直接落库一个数据源，供采集任务引用。"""
    async with session_factory() as session:
        ds = DataSource(
            id=ds_id,
            name=name,
            type="s3",
            status="connected",
            config={"bucket": "b"},
            creator="admin",
        )
        session.add(ds)
        await session.commit()
        await session.refresh(ds)
        return ds


async def _create_task(
    client: AsyncClient, datasource_id: str, name: str = "每日同步任务"
) -> dict:
    """创建任务并返回 data 部分。"""
    resp = await client.post(
        "/api/v1/ingest-tasks",
        json={
            "name": name,
            "datasourceId": datasource_id,
            "schedule": {"mode": "cron", "cron": "0 2 * * *"},
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    return body["data"]


@pytest.mark.asyncio
async def test_create_validates_datasource_exists(client: AsyncClient) -> None:
    """数据源不存在时创建返回 404 + {success:false, message}。"""
    resp = await client.post(
        "/api/v1/ingest-tasks",
        json={
            "name": "孤儿任务",
            "datasourceId": "ds-nope00",
            "schedule": {"mode": "once"},
        },
    )
    assert resp.status_code == 404
    body = resp.json()
    assert body["success"] is False
    assert isinstance(body["message"], str)


@pytest.mark.asyncio
async def test_create_sets_pending_and_redundant_name(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    """创建成功：初始 pending/0、日志为创建提示、冗余数据源名称、id 前缀正确。"""
    ds = await _seed_datasource(session_factory)
    data = await _create_task(client, ds.id)

    assert data["id"].startswith("task-")
    assert data["status"] == "pending"
    assert data["progress"] == 0
    assert data["datasourceId"] == ds.id
    assert data["datasourceName"] == ds.name  # 冗余自数据源表
    assert data["logs"] == ["[INFO] 任务已创建"]
    assert data["lastRunAt"] is None
    # schedule 透传且为 camelCase 结构
    assert data["schedule"] == {"mode": "cron", "cron": "0 2 * * *"}


@pytest.mark.asyncio
async def test_lifecycle_unsupported_source_fails_honestly(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    """走查：create→rerun(非 PG 源如实失败,不伪造 running/success)→GET 稳定→stop→delete。"""
    ds = await _seed_datasource(session_factory)  # type=s3,非 PG
    task = await _create_task(client, ds.id)
    task_id = task["id"]

    # 1) pending 任务 GET 不推进进度
    resp = await client.get(f"/api/v1/ingest-tasks/{task_id}")
    assert resp.status_code == 200
    assert resp.json()["data"]["progress"] == 0
    assert resp.json()["data"]["status"] == "pending"

    # 2) rerun → 非 PG 源如实失败(不再伪造 running)、last_run_at 落值、日志说明原因
    resp = await client.post(f"/api/v1/ingest-tasks/{task_id}/rerun")
    assert resp.status_code == 200
    rerun = resp.json()["data"]
    assert rerun["status"] == "failed"
    assert rerun["lastRunAt"] is not None
    assert any("暂不支持自动采集" in line for line in rerun["logs"])

    # 3) 反复 GET 既不推进进度也不伪造成功:仍为 failed/0
    for _ in range(3):
        resp = await client.get(f"/api/v1/ingest-tasks/{task_id}")
        data = resp.json()["data"]
        assert data["status"] == "failed"
        assert data["progress"] == 0

    # 4) stop → failed、追加手动停止日志
    resp = await client.post(f"/api/v1/ingest-tasks/{task_id}/stop")
    assert resp.status_code == 200
    stopped = resp.json()["data"]
    assert stopped["status"] == "failed"
    assert "[WARN] 任务被手动停止" in stopped["logs"]

    # 5) delete → success:true，再 GET 404
    resp = await client.delete(f"/api/v1/ingest-tasks/{task_id}")
    assert resp.status_code == 200
    assert resp.json()["success"] is True

    resp = await client.get(f"/api/v1/ingest-tasks/{task_id}")
    assert resp.status_code == 404
    assert resp.json()["success"] is False


@pytest.mark.asyncio
async def test_pg_rerun_without_extract_fails_honestly(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    """PG 源但未配置采集对象:rerun 如实失败并提示去配置,不伪造成功、不产出数据集。"""
    async with session_factory() as session:
        session.add(
            DataSource(
                id="ds-pg-noextract",
                name="PG无采集对象",
                type="database",
                db_kind="postgresql",
                status="connected",
                config={
                    "host": "127.0.0.1",
                    "port": 5432,
                    "database": "d",
                    "username": "u",
                    "password": "p",
                },
                creator="admin",
            )
        )
        await session.commit()
    task = await _create_task(client, "ds-pg-noextract", name="未配置采集对象")

    resp = await client.post(f"/api/v1/ingest-tasks/{task['id']}/rerun")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "failed"
    assert any("未配置采集对象" in line for line in data["logs"])

    # 未配置即失败发生在连库之前:不产生任何运行记录/数据集
    resp = await client.get(f"/api/v1/ingest-tasks/{task['id']}/runs")
    assert resp.json()["total"] == 0


@pytest.mark.asyncio
async def test_action_endpoints_404_when_missing(client: AsyncClient) -> None:
    """rerun/stop/delete 命中不存在的任务均 404 + {success:false}。"""
    for method, path in (
        ("post", "/api/v1/ingest-tasks/task-zzzzzz/rerun"),
        ("post", "/api/v1/ingest-tasks/task-zzzzzz/stop"),
        ("delete", "/api/v1/ingest-tasks/task-zzzzzz"),
    ):
        resp = await getattr(client, method)(path)
        assert resp.status_code == 404, (method, path)
        assert resp.json()["success"] is False


@pytest.mark.asyncio
async def test_list_pagination_and_filters(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    """列表分页响应形状、name 模糊与 status 精确筛选。"""
    ds = await _seed_datasource(session_factory)
    # 造 3 个任务：两个含「同步」，一个含「导出」
    t_sync_a = await _create_task(client, ds.id, name="对象存储同步")
    await _create_task(client, ds.id, name="日志增量同步")
    await _create_task(client, ds.id, name="全量导出")

    # 全量列表：分页响应形状
    resp = await client.get("/api/v1/ingest-tasks?current=1&pageSize=10")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["total"] == 3
    assert len(body["data"]) == 3

    # name 模糊筛选
    resp = await client.get("/api/v1/ingest-tasks", params={"name": "同步"})
    body = resp.json()
    assert body["total"] == 2
    assert all("同步" in item["name"] for item in body["data"])

    # status 精确筛选：rerun 其中一个(s3 源→如实 failed),再按 status=failed 过滤
    await client.post(f"/api/v1/ingest-tasks/{t_sync_a['id']}/rerun")
    resp = await client.get("/api/v1/ingest-tasks", params={"status": "failed"})
    body = resp.json()
    assert body["total"] == 1
    assert body["data"][0]["id"] == t_sync_a["id"]
    assert body["data"][0]["status"] == "failed"

    # 分页：pageSize=2 → 第一页 2 条，total 仍为 3
    resp = await client.get("/api/v1/ingest-tasks?current=1&pageSize=2")
    body = resp.json()
    assert body["total"] == 3
    assert len(body["data"]) == 2


# ---------------------------------------------------------------------------
# 收编(0005):每次运行产一条 jobs 表 type=ingest 记录
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_pg_rerun_creates_ingest_job_and_lineage(
    client: AsyncClient,
    session_factory: async_sessionmaker,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PG 真实拉取:运行产 job(type=ingest),产物版本血缘指向该 job,
    runs 端点(读 jobs 表)回放 wire 形态与收编前一致。"""
    from urllib.parse import urlparse

    from app.core.config import settings
    from tests.conftest import TEST_DATABASE_URL

    monkeypatch.setattr(settings, "datasets_dir", str(tmp_path))

    # 数据源指向测试库自身(asyncpg 直连)
    u = urlparse(TEST_DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://"))
    async with session_factory() as session:
        from app.models.datasource import DataSource as DS

        session.add(
            DS(
                id="ds-pg-self",
                name="测试库自身",
                type="database",
                db_kind="postgresql",
                status="connected",
                config={
                    "host": u.hostname,
                    "port": u.port,
                    "database": u.path.lstrip("/"),
                    "username": u.username,
                    "password": u.password,
                },
                creator="admin",
            )
        )
        await session.commit()

    resp = await client.post(
        "/api/v1/ingest-tasks",
        json={
            "name": "PG真实采集",
            "datasourceId": "ds-pg-self",
            "schedule": {"mode": "once"},
            "extract": {"mode": "sql", "sql": "SELECT 1 AS num, 'hi' AS text"},
        },
    )
    assert resp.status_code == 200, resp.text
    task_id = resp.json()["data"]["id"]

    # 运行:同步真实拉取
    resp = await client.post(f"/api/v1/ingest-tasks/{task_id}/rerun")
    body = resp.json()
    assert body["data"]["status"] == "success", body
    assert body["data"]["runCount"] == 1

    # jobs 表多了一条 type=ingest,且通用 /jobs 列表可见
    resp = await client.get("/api/v1/jobs", params={"type": "ingest"})
    jobs = resp.json()["data"]
    assert len(jobs) == 1 and jobs[0]["type"] == "ingest"
    job_id = jobs[0]["id"]

    # 产物版本血缘指向本次运行的 job(而非 task)
    async with session_factory() as session:
        from sqlalchemy import select

        from app.models.dataset_version import DatasetVersion as DV

        version = (await session.scalars(select(DV))).one()
        assert version.produced_by_job_id == job_id
        assert version.rows == 1

        # 采集落地的数据集归到 SQL 接入栏(data_type='sql'),否则在数据接入页任何分栏都不可见
        from app.models.dataset import Dataset as DSModel

        ds_row = await session.get(DSModel, version.dataset_id)
        assert ds_row is not None
        assert ds_row.data_type == "sql"

    # runs 端点 wire 形态与收编前一致
    resp = await client.get(f"/api/v1/ingest-tasks/{task_id}/runs")
    body = resp.json()
    assert body["total"] == 1
    run = body["data"][0]
    assert run["id"] == job_id
    assert run["status"] == "success"
    assert run["rows"] == 1
    assert run["datasetCount"] == 1
    assert run["outputs"][0]["versionNo"] == 1

    # 任务详情的产物列表经 job 反查
    resp = await client.get(f"/api/v1/ingest-tasks/{task_id}")
    output = resp.json()["data"]["output"]
    assert len(output) == 1 and output[0]["rows"] == 1


@pytest.mark.asyncio
async def test_runs_empty_for_task_without_jobs(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    """无运行记录的任务:runs 端点返回空列表。"""
    ds = await _seed_datasource(session_factory)
    task = await _create_task(client, ds.id)
    resp = await client.get(f"/api/v1/ingest-tasks/{task['id']}/runs")
    body = resp.json()
    assert body["total"] == 0 and body["data"] == []
