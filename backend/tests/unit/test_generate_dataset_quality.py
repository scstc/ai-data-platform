"""generate-dataset 路由应用 quality_policy + schema 漂移对比测试(切片 B / Task 5)。

锁的意图(见 task-5-brief):
- generate-dataset 是唯一会在同一数据集上累积 v1/v2/... 的入口,所以 schema drift
  比对在此处**有意义**(land_records / rerun 总是新建首版,无前序可比)。
- 首版本:无 prev → drift 三桶皆空;即便 block_on_schema_drift=True 也不阻断。
- 第二版本新增列:drift_diff 检出 added;block_on_schema_drift=True →
  版本 quality_verdict="failed"、响应 data.qualityVerdict="failed"、
  reason 含被加列名;**版本照常落地**(不 400/不删除,spec「阻断发布门不删数据」)。
- max_null_rate 超阈 → failed。
- quality_policy=None → verdict="skipped",版本正常落地,无回归。

避开真实 PG / MinIO:monkeypatch ``_fetch_db_records`` 返回受控 records,
monkeypatch ``upload_*_to_uploads`` 返回假 URI;真走 DB(session_factory),
因路由全程依赖 session 读写 task/dataset/version。
"""

from __future__ import annotations

from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.dataset_version import DatasetVersion
from app.models.datasource import DataSource


