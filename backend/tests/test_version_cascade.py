"""版本/数据集删除级联 + 成员删除共享 URI 防线(库表整改)。

为什么存在这些用例:
- 本仓库无 FK 弱关联,级联全靠应用层;整改前 delete 版本/数据集漏删
  dataset_version_tables,adp_gov 里 78% 成员行是孤儿。删除必须同事务清干净。
- 零拷贝结转(engine.carry_over_members)让多版本成员行共享同一 storage_uri;
  删成员若不查共享引用直接删对象,下游版本成员会指向空对象(数据丢失)。
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from app.models import DatasetVersionTable
from app.models.dataset import Dataset
from app.models.dataset_version import DatasetVersion
from app.models.job import Job
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
async def test_delete_dataset_blocked_by_downstream_reference(client, db_session):
    """P0:硬删数据集前必须查跨数据集下游引用——本数据集的版本若被其它「未删」
    数据集的任务消费为输入,硬删会让那条任务的血缘边(job_inputs)、任务详情
    「输入版本」、GET /lineage 展示悄悄悬空,而下游数据集本身仍在正常使用
    (未软删),这种失联对它是不可见的静默损坏,必须 409 拒绝并点名受影响数据集。
    """
    upstream = await create_dataset(db_session, name="上游被引用集")
    v_up, _ = await add_table_member(
        db_session, upstream.id, [{"text": "a"}], table_name="t1"
    )
    downstream = await create_dataset(db_session, name="下游消费集")
    db_session.add(
        Job(id="job-cross", name="跨集", type="clean", state="success", progress=100)
    )
    db_session.add(JobInput(job_id="job-cross", dataset_version_id=v_up.id))
    db_session.add(
        DatasetVersion(
            id="dsv-cross-out",
            dataset_id=downstream.id,
            version_no=1,
            storage_uri="/data/datasets/cross.jsonl",
            format="jsonl",
            produced_by_job_id="job-cross",
        )
    )
    await db_session.commit()

    resp = await client.delete(f"/api/v1/datasets/{upstream.id}")
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["success"] is False
    affected_ids = {d["id"] for d in body["affectedDatasets"]}
    assert downstream.id in affected_ids

    # 拒绝生效:上游数据集未被误删
    assert await db_session.get(Dataset, upstream.id) is not None


@pytest.mark.asyncio
async def test_delete_dataset_allowed_when_downstream_already_deleted(
    client, db_session
):
    """下游数据集本身已软删(在回收站)不构成阻塞——否则回收站里的数据集会
    永久卡住上游数据集的删除,与「仅未删数据集的引用才算数」的设计对齐。"""
    upstream = await create_dataset(db_session, name="上游集2")
    v_up, _ = await add_table_member(
        db_session, upstream.id, [{"text": "a"}], table_name="t1"
    )
    downstream = await create_dataset(db_session, name="下游已删集")
    downstream.deleted_at = datetime.now(UTC).replace(tzinfo=None)
    db_session.add(
        Job(id="job-cross2", name="跨集2", type="clean", state="success", progress=100)
    )
    db_session.add(JobInput(job_id="job-cross2", dataset_version_id=v_up.id))
    db_session.add(
        DatasetVersion(
            id="dsv-cross-out2",
            dataset_id=downstream.id,
            version_no=1,
            storage_uri="/data/datasets/cross2.jsonl",
            format="jsonl",
            produced_by_job_id="job-cross2",
        )
    )
    await db_session.commit()

    resp = await client.delete(f"/api/v1/datasets/{upstream.id}")
    assert resp.status_code == 200, resp.text


@pytest.mark.asyncio
async def test_delete_dataset_cascades_related_jobs(client, db_session):
    """删数据集必须级联硬删关联任务(消费/产出/spec 反查/采集),不留孤儿。

    为什么:过期回收站会级联打标,手动删除若不删任务,任务列表会留下输入版本
    已不存在的孤儿任务(详情空白、重跑 404)。无关任务绝不能被误删;共享任务在
    其他数据集的产出版本保留、仅置空上游 job 指针(与单删任务同语义)。
    """
    from app.models.dataset_version import DatasetVersion
    from app.models.ingest_task import IngestTask
    from app.models.job import Job

    ds = await create_dataset(db_session, name="任务级联测试")
    dsid = ds.id
    v1, _ = await add_table_member(
        db_session, dsid, [{"text": "a"}], table_name="t1"
    )
    other = await create_dataset(db_session, name="旁观数据集")
    ov, _ = await add_table_member(
        db_session, other.id, [{"text": "b"}], table_name="t1"
    )
    db_session.add_all(
        [
            # 消费者:血缘边指向被删数据集的版本
            Job(id="job-consumer", name="消费任务", type="process"),
            JobInput(job_id="job-consumer", dataset_version_id=v1.id),
            # 失败任务:无血缘边,仅 spec 记录输入版本(spec 反查兜底)
            Job(
                id="job-speconly",
                name="失败任务",
                type="process",
                state="failed",
                spec={"dataset_version_id": v1.id},
            ),
            # 生产者:产出了旁观数据集的一个版本(共享任务跨数据集产出)
            Job(id="job-producer", name="生产任务", type="process"),
            # 无关任务:与被删数据集毫无关联,必须幸存
            Job(
                id="job-unrelated",
                name="无关任务",
                type="process",
                spec={"dataset_version_id": ov.id},
            ),
            # 绑定被删数据集的采集任务
            IngestTask(
                id="task-cascade",
                name="采集任务",
                datasource_id="src-x",
                datasource_name="源",
                schedule={"mode": "once"},
                status="success",
                logs=[],
                dataset_id=dsid,
            ),
        ]
    )
    # job-producer 同时产出:被删数据集的 v1 + 旁观数据集的 ov
    v1.produced_by_job_id = "job-producer"
    ov.produced_by_job_id = "job-producer"
    await db_session.commit()

    resp = await client.delete(f"/api/v1/datasets/{dsid}")
    assert resp.status_code == 200, resp.text

    remaining = set(
        (await db_session.execute(select(Job.id))).scalars().all()
    )
    assert remaining == {"job-unrelated"}
    assert await db_session.get(IngestTask, "task-cascade") is None
    # 共享任务在旁观数据集的产出版本保留,上游指针置空
    await db_session.refresh(ov)
    assert isinstance(ov, DatasetVersion) and ov.produced_by_job_id is None
    # 血缘边不残留
    edges = (await db_session.execute(select(JobInput))).scalars().all()
    assert edges == []


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


@pytest.mark.asyncio
async def test_delete_member_malformed_uri_skips_remove_object(
    client, db_session, monkeypatch, caplog
):
    """storage_uri 不是合法 s3:// URI(脏数据)时,绝不猜测 bucket/key 去删对象——
    此前的降级(默认桶 + 猜测 key)可能误删无关对象;正确做法是跳过该对象删除、
    只删 DB 行,并记错误日志留痕,不静默吞掉。"""
    import logging

    from app.api.v1 import datasets as dmod

    ds = await create_dataset(db_session, name="脏 URI 测试")
    v1, m1 = await add_table_member(
        db_session, ds.id, [{"text": "x"}], table_name="broken"
    )
    vid = v1.id
    # 人为破坏该成员的 storage_uri,模拟脏数据(非法 s3:// URI)
    m1.storage_uri = "not-a-valid-uri"
    await db_session.commit()

    called: list[tuple[str, str]] = []

    async def _record_remove(cfg, bucket, key):
        called.append((bucket, key))

    monkeypatch.setattr(dmod, "platform_config", lambda: object())
    monkeypatch.setattr(dmod, "remove_object", _record_remove)

    with caplog.at_level(logging.ERROR):
        resp = await client.delete(
            f"/api/v1/dataset-versions/{vid}/members",
            params={"keys": ["not-a-valid-uri"]},
        )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["deleted"] == 1
    # 绝不带猜测出的 bucket/key 去删对象
    assert called == []
    # 记了错误日志,不是静默吞掉
    assert any("无法解析" in r.message for r in caplog.records)
    assert await _member_rows(db_session, vid) == []
