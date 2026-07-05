"""数据湖三层模型(湖/文件/版本)单测。

验证：
- derive_identity_key 四条身份键规则
- 同 identity 多次入湖归到同一 DataLakeObject，版本号在对象维度自增
- 不同 identity 各自建 DataLakeObject
- 湖内合并 union / join 及其校验分支
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.models.data_lake import DataLakeObject, DataLakeSnapshot
from app.services import data_lake as data_lake_service
from app.services.data_lake import (
    create_data_lake,
    derive_identity_key,
    ingest_to_lake_parquet,
    ingest_to_lake_raw,
    merge_lake_objects,
)
from app.services.external_store import ExternalStoreError

# --------------------------------------------------------------------------
# derive_identity_key 四条规则
# --------------------------------------------------------------------------


def test_derive_identity_key_db_table():
    key, display = derive_identity_key("ds-pg01", {"db_table": "public.orders"})
    assert key == "ds-pg01:public.orders"
    assert display == "public.orders"


def test_derive_identity_key_obj_key():
    key, display = derive_identity_key(
        "ds-s3-01", {"bucket_name": "bkt", "obj_key": "raw/2026/report.parquet"}
    )
    assert key == "ds-s3-01:bkt/raw/2026/report.parquet"
    assert display == "report.parquet"


def test_derive_identity_key_hdfs_path():
    key, display = derive_identity_key(
        "ds-hdfs01", {"hdfs_path": "/warehouse/orders/part-0001.parquet"}
    )
    assert key == "ds-hdfs01:/warehouse/orders/part-0001.parquet"
    assert display == "part-0001.parquet"


def test_derive_identity_key_local_fallback():
    key, display = derive_identity_key(None, {}, original_filename="销售.csv")
    assert key == "local:销售.csv"
    assert display == "销售.csv"


# --------------------------------------------------------------------------
# 同 identity 多次入湖 → 同一 DataLakeObject，版本号自增
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_same_identity_ingest_reuses_object_and_increments_version(
    db_session, monkeypatch
):
    """两张来源同一 db_table 的数据先后入湖 → 归到同一个 DataLakeObject，
    version_no 1→2，latest_* 推进到最新快照。"""
    monkeypatch.setattr(
        data_lake_service, "_put_object_to_lake_minio", AsyncMock(return_value=None)
    )

    lake = await create_data_lake(db_session, name="同身份入湖测试湖")

    snap1 = await ingest_to_lake_parquet(
        db_session,
        lake_id=lake.id,
        data=[{"id": 1}],
        source_type="mysql",
        source_metadata={"db_schema": "public", "db_table": "public.orders"},
        datasource_id="ds-mysql01",
        job_id="job-001",
    )
    snap2 = await ingest_to_lake_parquet(
        db_session,
        lake_id=lake.id,
        data=[{"id": 1}, {"id": 2}],
        source_type="mysql",
        source_metadata={"db_schema": "public", "db_table": "public.orders"},
        datasource_id="ds-mysql01",
        job_id="job-002",
    )

    assert snap1.object_id == snap2.object_id
    assert snap1.version_no == 1
    assert snap2.version_no == 2
    assert snap1.job_id == "job-001"
    assert snap2.job_id == "job-002"

    obj = await db_session.get(DataLakeObject, snap2.object_id)
    assert obj.latest_version_no == 2
    assert obj.latest_snapshot_id == snap2.id
    assert obj.identity_key == "ds-mysql01:public.orders"
    assert obj.origin == "ingested"


@pytest.mark.asyncio
async def test_different_identity_creates_separate_objects(db_session, monkeypatch):
    """不同表/文件各自建自己的 DataLakeObject，version_no 各自从 1 起。"""
    monkeypatch.setattr(
        data_lake_service, "_put_object_to_lake_minio", AsyncMock(return_value=None)
    )

    lake = await create_data_lake(db_session, name="不同身份测试湖")

    snap_orders = await ingest_to_lake_parquet(
        db_session,
        lake_id=lake.id,
        data=[{"id": 1}],
        source_type="mysql",
        source_metadata={"db_table": "orders"},
        datasource_id="ds-mysql01",
    )
    snap_users = await ingest_to_lake_parquet(
        db_session,
        lake_id=lake.id,
        data=[{"id": 1}],
        source_type="mysql",
        source_metadata={"db_table": "users"},
        datasource_id="ds-mysql01",
    )

    assert snap_orders.object_id != snap_users.object_id
    assert snap_orders.version_no == 1
    assert snap_users.version_no == 1


@pytest.mark.asyncio
async def test_raw_ingest_resolves_object_by_local_filename(db_session, monkeypatch):
    """本地文档入湖走 ingest_to_lake_raw，同名文件二次上传落到同一对象 v2。"""
    monkeypatch.setattr(
        data_lake_service, "_put_object_to_lake_minio", AsyncMock(return_value=None)
    )

    lake = await create_data_lake(db_session, name="原格式入湖测试湖")

    snap1 = await ingest_to_lake_raw(
        db_session,
        lake_id=lake.id,
        file_content=b"hello",
        original_filename="report.pdf",
        data_category="document",
    )
    snap2 = await ingest_to_lake_raw(
        db_session,
        lake_id=lake.id,
        file_content=b"hello v2",
        original_filename="report.pdf",
        data_category="document",
    )

    assert snap1.object_id == snap2.object_id
    assert snap1.version_no == 1
    assert snap2.version_no == 2


# --------------------------------------------------------------------------
# 湖内合并
# --------------------------------------------------------------------------


async def _seed_parquet_object(
    db_session, lake_id: str, *, object_id: str, snapshot_id: str, columns: dict
):
    """构造一个已有 parquet 版本的 DataLakeObject + DataLakeSnapshot,
    供合并测试用(不走真实入湖流程,直接手工插行)。"""
    obj = DataLakeObject(
        id=object_id,
        lake_id=lake_id,
        identity_key=f"ds:{object_id}",
        display_name=object_id,
        data_category="database",
        storage_format="parquet",
        latest_version_no=1,
        latest_snapshot_id=snapshot_id,
    )
    db_session.add(obj)
    snap = DataLakeSnapshot(
        id=snapshot_id,
        lake_id=lake_id,
        source_version=f"source_v20260701_01_{object_id}",
        storage_uri=f"s3://adp-data-lake/data-lake/x/{object_id}/v1/data.parquet",
        storage_format="parquet",
        data_category="database",
        upload_channel="database",
        object_id=object_id,
        version_no=1,
    )
    db_session.add(snap)
    await db_session.commit()
    return obj, snap


@pytest.mark.asyncio
async def test_merge_union_creates_new_merged_object(db_session, monkeypatch):
    """union 合并:两个 parquet 文件纵向拼接成新 merged 文件,版本号 1,
    记录 merge_inputs 血缘。"""
    import pandas as pd

    lake = await create_data_lake(db_session, name="合并 union 测试湖")
    obj_a, snap_a = await _seed_parquet_object(
        db_session, lake.id, object_id="lobj-a", snapshot_id="snap-a1", columns={}
    )
    obj_b, snap_b = await _seed_parquet_object(
        db_session, lake.id, object_id="lobj-b", snapshot_id="snap-b1", columns={}
    )

    dfs = {
        "snap-a1": pd.DataFrame([{"id": 1, "name": "张三"}]),
        "snap-b1": pd.DataFrame([{"id": 2, "name": "李四"}]),
    }

    async def fake_read_df(snapshot):
        return dfs[snapshot.id]

    monkeypatch.setattr(data_lake_service, "_read_parquet_df", fake_read_df)
    monkeypatch.setattr(
        data_lake_service, "_put_object_to_lake_minio", AsyncMock(return_value=None)
    )

    result = await merge_lake_objects(
        db_session,
        lake_id=lake.id,
        mode="union",
        inputs=[{"object_id": "lobj-a"}, {"object_id": "lobj-b"}],
        name="合并订单表",
    )

    assert result.version_no == 1
    assert result.storage_format == "parquet"
    assert result.rows == 2
    assert result.merge_inputs == [
        {"object_id": "lobj-a", "snapshot_id": "snap-a1", "version_no": 1},
        {"object_id": "lobj-b", "snapshot_id": "snap-b1", "version_no": 1},
    ]

    merged_obj = await db_session.get(DataLakeObject, result.object_id)
    assert merged_obj.origin == "merged"
    assert merged_obj.display_name == "合并订单表"
    assert merged_obj.merge_config["mode"] == "union"
    assert merged_obj.merge_config["inputs"] == [
        {"object_id": "lobj-a"},
        {"object_id": "lobj-b"},
    ]


@pytest.mark.asyncio
async def test_merge_join_on_keys(db_session, monkeypatch):
    """join 合并:按 join_keys 关联,how=outer 保守保数据。"""
    import pandas as pd

    lake = await create_data_lake(db_session, name="合并 join 测试湖")
    await _seed_parquet_object(
        db_session, lake.id, object_id="lobj-c", snapshot_id="snap-c1", columns={}
    )
    await _seed_parquet_object(
        db_session, lake.id, object_id="lobj-d", snapshot_id="snap-d1", columns={}
    )

    dfs = {
        "snap-c1": pd.DataFrame([{"id": 1, "name": "张三"}]),
        "snap-d1": pd.DataFrame([{"id": 1, "amount": 100}]),
    }

    async def fake_read_df(snapshot):
        return dfs[snapshot.id]

    monkeypatch.setattr(data_lake_service, "_read_parquet_df", fake_read_df)
    monkeypatch.setattr(
        data_lake_service, "_put_object_to_lake_minio", AsyncMock(return_value=None)
    )

    result = await merge_lake_objects(
        db_session,
        lake_id=lake.id,
        mode="join",
        inputs=[{"object_id": "lobj-c"}, {"object_id": "lobj-d"}],
        join_keys=["id"],
        name="合并订单明细",
    )

    assert result.rows == 1  # id=1 两边都有,outer join 后仍是一行


@pytest.mark.asyncio
async def test_merge_join_missing_key_column_rejected(db_session, monkeypatch):
    """join 模式下若某输入缺 join_keys 指定的列 → 拒绝。"""
    import pandas as pd

    lake = await create_data_lake(db_session, name="合并缺列测试湖")
    await _seed_parquet_object(
        db_session, lake.id, object_id="lobj-e", snapshot_id="snap-e1", columns={}
    )
    await _seed_parquet_object(
        db_session, lake.id, object_id="lobj-f", snapshot_id="snap-f1", columns={}
    )

    dfs = {
        "snap-e1": pd.DataFrame([{"id": 1, "name": "张三"}]),
        "snap-f1": pd.DataFrame([{"amount": 100}]),  # 无 id 列
    }

    async def fake_read_df(snapshot):
        return dfs[snapshot.id]

    monkeypatch.setattr(data_lake_service, "_read_parquet_df", fake_read_df)

    with pytest.raises(ExternalStoreError, match="缺少 join_keys 列"):
        await merge_lake_objects(
            db_session,
            lake_id=lake.id,
            mode="join",
            inputs=[{"object_id": "lobj-e"}, {"object_id": "lobj-f"}],
            join_keys=["id"],
            name="不应该被创建",
        )


@pytest.mark.asyncio
async def test_merge_rejects_fewer_than_two_inputs(db_session):
    """输入少于 2 个 → 拒绝。"""
    lake = await create_data_lake(db_session, name="合并单输入测试湖")
    await _seed_parquet_object(
        db_session, lake.id, object_id="lobj-g", snapshot_id="snap-g1", columns={}
    )

    with pytest.raises(ExternalStoreError, match="至少需要选择 2 个文件"):
        await merge_lake_objects(
            db_session,
            lake_id=lake.id,
            mode="union",
            inputs=[{"object_id": "lobj-g"}],
            name="不应该被创建",
        )


@pytest.mark.asyncio
async def test_merge_rejects_append_to_non_merged_object(db_session, monkeypatch):
    """target_object_id 指向的对象 origin != merged → 拒绝(不能把普通采集文件
    当合并文件追加)。校验发生在读回 parquet(第 3 步)之后,故需 monkeypatch
    掉 _read_parquet_df,否则会先在 MinIO 读取阶段报错(服务层既有实现的
    检查顺序,非本次测试范围)。"""
    import pandas as pd

    lake = await create_data_lake(db_session, name="追加非合并对象测试湖")
    await _seed_parquet_object(
        db_session, lake.id, object_id="lobj-h", snapshot_id="snap-h1", columns={}
    )
    await _seed_parquet_object(
        db_session, lake.id, object_id="lobj-i", snapshot_id="snap-i1", columns={}
    )
    # lobj-h 本身是 ingested,不是 merged

    async def fake_read_df(snapshot):
        return pd.DataFrame([{"id": 1}])

    monkeypatch.setattr(data_lake_service, "_read_parquet_df", fake_read_df)

    with pytest.raises(ExternalStoreError, match="不是合并文件"):
        await merge_lake_objects(
            db_session,
            lake_id=lake.id,
            mode="union",
            inputs=[{"object_id": "lobj-h"}, {"object_id": "lobj-i"}],
            target_object_id="lobj-h",
        )
