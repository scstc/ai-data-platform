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
async def test_extract_to_new_dataset_rejects_empty_snapshots(db_session):
    """抽取生成数据集:空快照列表拒绝。"""
    from app.services.external_store import ExternalStoreError
    from app.services.lake_extract import extract_to_new_dataset

    lake = await create_data_lake(db_session, name="抽取空测试湖")
    with pytest.raises(ExternalStoreError, match="至少选择一个快照"):
        await extract_to_new_dataset(
            db_session,
            lake_id=lake.id,
            snapshot_ids=[],
            dataset_name="不应该被创建",
        )


@pytest.mark.asyncio
async def test_extract_to_new_dataset_rejects_cross_lake_snapshots(db_session):
    """抽取生成数据集:快照不属于目标湖 → 拒绝(防止跨湖污染)。"""
    from app.models.data_lake import DataLakeSnapshot
    from app.services.external_store import ExternalStoreError
    from app.services.lake_extract import extract_to_new_dataset

    lake_a = await create_data_lake(db_session, name="源湖 A")
    lake_b = await create_data_lake(db_session, name="源湖 B")
    stray = DataLakeSnapshot(
        id="snap-stray001",
        lake_id=lake_b.id,  # 属于 B
        source_version="source_v20260701_01_x",
        storage_uri="s3://adp-data-lake/x.parquet",
        storage_format="parquet",
        data_category="database",
        upload_channel="database",
    )
    db_session.add(stray)
    await db_session.commit()

    # 请求 A 湖抽取,却传入 B 湖的快照 id → 拒绝
    with pytest.raises(ExternalStoreError, match="不属于"):
        await extract_to_new_dataset(
            db_session,
            lake_id=lake_a.id,
            snapshot_ids=[stray.id],
            dataset_name="不应该被创建",
        )


@pytest.mark.asyncio
async def test_extract_to_existing_dataset_appends_without_recomputing_semantic(
    db_session, monkeypatch
):
    """dataset_id 指向已有数据集时:不建新数据集,复用其 semantic_type,
    落成表成员追加进该数据集(而非按本次快照类别重新推断)。"""
    from types import SimpleNamespace

    from app.models.dataset import Dataset
    from app.services import lake_extract, landing

    lake = await create_data_lake(db_session, name="追加目标湖")
    snap = DataLakeSnapshot(
        id="snap-append01",
        lake_id=lake.id,
        source_version="source_v20260701_01_csv",
        storage_uri="s3://adp-data-lake/data-lake/x/source_v20260701_01_csv/新增.csv",
        storage_format="csv",
        data_category="database",
        upload_channel="local",
        source_metadata={"original_filename": "新增.csv"},
    )
    db_session.add(snap)
    existing = Dataset(
        id="dset-existing01",
        name="既有数据集",
        semantic_type="multimodal",
    )
    db_session.add(existing)
    await db_session.commit()

    created_calls: list[dict] = []
    captured: list[tuple[str, str | None, str | None]] = []

    async def fake_create_dataset(session, **kwargs):
        created_calls.append(kwargs)
        return SimpleNamespace(id="dset-should-not-exist", name=kwargs["name"])

    async def fake_extract(
        session, lake_id, source_version, *, inject_lineage=True, doc_options=None
    ):
        return [{"v": source_version}]

    async def fake_add_table_member(
        session, dataset_id, records, *, table_name, semantic_type=None, **kw
    ):
        captured.append((dataset_id, semantic_type, kw.get("source_snapshot_id")))
        return (None, None)

    monkeypatch.setattr(landing, "create_dataset", fake_create_dataset)
    monkeypatch.setattr(lake_extract, "extract_from_lake_snapshot", fake_extract)
    monkeypatch.setattr(landing, "add_table_member", fake_add_table_member)

    dataset = await lake_extract.extract_to_new_dataset(
        db_session,
        lake_id=lake.id,
        snapshot_ids=[snap.id],
        dataset_id=existing.id,
    )

    assert dataset.id == existing.id
    assert created_calls == []  # 未新建数据集
    # 复用既有 semantic_type;成员行透传湖快照 id(湖→仓结构化血缘)
    assert captured == [(existing.id, "multimodal", snap.id)]


@pytest.mark.asyncio
async def test_extract_to_dataset_rejects_missing_dataset_id(db_session):
    """dataset_id 指向不存在的数据集 → 拒绝。"""
    from app.services.external_store import ExternalStoreError
    from app.services.lake_extract import extract_to_new_dataset

    lake = await create_data_lake(db_session, name="追加目标缺失湖")
    snap = DataLakeSnapshot(
        id="snap-append02",
        lake_id=lake.id,
        source_version="source_v20260701_02_csv",
        storage_uri="s3://adp-data-lake/data-lake/x/source_v20260701_02_csv/data.csv",
        storage_format="csv",
        data_category="database",
        upload_channel="local",
    )
    db_session.add(snap)
    await db_session.commit()

    with pytest.raises(ExternalStoreError, match="目标数据集不存在"):
        await extract_to_new_dataset(
            db_session,
            lake_id=lake.id,
            snapshot_ids=[snap.id],
            dataset_id="dset-does-not-exist",
        )


