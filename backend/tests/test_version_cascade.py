"""版本/数据集删除级联 + 成员删除共享 URI 防线(库表整改)。

为什么存在这些用例:
- 本仓库无 FK 弱关联,级联全靠应用层;整改前 delete 版本/数据集漏删
  dataset_version_tables,adp_gov 里 78% 成员行是孤儿。删除必须同事务清干净。
- 零拷贝结转(engine.carry_over_members)让多版本成员行共享同一 storage_uri;
  删成员若不查共享引用直接删对象,下游版本成员会指向空对象(数据丢失)。
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from app.models import DatasetVersionTable
from app.models.job_input import JobInput
from app.services.landing import add_table_member, create_dataset


async def _member_rows(session, version_id: str) -> list[DatasetVersionTable]:
    return list(
        (
            await session.execute(
                select(DatasetVersionTable).where(
                    DatasetVersionTable.dataset_version_id == version_id
                )
            )
        )
        .scalars()
        .all()
    )


@pytest.mark.asyncio
async def test_delete_version_cascades_members_and_job_inputs(client, db_session):
    """删 draft 版本必须级联删表成员行与血缘边,不留孤儿。"""
    ds = await create_dataset(db_session, name="级联删除测试")
    v1, _ = await add_table_member(
        db_session, ds.id, [{"text": "a"}], table_name="t1"
    )
    vid = v1.id
    await add_table_member(db_session, ds.id, [{"text": "b"}], table_name="t2")
    db_session.add(JobInput(job_id="job-cascade", dataset_version_id=vid))
    await db_session.commit()
    assert len(await _member_rows(db_session, vid)) == 2

    resp = await client.delete(f"/api/v1/dataset-versions/{vid}")
    assert resp.status_code == 200, resp.text

    assert await _member_rows(db_session, vid) == []
    edges = (
        await db_session.execute(
            select(JobInput).where(JobInput.dataset_version_id == vid)
        )
    ).scalars().all()
    assert edges == []


@pytest.mark.asyncio
async def test_delete_dataset_cascades_member_rows(client, db_session):
    """删数据集(_purge_dataset)同样要清掉全部版本的表成员行。"""
    ds = await create_dataset(db_session, name="数据集级联测试")
    dsid = ds.id
    v1, _ = await add_table_member(
        db_session, dsid, [{"text": "a"}], table_name="t1"
    )
    vid = v1.id

    resp = await client.delete(f"/api/v1/datasets/{dsid}")
    assert resp.status_code == 200, resp.text

    assert await _member_rows(db_session, vid) == []


@pytest.mark.asyncio
async def test_delete_member_keeps_object_shared_by_carry_over(
    client, db_session, monkeypatch
):
    """成员对象被其他版本零拷贝结转引用时,删成员只删行不删对象;
    无共享引用时对象一并删除。"""
    from app.api.v1 import datasets as dmod

    ds = await create_dataset(db_session, name="共享URI防线测试")
    v1, m1 = await add_table_member(
        db_session, ds.id, [{"text": "x"}], table_name="shared"
    )
    vid, uri1, fmt1 = v1.id, m1.storage_uri, m1.format
    _, m2 = await add_table_member(
        db_session, ds.id, [{"text": "y"}], table_name="solo"
    )
    uri2 = m2.storage_uri
    # 模拟结转:后续版本的成员行引用同一 storage_uri(见 carry_over_members)
    db_session.add(
        DatasetVersionTable(
            id="dvt-carry1",
            dataset_version_id="dsv-next",
            table_name="shared",
            storage_uri=uri1,
            format=fmt1,
        )
    )
    await db_session.commit()

    removed: list[str] = []

    async def _record_remove(cfg, bucket, key):
        removed.append(key)

    monkeypatch.setattr(dmod, "platform_config", lambda: object())
    monkeypatch.setattr(dmod, "remove_object", _record_remove)

    def _key_of(uri: str) -> str:
        try:
            _, key = dmod.parse_s3_uri(uri)
            return key
        except Exception:
            return str(uri)

    # 共享成员:HTTP 成功、行已删,但对象保留(remove_object 未被调用)
    resp = await client.delete(
        f"/api/v1/dataset-versions/{vid}/members",
        params={"keys": [_key_of(uri1)]},
    )
    assert resp.status_code == 200, resp.text
    assert removed == []
    names = {r.table_name for r in await _member_rows(db_session, vid)}
    assert names == {"solo"}

    # 独享成员:行删,对象也删
    resp = await client.delete(
        f"/api/v1/dataset-versions/{vid}/members",
        params={"keys": [_key_of(uri2)]},
    )
    assert resp.status_code == 200, resp.text
    assert len(removed) == 1
    assert await _member_rows(db_session, vid) == []
