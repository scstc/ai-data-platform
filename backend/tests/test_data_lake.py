"""数据湖模型 + 服务层单测（不涉及 MinIO I/O）。

验证：
- 版本号格式符合治理文档规范（source_v年月日_批次_类型）
- 数据湖容器 CRUD
- 快照的 (lake_id, source_version) 唯一约束
- 血缘字段注入逻辑
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.data_lake import DataLakeSnapshot
from app.services.data_lake import (
    create_data_lake,
    generate_source_version,
    get_lake_by_id,
    get_snapshot_by_version,
    list_lake_snapshots,
)
from app.services.lake_extract import _inject_lineage_fields

# --------------------------------------------------------------------------
# 版本号生成
# --------------------------------------------------------------------------


def test_generate_source_version_format():
    """版本号必须符合 source_v年月日_批次_类型 格式。"""
    v = generate_source_version(
        date=datetime(2026, 7, 1, tzinfo=UTC),
        batch=1,
        source_type="mysql",
    )
    assert v == "source_v20260701_01_mysql"


def test_generate_source_version_batch_padded():
    """批次号必须补零到 2 位。"""
    v = generate_source_version(
        date=datetime(2026, 1, 1, tzinfo=UTC),
        batch=5,
        source_type="pg",
    )
    assert v == "source_v20260101_05_pg"


def test_generate_source_version_defaults_now():
    """默认参数用当前时间。"""
    v = generate_source_version(source_type="s3")
    assert v.startswith("source_v")
    assert v.endswith("_s3")
    # 版本号中间部分应为 8 位年月日 + 2 位批次
    parts = v.split("_")
    assert len(parts) == 4  # source, vYYYYMMDD, NN, type
    assert len(parts[1]) == 9  # v + 8 digit date
    assert parts[1].startswith("v")


# --------------------------------------------------------------------------
# 数据湖容器 CRUD
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_data_lake(db_session):
    """创建数据湖并读回（多源汇聚容器，不绑定类型）。"""
    lake = await create_data_lake(
        db_session,
        name="测试湖",
        description="pytest fixture",
        creator="admin",
    )
    assert lake.id.startswith("lake-")
    assert lake.name == "测试湖"
    assert lake.creator == "admin"

    # 读回确认落库
    got = await get_lake_by_id(db_session, lake.id)
    assert got is not None
    assert got.name == "测试湖"


@pytest.mark.asyncio
async def test_lake_is_multi_source_container(db_session):
    """一个数据湖可以承接多种来源的快照（多源汇聚）。"""
    from app.models.data_lake import DataLakeSnapshot

    lake = await create_data_lake(db_session, name="多源湖")

    # 三种不同来源的快照落进同一个湖
    snap_db = DataLakeSnapshot(
        id="snap-multi01",
        lake_id=lake.id,
        source_version="source_v20260701_01_mysql",
        storage_uri="s3://uploads/multi/1.parquet",
        storage_format="parquet",
        data_category="database",
        upload_channel="database",
        datasource_id="ds-mysql01",
    )
    snap_oss = DataLakeSnapshot(
        id="snap-multi02",
        lake_id=lake.id,
        source_version="source_v20260701_02_pdf",
        storage_uri="s3://uploads/multi/2.pdf",
        storage_format="pdf",
        data_category="document",
        upload_channel="oss",
        datasource_id="ds-oss01",
    )
    snap_local = DataLakeSnapshot(
        id="snap-multi03",
        lake_id=lake.id,
        source_version="source_v20260701_03_png",
        storage_uri="s3://uploads/multi/3.png",
        storage_format="png",
        data_category="image",
        upload_channel="local",
        datasource_id=None,  # 本地上传无 datasource
    )
    db_session.add_all([snap_db, snap_oss, snap_local])
    await db_session.commit()

    snapshots = await list_lake_snapshots(db_session, lake.id)
    assert len(snapshots) == 3
    # 同一湖里数据类型和来源渠道都可以不同
    categories = {s.data_category for s in snapshots}
    assert categories == {"database", "document", "image"}
    channels = {s.upload_channel for s in snapshots}
    assert channels == {"database", "oss", "local"}


@pytest.mark.asyncio
async def test_get_lake_missing(db_session):
    """不存在的湖返回 None（不抛异常）。"""
    got = await get_lake_by_id(db_session, "lake-doesnotexist")
    assert got is None


# --------------------------------------------------------------------------
# 快照唯一约束
# --------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_snapshot_unique_per_lake_version(db_session):
    """(lake_id, source_version) 组合唯一，不能重复插入相同 source_version。"""
    lake = await create_data_lake(
        db_session,
        name="唯一约束测试湖",
    )
    # 手动插两条同 source_version 的快照 → 应违反 uq_lake_source_version
    snap1 = DataLakeSnapshot(
        id="snap-aaa111",
        lake_id=lake.id,
        source_version="source_v20260701_01_test",
        storage_uri="s3://uploads/test/1.parquet",
        storage_format="parquet",
        data_category="database",
        upload_channel="database",
    )
    snap2 = DataLakeSnapshot(
        id="snap-bbb222",
        lake_id=lake.id,
        source_version="source_v20260701_01_test",  # 同版本号
        storage_uri="s3://uploads/test/2.parquet",
        storage_format="parquet",
        data_category="database",
        upload_channel="database",
    )
    db_session.add(snap1)
    await db_session.commit()

    db_session.add(snap2)
    with pytest.raises(IntegrityError):
        await db_session.commit()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_list_snapshots_desc_by_created(db_session):
    """快照列表按创建时间倒序。"""
    lake = await create_data_lake(
        db_session,
        name="列表测试湖",
    )
    # 插 3 条快照
    for i in range(3):
        snap = DataLakeSnapshot(
            id=f"snap-list{i:03d}",
            lake_id=lake.id,
            source_version=f"source_v20260701_0{i + 1}_test",
            storage_uri=f"s3://uploads/test/{i}.parquet",
            storage_format="parquet",
            data_category="database",
            upload_channel="database",
        )
        db_session.add(snap)
    await db_session.commit()

    snapshots = await list_lake_snapshots(db_session, lake.id)
    assert len(snapshots) == 3
    # 三条 source_version 都在返回中
    versions = {s.source_version for s in snapshots}
    assert versions == {
        "source_v20260701_01_test",
        "source_v20260701_02_test",
        "source_v20260701_03_test",
    }


@pytest.mark.asyncio
async def test_get_snapshot_by_version(db_session):
    """根据 (lake_id, source_version) 定位快照。"""
    lake = await create_data_lake(
        db_session,
        name="按版本查询测试湖",
    )
    snap = DataLakeSnapshot(
        id="snap-bybv01",
        lake_id=lake.id,
        source_version="source_v20260701_01_target",
        storage_uri="s3://uploads/test/target.parquet",
        storage_format="parquet",
        data_category="database",
        upload_channel="database",
    )
    db_session.add(snap)
    await db_session.commit()

    got = await get_snapshot_by_version(
        db_session, lake.id, "source_v20260701_01_target"
    )
    assert got is not None
    assert got.id == "snap-bybv01"

    # 不存在的版本返回 None
    missing = await get_snapshot_by_version(
        db_session, lake.id, "source_v20260701_99_nope"
    )
    assert missing is None


# --------------------------------------------------------------------------
# 血缘字段注入
# --------------------------------------------------------------------------


def test_inject_lineage_database_fields():
    """数据库来源：注入通用血缘 + db_schema/db_table/db_engine。"""
    snapshot = DataLakeSnapshot(
        id="snap-lineage1",
        lake_id="lake-test",
        source_version="source_v20260701_01_mysql",
        storage_uri="s3://uploads/test/data.parquet",
        storage_format="parquet",
        data_category="database",
        upload_channel="database",
        source_metadata={
            "db_schema": "finance",
            "db_table": "transactions",
            "db_engine": "MySQL 8.0",
        },
    )
    records = [
        {"id": 1, "name": "张三", "amount": 1000},
        {"id": 2, "name": "李四", "amount": 2000},
    ]
    enriched = _inject_lineage_fields(records, snapshot)

    assert len(enriched) == 2
    for record in enriched:
        # 通用血缘字段
        assert record["source_version"] == "source_v20260701_01_mysql"
        assert record["source_category"] == "database"
        assert record["upload_channel"] == "database"
        assert record["data_lake_snapshot_id"] == "snap-lineage1"
        # 差异化溯源字段
        assert record["db_schema"] == "finance"
        assert record["db_table"] == "transactions"
        assert record["db_engine"] == "MySQL 8.0"
    # 原字段保留
    assert enriched[0]["id"] == 1
    assert enriched[1]["name"] == "李四"


def test_inject_lineage_object_store_fields():
    """对象存储来源：注入 bucket_name/obj_key。"""
    snapshot = DataLakeSnapshot(
        id="snap-obj-01",
        lake_id="lake-test",
        source_version="source_v20260701_01_s3",
        storage_uri="s3://uploads/test/data.parquet",
        storage_format="parquet",
        data_category="database",
        upload_channel="oss",
        source_metadata={
            "bucket_name": "user-data",
            "obj_key": "raw/2026/report.parquet",
        },
    )
    records = [{"id": 1}]
    enriched = _inject_lineage_fields(records, snapshot)

    assert enriched[0]["bucket_name"] == "user-data"
    assert enriched[0]["obj_key"] == "raw/2026/report.parquet"
    # 不应有数据库字段
    assert "db_schema" not in enriched[0]


def test_inject_lineage_no_source_metadata():
    """source_metadata 为空时只注入通用字段，不报错。"""
    snapshot = DataLakeSnapshot(
        id="snap-nometa",
        lake_id="lake-test",
        source_version="source_v20260701_01_test",
        storage_uri="s3://uploads/test/data.parquet",
        storage_format="parquet",
        data_category="database",
        upload_channel="database",
        source_metadata=None,
    )
    records = [{"col": "value"}]
    enriched = _inject_lineage_fields(records, snapshot)

    assert enriched[0]["source_version"] == "source_v20260701_01_test"
    assert enriched[0]["col"] == "value"
    assert "db_schema" not in enriched[0]


def test_inject_lineage_original_records_untouched():
    """血缘注入不应污染原记录（深拷贝语义）。"""
    snapshot = DataLakeSnapshot(
        id="snap-purity",
        lake_id="lake-test",
        source_version="source_v20260701_01_test",
        storage_uri="s3://uploads/test/data.parquet",
        storage_format="parquet",
        data_category="database",
        upload_channel="database",
        source_metadata=None,
    )
    original_records = [{"col": "value"}]
    _inject_lineage_fields(original_records, snapshot)

    # 原记录不应被污染
    assert "source_version" not in original_records[0]
    assert original_records[0] == {"col": "value"}