@pytest.mark.asyncio
async def test_extract_from_lake_snapshot_accepts_doc_formats(db_session, monkeypatch):
    """pdf/doc/docx/ppt/pptx/html 快照走原格式解析分支,不再被判"不支持的存储格式"。

    抽取层此前只放行 csv/xlsx/jsonl 等纯文本格式,文档快照(数据湖按 PRD §2.1
    原格式入湖)会在抽取时直接报错,导致"湖→集"链路对文档类数据断链。
    """
    from app.models.data_lake import DataLakeSnapshot
    from app.services import lake_extract

    lake = await create_data_lake(db_session, name="文档湖")
    snap = DataLakeSnapshot(
        id="snap-pdf001",
        lake_id=lake.id,
        source_version="source_v20260701_01_pdf",
        storage_uri="s3://uploads/doc/1.pdf",
        storage_format="pdf",
        data_category="document",
        upload_channel="local",
    )
    db_session.add(snap)
    await db_session.commit()

    async def fake_read_raw(snapshot, *, doc_options=None):
        # 真实实现会下载字节再走 landing._doc_to_records(markitdown/OCR),
        # 这里只验证网关放行 + 血缘注入,文档解析本身已有独立单测覆盖。
        return [{"text": "解析出的段落"}]

    monkeypatch.setattr(lake_extract, "_read_raw_from_snapshot", fake_read_raw)

    records = await lake_extract.extract_from_lake_snapshot(
        db_session, lake.id, "source_v20260701_01_pdf"
    )
    assert len(records) == 1
    assert records[0]["text"] == "解析出的段落"
    # meta 含 DJ 三元组 + snapshot_id;created_at 由 DB server_default 生成
    assert records[0]["meta"] == {
        "src": "1.pdf",
        "date": snap.created_at.strftime("%Y-%m-%d"),
        "version": "source_v20260701_01_pdf",
        "snapshot_id": "snap-pdf001",
    }


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
async def test_extract_names_members_by_lake_file_name(db_session, monkeypatch):
    """抽取生成数据集:成员名取数据湖原始文件名,而非 source_version;
    同名文件(不同版本)追加 _2/_3 后缀去重,不静默覆盖。"""
    from types import SimpleNamespace

    from app.services import lake_extract, landing

    lake = await create_data_lake(db_session, name="命名湖")
    # 两个快照原始文件名相同(同文件二次入湖 → 不同 source_version),验证去重
    snap_a = DataLakeSnapshot(
        id="snap-name01",
        lake_id=lake.id,
        source_version="source_v20260701_01_csv",
        storage_uri="s3://adp-data-lake/data-lake/x/source_v20260701_01_csv/销售.csv",
        storage_format="csv",
        data_category="database",
        upload_channel="local",
        source_metadata={"original_filename": "销售.csv"},
    )
    snap_b = DataLakeSnapshot(
        id="snap-name02",
        lake_id=lake.id,
        source_version="source_v20260701_02_csv",
        storage_uri="s3://adp-data-lake/data-lake/x/source_v20260701_02_csv/销售.csv",
        storage_format="csv",
        data_category="database",
        upload_channel="local",
        source_metadata={"original_filename": "销售.csv"},
    )
    db_session.add_all([snap_a, snap_b])
    await db_session.commit()

    captured: list[str] = []

    async def fake_create_dataset(session, **kwargs):
        return SimpleNamespace(id="dset-name01", name=kwargs["name"])

    async def fake_extract(
        session, lake_id, source_version, *, inject_lineage=True, doc_options=None
    ):
        return [{"v": source_version}]

    async def fake_add_table_member(session, dataset_id, records, *, table_name, **kw):
        captured.append(table_name)
        return (None, None)

    monkeypatch.setattr(landing, "create_dataset", fake_create_dataset)
    monkeypatch.setattr(lake_extract, "extract_from_lake_snapshot", fake_extract)
    monkeypatch.setattr(landing, "add_table_member", fake_add_table_member)

    await lake_extract.extract_to_new_dataset(
        db_session,
        lake_id=lake.id,
        snapshot_ids=[snap_a.id, snap_b.id],
        dataset_name="按文件名命名的数据集",
    )

    # 用文件名(去扩展名)而非 source_version;第二个同名追加 _2
    assert captured == ["销售", "销售_2"]
    assert not any(name.startswith("source_v") for name in captured)


