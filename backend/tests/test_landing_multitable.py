import pytest
from sqlalchemy import select

from app.models import DatasetVersion, DatasetVersionTable
from app.services.landing import add_table_member, create_dataset


@pytest.mark.asyncio
async def test_create_empty_then_add_two_members(db_session):
    ds = await create_dataset(db_session, name="多表集", data_type="sql")
    # 空数据集:无任何版本
    vers = (await db_session.execute(
        select(DatasetVersion).where(DatasetVersion.dataset_id == ds.id)
    )).scalars().all()
    assert vers == []

    ver1, m1 = await add_table_member(
        db_session, ds.id, [{"a": 1}], table_name="users"
    )
    ver2, m2 = await add_table_member(
        db_session, ds.id, [{"b": 2}, {"b": 3}], table_name="orders"
    )
    # 两个成员同属一个 draft 版本 v1
    assert ver1.id == ver2.id
    assert ver2.version_no == 1
    assert ver2.publish_status == "draft"
    members = (await db_session.execute(
        select(DatasetVersionTable).where(
            DatasetVersionTable.dataset_version_id == ver2.id
        )
    )).scalars().all()
    assert {m.table_name for m in members} == {"users", "orders"}
    # rollup:总行数 = 1 + 2
    await db_session.refresh(ver2)
    assert ver2.rows == 3


@pytest.mark.asyncio
async def test_overwrite_same_table_in_draft(db_session):
    ds = await create_dataset(db_session, name="覆盖集")
    await add_table_member(db_session, ds.id, [{"a": 1}], table_name="t")
    ver, m = await add_table_member(
        db_session, ds.id, [{"a": 9}, {"a": 8}], table_name="t"
    )
    members = (await db_session.execute(
        select(DatasetVersionTable).where(
            DatasetVersionTable.dataset_version_id == ver.id,
            DatasetVersionTable.table_name == "t",
        )
    )).scalars().all()
    assert len(members) == 1          # 覆盖,不新增
    assert members[0].rows == 2       # 取最新一次


@pytest.mark.asyncio
async def test_member_source_snapshot_lineage(db_session):
    """湖→仓血缘列:落成员时写入、同名覆盖跟随新来源、发布克隆随行。

    这列是"数据集成员 ← 湖快照"唯一的 DB 级血缘(记录级 meta 在 MinIO 数据
    文件里,SQL 查不到),丢了就只剩自由文本 note——所以三条路径都要钉住。
    """
    ds = await create_dataset(db_session, name="血缘集")
    # 1. 湖抽取来源:写入快照 id
    v1, m1 = await add_table_member(
        db_session, ds.id, [{"a": 1}], table_name="t",
        source_snapshot_id="snap-lineage1",
    )
    assert m1.source_snapshot_id == "snap-lineage1"
    # 2. 同名覆盖且新来源非湖:血缘置空,不残留旧快照引用
    _, m2 = await add_table_member(db_session, ds.id, [{"a": 2}], table_name="t")
    assert m2.id == m1.id
    assert m2.source_snapshot_id is None
    # 3. 再覆盖回湖来源 → 发布 → 新 draft 克隆成员:血缘随行
    await add_table_member(
        db_session, ds.id, [{"a": 3}], table_name="t",
        source_snapshot_id="snap-lineage2",
    )
    v1.publish_status = "published"
    await db_session.commit()
    v2, _ = await add_table_member(db_session, ds.id, [{"b": 1}], table_name="t2")
    cloned = (await db_session.execute(
        select(DatasetVersionTable).where(
            DatasetVersionTable.dataset_version_id == v2.id,
            DatasetVersionTable.table_name == "t",
        )
    )).scalar_one()
    assert cloned.source_snapshot_id == "snap-lineage2"


@pytest.mark.asyncio
async def test_published_version_triggers_new_draft(db_session):
    ds = await create_dataset(db_session, name="发布集")
    v1, _ = await add_table_member(db_session, ds.id, [{"a": 1}], table_name="t")
    v1.publish_status = "published"
    await db_session.commit()
    v2, _ = await add_table_member(db_session, ds.id, [{"a": 2}], table_name="t2")
    assert v2.version_no == 2
    assert v2.publish_status == "draft"
    # 克隆上一版成员:v2 应含 t(克隆)+ t2(新增)
    members = (await db_session.execute(
        select(DatasetVersionTable.table_name).where(
            DatasetVersionTable.dataset_version_id == v2.id
        )
    )).scalars().all()
    assert set(members) == {"t", "t2"}
