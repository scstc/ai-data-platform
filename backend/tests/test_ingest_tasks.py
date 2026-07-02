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


async def _seed_lake(
    session_factory: async_sessionmaker, lake_id: str = "lake-test01"
) -> str:
    """直接落库一个数据湖(治理改造:采集任务必须绑湖,数据入湖归档)。"""
    from app.models.data_lake import DataLake

    async with session_factory() as session:
        if await session.get(DataLake, lake_id) is None:
            session.add(DataLake(id=lake_id, name="测试湖"))
            await session.commit()
    return lake_id


async def _create_task(
    client: AsyncClient, session_factory: async_sessionmaker,
    datasource_id: str, name: str = "每日同步任务",
    lake_id: str | None = None,
) -> dict:
    """创建任务并返回 data 部分。湖优先:未给 lake_id 时先落一个测试湖。"""
    if lake_id is None:
        lake_id = await _seed_lake(session_factory)
    resp = await client.post(
        "/api/v1/ingest-tasks",
        json={
            "name": name,
            "datasourceId": datasource_id,
            "lakeId": lake_id,
            # cron 调度本期未启用(§4.10),创建端会 422;一律用 once。
            "schedule": {"mode": "once"},
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    return body["data"]


@pytest.mark.asyncio
async def test_create_validates_datasource_exists(client: AsyncClient) -> None:
    """数据源不存在时创建返回 404 + {success:false, message}。"""
    # datasource 校验先于 lake 校验,故 lakeId 给占位值即可走 datasource 404
    resp = await client.post(
        "/api/v1/ingest-tasks",
        json={
            "name": "孤儿任务",
            "datasourceId": "ds-nope00",
            "lakeId": "lake-nope0",
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
    data = await _create_task(client, session_factory, ds.id)

    assert data["id"].startswith("task-")
    assert data["status"] == "pending"
    assert data["progress"] == 0
    assert data["datasourceId"] == ds.id
    assert data["datasourceName"] == ds.name  # 冗余自数据源表
    assert data["logs"] == ["[INFO] 任务已创建"]
    assert data["lastRunAt"] is None
    # schedule 透传且为 camelCase 结构(cron 本期未启用,用 once)
    assert data["schedule"] == {"mode": "once", "cron": None}


@pytest.mark.asyncio
async def test_lifecycle_unsupported_source_fails_honestly(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    """走查:create→rerun(S3 源已支持但未配采集对象→如实失败)→GET 稳定→stop→delete。

    S3 连接器已接入(§4.9),但本任务未配 extract,故 rerun 在建 job 前诚实失败
    (「未配置采集对象」),不伪造 running/success、不产出运行记录。
    """
    ds = await _seed_datasource(session_factory)  # type=s3,已支持但本例未配 extract
    task = await _create_task(client, session_factory, ds.id)
    task_id = task["id"]

    # 1) pending 任务 GET 不推进进度
    resp = await client.get(f"/api/v1/ingest-tasks/{task_id}")
    assert resp.status_code == 200
    assert resp.json()["data"]["progress"] == 0
    assert resp.json()["data"]["status"] == "pending"

    # 2) rerun → 未配采集对象如实失败(不伪造 running)、last_run_at 落值、日志说明原因
    resp = await client.post(f"/api/v1/ingest-tasks/{task_id}/rerun")
    assert resp.status_code == 200
    rerun = resp.json()["data"]
    assert rerun["status"] == "failed"
    assert rerun["lastRunAt"] is not None
    assert any("未配置采集对象" in line for line in rerun["logs"])

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
    task = await _create_task(
        client, session_factory, "ds-pg-noextract", name="未配置采集对象"
    )

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
    t_sync_a = await _create_task(
        client, session_factory, ds.id, name="对象存储同步"
    )
    await _create_task(client, session_factory, ds.id, name="日志增量同步")
    await _create_task(client, session_factory, ds.id, name="全量导出")

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
    """PG 真实拉取(治理改造:湖优先):运行产 job(type=ingest),数据入湖为
    source_v 快照(不再直落数据集),快照追溯字段指向任务/数据源。"""
    from urllib.parse import urlparse

    from tests.conftest import TEST_DATABASE_URL

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

    lake_id = await _seed_lake(session_factory, "lake-pgself")
    resp = await client.post(
        "/api/v1/ingest-tasks",
        json={
            "name": "PG真实采集",
            "datasourceId": "ds-pg-self",
            "lakeId": lake_id,
            "schedule": {"mode": "once"},
            "extract": {"mode": "sql", "sql": "SELECT 1 AS num, 'hi' AS text"},
        },
    )
    assert resp.status_code == 200, resp.text
    task_id = resp.json()["data"]["id"]

    # 运行:同步真实拉取 → 入湖
    resp = await client.post(f"/api/v1/ingest-tasks/{task_id}/rerun")
    body = resp.json()
    assert body["data"]["status"] == "success", body
    assert body["data"]["runCount"] == 1
    assert any("已入湖" in line for line in body["data"]["logs"])

    # jobs 表多了一条 type=ingest,且通用 /jobs 列表可见
    resp = await client.get("/api/v1/jobs", params={"type": "ingest"})
    jobs = resp.json()["data"]
    assert len(jobs) == 1 and jobs[0]["type"] == "ingest"
    job_id = jobs[0]["id"]

    # 产物是湖快照(parquet 归档),追溯字段指向任务/数据源;不产生数据集版本
    async with session_factory() as session:
        from sqlalchemy import select

        from app.models.data_lake import DataLakeSnapshot
        from app.models.dataset_version import DatasetVersion as DV

        snap = (await session.scalars(select(DataLakeSnapshot))).one()
        assert snap.lake_id == lake_id
        assert snap.ingest_task_id == task_id
        assert snap.datasource_id == "ds-pg-self"
        assert snap.rows == 1
        assert snap.storage_format == "parquet"
        assert snap.source_version.endswith("_postgresql")

        versions = (await session.scalars(select(DV))).all()
        assert versions == []  # 湖优先:采集不再直落数据集

    # runs 端点:job 记录仍在,数据集产物为 0(数据在湖里)
    resp = await client.get(f"/api/v1/ingest-tasks/{task_id}/runs")
    body = resp.json()
    assert body["total"] == 1
    run = body["data"][0]
    assert run["id"] == job_id
    assert run["status"] == "success"
    assert run["datasetCount"] == 0


@pytest.mark.asyncio
async def test_runs_empty_for_task_without_jobs(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    """无运行记录的任务:runs 端点返回空列表。"""
    ds = await _seed_datasource(session_factory)
    task = await _create_task(client, session_factory, ds.id)
    resp = await client.get(f"/api/v1/ingest-tasks/{task['id']}/runs")
    body = resp.json()
    assert body["total"] == 0 and body["data"] == []


# ---------------------------------------------------------------------------
# PG 族注册表派发(§4.4 / §9):hologres/kingbase/gaussdb 走同一 PgConnector
# 用自引用 PG(datasource.config 指向测试库自身)各跑一例。
# 诚实边界:只证 db_kind 字符串经 resolve() 路由到 PgConnector 并真 SELECT 落地,
# **不证明对真实 hologres/kingbase/gaussdb 的方言/系统表/权限兼容**(那是承诺级)。
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
@pytest.mark.parametrize("db_kind", ["hologres", "kingbase", "gaussdb"])
async def test_pg_family_db_kinds_route_through_pgconnector(
    client: AsyncClient,
    session_factory: async_sessionmaker,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
    db_kind: str,
) -> None:
    """hologres/kingbase/gaussdb 经注册表派发到 PgConnector,自引用 PG 真 SELECT 入湖。

    锁的意图:这三个 db_kind 不是空壳——它们经 resolve(("database", db_kind)) 命中
    PgConnector(PG 线协议复用),run_ingest 走真实 asyncpg SELECT 并入湖为
    source_v 快照(治理改造:湖优先),快照 source_version 尾缀 = db_kind。
    """
    from urllib.parse import urlparse

    from tests.conftest import TEST_DATABASE_URL

    # 数据源指向测试库自身,但 db_kind 标成被测的 PG 族品牌
    u = urlparse(TEST_DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://"))
    ds_id = f"ds-pgfam-{db_kind}"
    async with session_factory() as session:
        session.add(
            DataSource(
                id=ds_id,
                name=f"{db_kind}自引用",
                type="database",
                db_kind=db_kind,
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

    lake_id = await _seed_lake(session_factory, f"lake-fam-{db_kind[:4]}")
    resp = await client.post(
        "/api/v1/ingest-tasks",
        json={
            "name": f"{db_kind}采集",
            "datasourceId": ds_id,
            "lakeId": lake_id,
            "schedule": {"mode": "once"},
            "extract": {"mode": "sql", "sql": "SELECT 42 AS answer"},
        },
    )
    assert resp.status_code == 200, resp.text
    task_id = resp.json()["data"]["id"]

    # 真实拉取:经 PgConnector.run_ingest 入湖
    resp = await client.post(f"/api/v1/ingest-tasks/{task_id}/rerun")
    body = resp.json()
    assert body["data"]["status"] == "success", body

    # 湖快照:真 SELECT 一行,source_version 尾缀标 db_kind
    async with session_factory() as session:
        from sqlalchemy import select

        from app.models.data_lake import DataLakeSnapshot

        snap = (await session.scalars(select(DataLakeSnapshot))).one()
        assert snap.lake_id == lake_id
        assert snap.rows == 1
        assert snap.source_version.endswith(f"_{db_kind}")


# ---------------------------------------------------------------------------
# 治理改造:湖归档保持 parquet;存量数据集任务(仅 dataset_id)保持旧路径落 jsonl
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_pg_rerun_legacy_dataset_task_lands_jsonl(
    client: AsyncClient,
    session_factory: async_sessionmaker,
    tmp_path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """存量任务(有 dataset_id、无 lake_id)rerun 仍直落数据集,成员为 jsonl。

    锁两个意图:① lake_id 为空的存量任务不被湖改造破坏(向后兼容);
    ② 数据集成员统一 jsonl(parquet→jsonl 契约,下游 DJ/训练交付)。
    创建走直插 DB(新建 API 已强制 lakeId,存量任务只能来自历史数据)。
    """
    from urllib.parse import urlparse

    from app.core.config import settings
    from tests.conftest import TEST_DATABASE_URL

    monkeypatch.setattr(settings, "datasets_dir", str(tmp_path))

    u = urlparse(TEST_DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://"))
    async with session_factory() as session:
        from app.models.datasource import DataSource as DS

        session.add(
            DS(
                id="ds-pg-legacy",
                name="存量任务测试库",
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

    did = (await client.post(
        "/api/v1/datasets", json={"name": "存量采集-目标集"}
    )).json()["data"]["id"]

    # 存量任务直插 DB:dataset_id 有值、lake_id 为空(历史数据形态)
    async with session_factory() as session:
        from app.models.ingest_task import IngestTask

        session.add(
            IngestTask(
                id="task-legacy",
                name="存量数据集任务",
                datasource_id="ds-pg-legacy",
                datasource_name="存量任务测试库",
                dataset_id=did,
                lake_id=None,
                schedule={"mode": "once"},
                extract={"mode": "sql", "sql": "SELECT 1 AS n, 'hello' AS s"},
                status="pending",
                progress=0,
                logs=["[INFO] 任务已创建"],
                creator="admin",
            )
        )
        await session.commit()

    resp = await client.post("/api/v1/ingest-tasks/task-legacy/rerun")
    body = resp.json()
    assert body["data"]["status"] == "success", body

    async with session_factory() as session:
        from sqlalchemy import select

        from app.models.data_lake import DataLakeSnapshot
        from app.models.dataset_version import DatasetVersion as DV

        version = (await session.scalars(select(DV))).one()
        assert version.format == "jsonl", f"期望 jsonl,实际 {version.format!r}"
        assert version.rows == 1

        # 存量路径不写湖
        snaps = (await session.scalars(select(DataLakeSnapshot))).all()
        assert snaps == []
