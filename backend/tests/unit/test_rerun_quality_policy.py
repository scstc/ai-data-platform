"""rerun 路由应用 quality_policy 的测试(切片 B / Task 4)。

锁的意图:
- 任务带 qualityPolicy.maxNullRate=0.2、落地版本存在空值率 0.3 的列 →
  版本 quality_verdict="failed"、task.status="failed"、job.state="failed",
  日志含 [ERROR] 质量门未通过 + 列名 + 空值率(0.30) + 阈值(0.20)。
- 任务 quality_policy=None → 版本 quality_verdict="skipped",rerun 正常成功
  (已存在的无策略任务行为不变,不阻断)。

避开真实 PG 拉取(关注点是质量门判定而非拉取):monkeypatch 连接器 resolve
返回假 connector,其 run_ingest 直接构造带指定 quality_stats 的版本。
仍走 DB(经 client fixture),因为路由全程依赖 session 读写 task/job/version。
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.dataset import Dataset
from app.models.dataset_version import DatasetVersion
from app.models.datasource import DataSource
from app.models.job import Job


class _FakeConnector:
    """假连接器:跳过真实拉取,直接构造带预设 quality_stats 的落地版本。

    land_records 在真实路径里已写 quality_stats/schema_snapshot(verdict 默认 skipped);
    这里手动复刻同一产物形态,供 rerun 路由的 quality_policy 评估读取。
    """

    def __init__(self, quality_stats: dict, rows: int = 10) -> None:
        self._quality_stats = quality_stats
        self._rows = rows

    async def run_ingest(self, session, task, datasource, *, job_id):
        dataset = Dataset(
            id="dset-fake01",
            name="假数据集",
            data_type="sql",
            semantic_type="structured",
            creator="admin",
        )
        session.add(dataset)
        await session.flush()
        version = DatasetVersion(
            id="dsv-fake01",
            dataset_id=dataset.id,
            version_no=1,
            storage_uri="file://fake/data.jsonl",
            format="jsonl",
            rows=self._rows,
            size=100,
            origin="managed",
            semantic_type="structured",
            produced_by_job_id=job_id,
            quality_stats=self._quality_stats,
            schema_snapshot=[
                {"name": c["name"], "type": c["type"]}
                for c in self._quality_stats.get("columns", [])
            ],
        )
        session.add(version)
        await session.commit()
        await session.refresh(dataset)
        await session.refresh(version)
        return [(dataset, version)]


def _patch_resolve(monkeypatch: pytest.MonkeyPatch, quality_stats: dict) -> None:
    """把 ingest_tasks 路由里的 resolve 替换成返回 _FakeConnector 的桩。"""
    import app.api.v1.ingest_tasks as route_mod

    monkeypatch.setattr(
        route_mod, "resolve", lambda *a, **kw: _FakeConnector(quality_stats)
    )


async def _seed_datasource(
    session_factory: async_sessionmaker, ds_id: str = "ds-qp01"
) -> str:
    async with session_factory() as session:
        session.add(
            DataSource(
                id=ds_id,
                name="质量门测试源",
                type="database",
                db_kind="postgresql",
                status="connected",
                config={
                    "host": "x",
                    "port": 5432,
                    "database": "d",
                    "username": "u",
                    "password": "p",
                },
                creator="admin",
            )
        )
        await session.commit()
    return ds_id


@pytest.mark.asyncio
async def test_rerun_blocks_on_high_null_rate(
    client: AsyncClient,
    session_factory: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """maxNullRate=0.2 + 落地版本含空值率 0.3 的列 → 版本/任务/job 均 failed。"""
    ds_id = await _seed_datasource(session_factory)
    # dirty_col 空值率 0.3 > 阈值 0.2 → 应阻断
    _patch_resolve(
        monkeypatch,
        {
            "rows": 10,
            "columns": [
                {"name": "clean_col", "type": "integer", "null_rate": 0.0},
                {"name": "dirty_col", "type": "text", "null_rate": 0.3},
            ],
        },
    )

    resp = await client.post(
        "/api/v1/ingest-tasks",
        json={
            "name": "质量门阻断测试",
            "datasourceId": ds_id,
            "schedule": {"mode": "once"},
            # extract 只用于过校验,真实拉取被 monkeypatch 跳过
            "extract": {"mode": "sql", "sql": "SELECT 1"},
            "qualityPolicy": {"maxNullRate": 0.2},
        },
    )
    assert resp.status_code == 200, resp.text
    task_id = resp.json()["data"]["id"]

    resp = await client.post(f"/api/v1/ingest-tasks/{task_id}/rerun")
    assert resp.status_code == 200
    data = resp.json()["data"]
    # 任务级:failed
    assert data["status"] == "failed"

    # 日志:含列名 + 实际空值率 0.30 + 阈值 0.20 + 质量门未通过标识
    joined = "\n".join(data["logs"])
    assert "质量门未通过" in joined
    assert "dirty_col" in joined
    assert "0.30" in joined
    assert "0.20" in joined

    # 版本 quality_verdict="failed"、job.state="failed"
    async with session_factory() as session:
        version = (await session.scalars(select(DatasetVersion))).one()
        assert version.quality_verdict == "failed"
        job = (await session.scalars(select(Job))).one()
        assert job.state == "failed"


@pytest.mark.asyncio
async def test_rerun_skipped_when_no_policy(
    client: AsyncClient,
    session_factory: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """quality_policy=None → 版本 skipped,rerun 正常成功,不阻断。

    已存在的无策略任务行为不变:即使列空值率高达 0.99,也不做质量门检查。
    """
    ds_id = await _seed_datasource(session_factory, ds_id="ds-qp02")
    _patch_resolve(
        monkeypatch,
        {
            "rows": 10,
            "columns": [
                {"name": "dirty_col", "type": "text", "null_rate": 0.99},
            ],
        },
    )

    resp = await client.post(
        "/api/v1/ingest-tasks",
        json={
            "name": "无策略不阻断",
            "datasourceId": ds_id,
            "schedule": {"mode": "once"},
            "extract": {"mode": "sql", "sql": "SELECT 1"},
        },
    )
    assert resp.status_code == 200, resp.text
    task_id = resp.json()["data"]["id"]

    resp = await client.post(f"/api/v1/ingest-tasks/{task_id}/rerun")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "success"

    async with session_factory() as session:
        version = (await session.scalars(select(DatasetVersion))).one()
        assert version.quality_verdict == "skipped"
        job = (await session.scalars(select(Job))).one()
        assert job.state == "success"
