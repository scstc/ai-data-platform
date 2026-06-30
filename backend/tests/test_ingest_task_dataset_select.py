import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker


async def _seed_s3_datasource(session_factory: async_sessionmaker) -> str:
    """造一个最小可用数据源(类型 s3),返回其 id。"""
    from app.models.datasource import DataSource

    async with session_factory() as s:
        ds = DataSource(
            id="ds-task8",
            name="task8源",
            type="s3",
            status="active",
            config={"bucket": "b", "endpoint": "x", "accessKey": "a", "secretKey": "k"},
            creator="admin",
        )
        s.add(ds)
        await s.commit()
    return "ds-task8"


@pytest.mark.asyncio
async def test_ingest_task_requires_dataset_id(
    client: AsyncClient, session_factory
) -> None:
    """缺 datasetId → 422(必填字段)。"""
    src = await _seed_s3_datasource(session_factory)
    resp = await client.post(
        "/api/v1/ingest-tasks",
        json={
            "name": "无目标集任务",
            "datasourceId": src,
            "schedule": {"mode": "once"},
            "extract": {"mode": "table", "tables": ["public.users"]},
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_ingest_task_unknown_dataset_404(
    client: AsyncClient, session_factory
) -> None:
    """datasetId 指向不存在的数据集 → 404。"""
    src = await _seed_s3_datasource(session_factory)
    resp = await client.post(
        "/api/v1/ingest-tasks",
        json={
            "name": "坏目标集任务",
            "datasourceId": src,
            "datasetId": "dset-nope",
            "schedule": {"mode": "once"},
            "extract": {"mode": "table", "tables": ["public.users"]},
        },
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_ingest_task_persists_dataset_id(
    client: AsyncClient, session_factory
) -> None:
    """合法 datasetId → 200 且任务持久化 datasetId。"""
    src = await _seed_s3_datasource(session_factory)
    ds = (await client.post("/api/v1/datasets", json={"name": "采集目标"})).json()[
        "data"
    ]["id"]
    resp = await client.post(
        "/api/v1/ingest-tasks",
        json={
            "name": "正常采集任务",
            "datasourceId": src,
            "datasetId": ds,
            "schedule": {"mode": "once"},
            "extract": {"mode": "table", "tables": ["public.users"]},
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["datasetId"] == ds