async def _seed_pg_datasource(
    session_factory: async_sessionmaker,
    ds_id: str = "ds-gdq01",
) -> str:
    """落库一个 PG 数据源(generate-dataset 仅支持 PG 族 / goldendb)。"""
    async with session_factory() as session:
        session.add(
            DataSource(
                id=ds_id,
                name=f"质量门生成源-{ds_id}",
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


def _patch_fetch_and_uploads(
    monkeypatch: pytest.MonkeyPatch,
    records_per_call: list[list[dict[str, Any]]],
) -> dict[str, int]:
    """拦截 _fetch_db_records(按调用顺序返回不同 records)+ 上传函数(返回假 URI)。

    records_per_call[i] 是第 i+1 次 generate-dataset 调用所返回的 records。
    返回共享 state dict,供测试观察调用次数。
    """
    state = {"calls": 0}

    async def _fake_fetch(datasource, task):  # noqa: ANN001
        idx = state["calls"]
        state["calls"] += 1
        return records_per_call[idx]

    async def _fake_upload_jsonl(dataset_id, version_no, blob):  # noqa: ANN001
        return f"file://fake/{dataset_id}/v{version_no}/data.jsonl"

    async def _fake_upload_parquet(dataset_id, version_no, blob):  # noqa: ANN001
        return f"file://fake/{dataset_id}/v{version_no}/data.parquet"

    monkeypatch.setattr(
        "app.api.v1.ingest_tasks._fetch_db_records", _fake_fetch
    )
    monkeypatch.setattr(
        "app.api.v1.ingest_tasks.upload_jsonl_to_uploads", _fake_upload_jsonl
    )
    monkeypatch.setattr(
        "app.services.external_store.upload_parquet_to_uploads",
        _fake_upload_parquet,
    )
    return state


async def _create_task(
    client: AsyncClient,
    ds_id: str,
    *,
    quality_policy: dict | None = None,
    name: str = "生成任务",
) -> str:
    """创建采集任务(带可选 qualityPolicy),返回 task_id。"""
    payload: dict[str, Any] = {
        "name": name,
        "datasourceId": ds_id,
        "schedule": {"mode": "once"},
        "extract": {"mode": "sql", "sql": "SELECT 1"},
    }
    if quality_policy is not None:
        payload["qualityPolicy"] = quality_policy
    resp = await client.post("/api/v1/ingest-tasks", json=payload)
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]["id"]


@pytest.mark.asyncio
async def test_first_version_no_drift_even_with_block_policy(
    client: AsyncClient,
    session_factory: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """首版本:无 prev → drift 三桶皆空;block_on_schema_drift=True 仍 passed。

    锁意图:drift 基线需前一版本提供;首版 prev=None → drift_diff 短路返回空,
    block_on_schema_drift 闸门无东西可拦 → verdict="passed"。版本正常落地。
    """
    ds_id = await _seed_pg_datasource(session_factory, ds_id="ds-gdq-first")
    _patch_fetch_and_uploads(
        monkeypatch,
        records_per_call=[
            [
                {"id": 1, "name": "alice"},
                {"id": 2, "name": "bob"},
            ],
        ],
    )
    task_id = await _create_task(
        client,
        ds_id,
        quality_policy={"blockOnSchemaDrift": True},
        name="首版本无漂移",
    )

    resp = await client.post(f"/api/v1/ingest-tasks/{task_id}/generate-dataset")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    # 首版无漂移可拦 → verdict="passed"(配置了策略但没违例)
    assert body["data"]["qualityVerdict"] == "passed"
    assert body["data"]["versionNo"] == 1

    # 版本落库:quality_stats / schema_snapshot 已写,verdict="passed"
    async with session_factory() as session:
        version = (await session.scalars(select(DatasetVersion))).one()
        assert version.quality_verdict == "passed"
        assert version.quality_stats is not None
        assert version.quality_stats["rows"] == 2
        assert version.schema_snapshot is not None
        snap_names = {c["name"] for c in version.schema_snapshot}
        assert {"id", "name"}.issubset(snap_names)


@pytest.mark.asyncio
async def test_second_version_added_column_blocked_by_drift_policy(
    client: AsyncClient,
    session_factory: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """v1 cols=[id,name] → v2 cols=[id,name,extra]:drift_diff 检出 added=extra。

    block_on_schema_drift=True → v2 verdict="failed",reason 含 "+extra";
    **版本照常落地**(不 400/不删除——spec「阻断发布门不删数据」)。
    """
    ds_id = await _seed_pg_datasource(session_factory, ds_id="ds-gdq-drift")
    _patch_fetch_and_uploads(
        monkeypatch,
        records_per_call=[
            # v1:两列
            [
                {"id": 1, "name": "alice"},
                {"id": 2, "name": "bob"},
            ],
            # v2:新增 extra 列 → drift_diff added=["extra"]
            [
                {"id": 1, "name": "alice", "extra": "x"},
                {"id": 2, "name": "bob", "extra": "y"},
            ],
        ],
    )
    task_id = await _create_task(
        client,
        ds_id,
        quality_policy={"blockOnSchemaDrift": True},
        name="漂移阻断",
    )

    # 第一次:首版无漂移 → passed
    resp = await client.post(f"/api/v1/ingest-tasks/{task_id}/generate-dataset")
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["qualityVerdict"] == "passed"

    # 第二次:v2 新增 extra 列 → drift → failed(数据仍落地)
    resp = await client.post(f"/api/v1/ingest-tasks/{task_id}/generate-dataset")
    assert resp.status_code == 200, resp.text  # 不 400
    body = resp.json()
    assert body["success"] is True  # 数据落地成功
    data = body["data"]
    assert data["versionNo"] == 2
    assert data["qualityVerdict"] == "failed"
    # reason 含被加列名(extra)
    reason = data.get("qualityReason", "")
    assert "extra" in reason
    assert "schema" in reason  # 漂移原因前缀

    # 版本表:v1=passed / v2=failed 都已落地(没删)
    async with session_factory() as session:
        versions = (
            await session.scalars(
                select(DatasetVersion).order_by(DatasetVersion.version_no)
            )
        ).all()
        assert len(versions) == 2, "两个版本都应落地(failed 不删数据)"
        assert versions[0].quality_verdict == "passed"
        assert versions[1].quality_verdict == "failed"
        # v2 的 schema_snapshot 含新列
        v2_names = {c["name"] for c in versions[1].schema_snapshot or []}
        assert "extra" in v2_names


@pytest.mark.asyncio
async def test_max_null_rate_exceeded_fails(
    client: AsyncClient,
    session_factory: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """maxNullRate=0.2 + 落地版本 dirty_col 空值率 0.5 > 0.2 → failed。

    锁意图:空值率阈值闸门与 schema drift 闸门独立,前者先评估
    (evaluate_policy 短路顺序:null → drift → passed)。
    """
    ds_id = await _seed_pg_datasource(session_factory, ds_id="ds-gdq-null")
    _patch_fetch_and_uploads(
        monkeypatch,
        records_per_call=[
            [
                {"id": 1, "name": "a"},
                {"id": 2, "name": ""},  # 空串算 null
            ],  # name 列 null_rate=0.5
        ],
    )
    task_id = await _create_task(
        client,
        ds_id,
        quality_policy={"maxNullRate": 0.2},
        name="空值率超阈",
    )

    resp = await client.post(f"/api/v1/ingest-tasks/{task_id}/generate-dataset")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["data"]["qualityVerdict"] == "failed"
    reason = body["data"].get("qualityReason", "")
    assert "name" in reason  # 列名
    assert "0.50" in reason  # 实际空值率
    assert "0.20" in reason  # 阈值

    # 版本仍落地,verdict=failed
    async with session_factory() as session:
        version = (await session.scalars(select(DatasetVersion))).one()
        assert version.quality_verdict == "failed"
        assert version.rows == 2  # 数据完整保留


@pytest.mark.asyncio
async def test_no_policy_skipped_lands_normally(
    client: AsyncClient,
    session_factory: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """quality_policy=None → verdict="skipped",版本正常落地,行为零回归。

    已存在的无策略任务行为不变:即便列空值率高达 0.5、schema 跨版本变化,
    也不做质量门检查(无策略 = 无检查)。
    """
    ds_id = await _seed_pg_datasource(session_factory, ds_id="ds-gdq-nopolicy")
    _patch_fetch_and_uploads(
        monkeypatch,
        records_per_call=[
            [{"id": 1, "name": ""}],  # 空值率高,但无策略 → 不评估
        ],
    )
    task_id = await _create_task(client, ds_id, name="无策略不阻断")

    resp = await client.post(f"/api/v1/ingest-tasks/{task_id}/generate-dataset")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["qualityVerdict"] == "skipped"
    # 无违例时响应不带 qualityReason
    assert "qualityReason" not in body["data"]

    async with session_factory() as session:
        version = (await session.scalars(select(DatasetVersion))).one()
        assert version.quality_verdict == "skipped"
        # quality_stats / schema_snapshot 仍写(供后续启用策略时回看)
        assert version.quality_stats is not None
        assert version.schema_snapshot is not None
