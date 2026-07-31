"""采集/连接器/入湖缺陷修复的 DB 级回归(编码业务意图)。

覆盖本轮修复:
- §4 入湖任务静默失效的质量门:``lake_id`` + ``quality_policy`` 组合在入湖路径上
  没有落点(不产数据集版本),必须**在建任务时就拒绝**而非让用户以为质量门生效。
- §5 rerun 并发闸:任务正在运行时重复触发(双击 / cron+manual 撞车)必须 409,
  不能并发跑两次污染同一 draft。
- §9 推送幂等改 DB 持久:同一 ``idempotency_key`` 有效期内重放返回**同一版本**、
  不重复落地;过期后同 key 可重新落地(证明不是永久锁死,也不是永不去重)。

这些断言绑定「为什么」——质量门不能静默、并发不能重入、幂等要跨请求生效且会过期;
若日后有人把拦截去掉或把幂等退回进程内字典,用例即失败。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.data_lake import DataLake
from app.models.datasource import DataSource
from app.models.ingest_task import IngestTask
from app.models.push_idempotency import PushIdempotency
from app.services.connectors.push import land_push_records


@pytest.fixture(autouse=True)
def _datasets_dir(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """推送落地写 jsonl 到 settings.datasets_dir:指到 tmp,与本机环境解耦
    (同 test_ingest_push.py 的同名 fixture)。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "datasets_dir", str(tmp_path))


async def _seed_ds_and_lake(
    session_factory: async_sessionmaker,
    *,
    ds_id: str = "ds-fix01",
    lake_id: str = "lake-fix01",
) -> tuple[str, str]:
    """落一个数据源 + 数据湖,供建任务引用。"""
    async with session_factory() as session:
        if await session.get(DataSource, ds_id) is None:
            session.add(
                DataSource(
                    id=ds_id,
                    name="修复测试源",
                    type="database",
                    db_kind="postgresql",
                    status="connected",
                    config={"host": "h", "database": "d"},
                    creator="admin",
                )
            )
        if await session.get(DataLake, lake_id) is None:
            session.add(DataLake(id=lake_id, name="修复测试湖"))
        await session.commit()
    return ds_id, lake_id


# --------------------------------------------------------------------------- §4
@pytest.mark.asyncio
async def test_create_rejects_lake_task_with_quality_policy(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    """入湖任务带 quality_policy → 400(拒绝静默失效的质量门组合)。"""
    ds_id, lake_id = await _seed_ds_and_lake(session_factory)
    resp = await client.post(
        "/api/v1/ingest-tasks",
        json={
            "name": "带质量门的入湖任务",
            "datasourceId": ds_id,
            "lakeId": lake_id,
            "schedule": {"mode": "once"},
            "qualityPolicy": {"maxNullRate": 0.2},
        },
    )
    assert resp.status_code == 400, resp.text
    body = resp.json()
    assert body["success"] is False
    assert "quality" in body["message"].lower() or "质量" in body["message"]


@pytest.mark.asyncio
async def test_create_allows_lake_task_without_quality_policy(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    """不带 quality_policy 的入湖任务照常创建(拦截仅针对该无效组合,不误伤)。"""
    ds_id, lake_id = await _seed_ds_and_lake(session_factory)
    resp = await client.post(
        "/api/v1/ingest-tasks",
        json={
            "name": "普通入湖任务",
            "datasourceId": ds_id,
            "lakeId": lake_id,
            "schedule": {"mode": "once"},
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["status"] == "pending"


# --------------------------------------------------------------------------- §5
@pytest.mark.asyncio
async def test_rerun_conflicts_when_task_running(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    """任务正处于 running → rerun 返回 409,不重入执行。"""
    ds_id, lake_id = await _seed_ds_and_lake(session_factory)
    create = await client.post(
        "/api/v1/ingest-tasks",
        json={
            "name": "运行中任务",
            "datasourceId": ds_id,
            "lakeId": lake_id,
            "schedule": {"mode": "once"},
        },
    )
    task_id = create.json()["data"]["id"]

    # 直接把状态置 running,模拟另一次运行正在进行
    async with session_factory() as session:
        task = await session.get(IngestTask, task_id)
        task.status = "running"
        await session.commit()

    resp = await client.post(f"/api/v1/ingest-tasks/{task_id}/rerun")
    assert resp.status_code == 409, resp.text
    assert resp.json()["success"] is False


# --------------------------------------------------------------------------- §9
async def _make_api_datasource(session, ds_id: str = "ds-push-fix") -> DataSource:
    ds = DataSource(
        id=ds_id,
        name="推送源",
        type="api",
        status="active",
        config={},
        creator="admin",
    )
    session.add(ds)
    await session.commit()
    await session.refresh(ds)
    return ds


@pytest.mark.asyncio
async def test_push_idempotency_dedup_persisted(db_session) -> None:
    """同一 idempotency_key 有效期内重放 → 返回同一版本,不新增版本(DB 去重)。"""
    ds = await _make_api_datasource(db_session)
    v1, deduped1 = await land_push_records(
        db_session, ds, [{"a": 1}], idempotency_key="k-dup"
    )
    v2, deduped2 = await land_push_records(
        db_session, ds, [{"a": 2}], idempotency_key="k-dup"
    )
    # 幂等命中:第二次未落地新版本,返回首次版本,deduped 明示
    assert not deduped1
    assert deduped2
    assert v2.id == v1.id
    assert v2.version_no == v1.version_no
    # 幂等记录已持久到 DB(不是进程内字典)
    row = await db_session.get(PushIdempotency, "k-dup")
    assert row is not None
    assert row.dataset_version_id == v1.id


@pytest.mark.asyncio
async def test_push_idempotency_expires_allows_relanding(db_session) -> None:
    """幂等记录过期后,同 key 重新落地为新版本(证明不是永久锁死)。"""
    ds = await _make_api_datasource(db_session, ds_id="ds-push-exp")
    v1, _ = await land_push_records(
        db_session, ds, [{"a": 1}], idempotency_key="k-exp"
    )
    # 人为把该 key 记录置为已过期
    row = await db_session.get(PushIdempotency, "k-exp")
    row.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await db_session.commit()

    v2, deduped = await land_push_records(
        db_session, ds, [{"a": 2}], idempotency_key="k-exp"
    )
    assert not deduped
    assert v2.id != v1.id
    assert v2.version_no == v1.version_no + 1