@pytest.mark.asyncio
async def test_extract_names_db_members_by_db_table(db_session, monkeypatch):
    """DB 类快照(对象键恒为 data.parquet,无区分度)取源表名 db_table 命名成员。"""
    from types import SimpleNamespace

    from app.services import lake_extract, landing

    lake = await create_data_lake(db_session, name="DB 命名湖")
    snap = DataLakeSnapshot(
        id="snap-dbt01",
        lake_id=lake.id,
        source_version="source_v20260701_01_mysql",
        storage_uri="s3://adp-data-lake/data-lake/x/source_v20260701_01_mysql/data.parquet",
        storage_format="parquet",
        data_category="database",
        upload_channel="database",
        source_metadata={"db_schema": "public", "db_table": "orders"},
    )
    db_session.add(snap)
    await db_session.commit()

    captured: list[str] = []

    async def fake_create_dataset(session, **kwargs):
        return SimpleNamespace(id="dset-dbt01", name=kwargs["name"])

    async def fake_extract(
        session, lake_id, source_version, *, inject_lineage=True, doc_options=None
    ):
        return [{"v": source_version}]

    async def fake_add_table_member(session, dataset_id, records, *, table_name, **kw):
        captured.append(table_name)
        return (None, None)

    monkeypatch.setattr(landing, "create_dataset", fake_create_dataset)
    monkeypatch.setattr(lake_extract, "extract_from_lake_snapshot", fake_extract)
    monkeypatch.setattr(landing, "add_table_member", fake_add_table_member)

    await lake_extract.extract_to_new_dataset(
        db_session,
        lake_id=lake.id,
        snapshot_ids=[snap.id],
        dataset_name="按源表名命名的数据集",
    )

    # 取 db_table=orders,而非对象键末段 data 或 source_version
    assert captured == ["orders"]


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
async def test_rename_snapshot_updates_display_name_keeps_lineage(db_session):
    """快照改名:写 original_filename 作展示名,db_table 血缘保留不动。

    改名后抽取取名(_lake_file_name)必须用新名字——这是"用户改的名到处生效"
    的业务约定;而 db_table 是血缘字段,改名不能污染它。
    """
    from fastapi import HTTPException

    from app.api.v1.data_lakes import rename_snapshot
    from app.schemas.data_lake import SnapshotRenameRequest
    from app.services.lake_extract import _lake_file_name

    lake = await create_data_lake(db_session, name="改名测试湖")
    snap = DataLakeSnapshot(
        id="snap-rename1",
        lake_id=lake.id,
        source_version="source_v20260702_01_mysql",
        storage_uri="s3://uploads/test/data.parquet",
        storage_format="parquet",
        data_category="database",
        upload_channel="database",
        source_metadata={"db_schema": "public", "db_table": "orders"},
    )
    db_session.add(snap)
    await db_session.commit()

    # 改名前:DB 快照按源表名展示
    assert _lake_file_name(snap) == "orders"

    result = await rename_snapshot(
        snapshot_id=snap.id,
        body=SnapshotRenameRequest(filename="  2026订单表  "),
        db=db_session,
        user=None,
    )
    # 展示名 = 去空白后的新名字;血缘字段原样保留
    assert result.source_metadata["original_filename"] == "2026订单表"
    assert result.source_metadata["db_table"] == "orders"
    await db_session.refresh(snap)
    assert _lake_file_name(snap) == "2026订单表"

    # 非法入参:空名 / 含路径分隔符 → 400
    for bad in ("   ", "a/b", "a\\b"):
        with pytest.raises(HTTPException) as exc_info:
            await rename_snapshot(
                snapshot_id=snap.id,
                body=SnapshotRenameRequest(filename=bad),
                db=db_session,
                user=None,
            )
        assert exc_info.value.status_code == 400

    # 不存在的快照 → 404
    with pytest.raises(HTTPException) as exc_info:
        await rename_snapshot(
            snapshot_id="snap-nothere",
            body=SnapshotRenameRequest(filename="x"),
            db=db_session,
            user=None,
        )
    assert exc_info.value.status_code == 404


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
    """数据库来源：meta 注入 DJ 三元组,src 取源表名。"""
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
        # meta 含 DJ 三元组 + snapshot_id(全局唯一血缘键):src 取源表名
        # (DB 类无 original_filename);date 为 None(内存构造未落库)
        assert record["meta"] == {
            "src": "transactions",
            "date": None,
            "version": "source_v20260701_01_mysql",
            "snapshot_id": "snap-lineage1",
        }
    # 原字段保留在顶层
    assert enriched[0]["id"] == 1
    assert enriched[1]["name"] == "李四"
    # meta 各记录独立副本,改一条不影响其他
    enriched[0]["meta"]["src"] = "mutated"
    assert enriched[1]["meta"]["src"] == "transactions"


def test_inject_lineage_object_store_fields():
    """对象存储来源：meta 只留三元组,bucket/obj_key 等血缘不进记录。"""
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

    # 无 original_filename/db_table 时 src 回退到 storage_uri 末段;
    # bucket_name/obj_key 等血缘信息由 snapshot_id 反查快照获取,不冗余进记录
    assert enriched[0]["meta"] == {
        "src": "data.parquet",
        "date": None,
        "version": "source_v20260701_01_s3",
        "snapshot_id": "snap-obj-01",
    }


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

    assert enriched[0]["meta"] == {
        "src": "data.parquet",
        "date": None,
        "version": "source_v20260701_01_test",
        "snapshot_id": "snap-nometa",
    }
    assert enriched[0]["col"] == "value"


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
    assert "meta" not in original_records[0]
    assert original_records[0] == {"col": "value"}
