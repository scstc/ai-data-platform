"""数据集过期回收站:打标级联 → 普通接口隐身 → 超管恢复(#19 生命周期收口)。

测试意图(为何重要):
- 过期数据集必须自动进回收站且对所有普通接口隐身(含级联的关联任务),
  否则"有效期"只是个展示字段,过期数据仍被继续消费;
- 级联归因(deleted_by_dataset_id)是恢复正确性的根:恢复 A 不能连带
  恢复"同时还关联着仍在回收站的 B"的共享任务;
- 恢复必须同时续期,否则下一轮扫描立刻再次打标,恢复形同虚设;
- 回收站是超管专属入口,普通用户/匿名不得访问。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.models.dataset import Dataset
from app.models.dataset_version import DatasetVersion
from app.models.ingest_task import IngestTask
from app.models.job import Job
from app.models.job_input import JobInput
from app.services import dataset_lifecycle

pytestmark = pytest.mark.asyncio

_NOW = datetime.now(UTC).replace(tzinfo=None)
_EXPIRED = _NOW - timedelta(days=2)


def _dataset(did: str, valid_until: datetime | None = None) -> Dataset:
    return Dataset(id=did, name=did, owner="admin", valid_until=valid_until)


def _version(vid: str, did: str, job_id: str | None = None) -> DatasetVersion:
    return DatasetVersion(
        id=vid,
        dataset_id=did,
        version_no=1,
        storage_uri=f"uploads/{did}/v1/data.jsonl",
        produced_by_job_id=job_id,
    )


def _job(jid: str) -> Job:
    return Job(id=jid, name=jid, type="process", state="success")


def _ingest_task(tid: str, did: str) -> IngestTask:
    return IngestTask(
        id=tid,
        name=tid,
        datasource_id="ds-x",
        datasource_name="x",
        schedule={"mode": "once"},
        status="success",
        logs=[],
        dataset_id=did,
    )


async def _seed_expired_with_relations(session_factory) -> None:
    """过期数据集 dset-exp:1 个产出 job + 1 个消费 job + 1 个采集任务;
    对照组 dset-ok(未过期)带自己的产出 job。"""
    async with session_factory() as s:
        s.add_all([_dataset("dset-exp", _EXPIRED), _dataset("dset-ok")])
        s.add_all([_job("job-prod"), _job("job-cons"), _job("job-ok")])
        s.add_all(
            [
                _version("dsv-exp", "dset-exp", "job-prod"),
                _version("dsv-ok", "dset-ok", "job-ok"),
            ]
        )
        s.add(JobInput(job_id="job-cons", dataset_version_id="dsv-exp"))
        s.add(_ingest_task("task-exp", "dset-exp"))
        await s.commit()


async def test_mark_expired_cascades_with_attribution(session_factory) -> None:
    """过期扫描:数据集打标(reason=expired),输入/产出涉及的 job 与绑定的
    采集任务级联打标并归因;未过期数据集及其任务不受波及。"""
    await _seed_expired_with_relations(session_factory)
    async with session_factory() as s:
        assert await dataset_lifecycle.mark_expired_datasets(s) == 1
        await s.commit()
    async with session_factory() as s:
        ds = await s.get(Dataset, "dset-exp")
        assert ds.deleted_at is not None
        assert ds.deleted_reason == "expired"
        for jid in ("job-prod", "job-cons"):
            job = await s.get(Job, jid)
            assert job.deleted_at is not None
            assert job.deleted_by_dataset_id == "dset-exp"
        task = await s.get(IngestTask, "task-exp")
        assert task.deleted_by_dataset_id == "dset-exp"
        # 对照组不受波及
        assert (await s.get(Dataset, "dset-ok")).deleted_at is None
        assert (await s.get(Job, "job-ok")).deleted_at is None


async def test_deleted_hidden_from_normal_endpoints(
    client, session_factory
) -> None:
    """过期后:数据集列表(惰性补打)不出现、详情 404;级联任务从
    /jobs 列表与详情消失——其他用户彻底不可见。"""
    await _seed_expired_with_relations(session_factory)
    async with session_factory() as s:
        await dataset_lifecycle.mark_expired_datasets(s)
        await s.commit()

    resp = await client.get("/api/v1/datasets", params={"pageSize": 50})
    ids = [d["id"] for d in resp.json()["data"]]
    assert "dset-exp" not in ids
    assert "dset-ok" in ids

    assert (await client.get("/api/v1/datasets/dset-exp")).status_code == 404

    resp = await client.get("/api/v1/jobs", params={"pageSize": 50})
    job_ids = [j["id"] for j in resp.json()["data"]]
    assert "job-prod" not in job_ids
    assert "job-cons" not in job_ids
    assert "job-ok" in job_ids
    assert (await client.get("/api/v1/jobs/job-prod")).status_code == 404


async def test_lazy_mark_on_list_without_scan(client, session_factory) -> None:
    """不跑扫描,直接访问数据集列表:过期数据集被惰性补打并隐藏
    (过期即不可见不依赖调度器在线)。"""
    async with session_factory() as s:
        s.add(_dataset("dset-lazy", _EXPIRED))
        await s.commit()
    resp = await client.get("/api/v1/datasets", params={"pageSize": 50})
    assert "dset-lazy" not in [d["id"] for d in resp.json()["data"]]
    async with session_factory() as s:
        assert (await s.get(Dataset, "dset-lazy")).deleted_reason == "expired"


async def test_recycle_bin_list_and_restore_renews(
    client, session_factory
) -> None:
    """回收站列表含级联计数;恢复后:清标 + 续期约 1 自然月 +
    级联任务重新可见。"""
    await _seed_expired_with_relations(session_factory)
    async with session_factory() as s:
        await dataset_lifecycle.mark_expired_datasets(s)
        await s.commit()

    resp = await client.get("/api/v1/recycle-bin/datasets")
    assert resp.status_code == 200
    rows = {r["id"]: r for r in resp.json()["data"]}
    assert rows["dset-exp"]["deletedReason"] == "expired"
    assert rows["dset-exp"]["cascadedJobs"] == 2
    assert rows["dset-exp"]["cascadedIngestTasks"] == 1

    resp = await client.post("/api/v1/recycle-bin/datasets/dset-exp/restore")
    assert resp.status_code == 200
    assert resp.json()["data"]["restoredTasks"] == 3

    async with session_factory() as s:
        ds = await s.get(Dataset, "dset-exp")
        assert ds.deleted_at is None
        # 恢复即续期:否则下次扫描立刻再次打标
        delta = ds.valid_until - datetime.now(UTC).replace(tzinfo=None)
        assert 27 <= delta.days <= 31
        assert (await s.get(Job, "job-prod")).deleted_at is None
        assert (await s.get(IngestTask, "task-exp")).deleted_at is None

    resp = await client.get("/api/v1/jobs", params={"pageSize": 50})
    assert "job-prod" in [j["id"] for j in resp.json()["data"]]


async def test_restore_keeps_shared_job_hidden(session_factory) -> None:
    """共享任务归因:job 同时消费 A、B 两个过期数据集,恢复 A 后该 job
    必须仍隐身(重新归因到仍在回收站的 B),不能被连带恢复。"""
    async with session_factory() as s:
        s.add_all([_dataset("dset-a", _EXPIRED), _dataset("dset-b", _EXPIRED)])
        s.add(_job("job-shared"))
        s.add_all([_version("dsv-a", "dset-a"), _version("dsv-b", "dset-b")])
        s.add_all(
            [
                JobInput(job_id="job-shared", dataset_version_id="dsv-a"),
                JobInput(job_id="job-shared", dataset_version_id="dsv-b"),
            ]
        )
        await s.commit()
    async with session_factory() as s:
        assert await dataset_lifecycle.mark_expired_datasets(s) == 2
        await s.commit()
    async with session_factory() as s:
        ds_a = await s.get(Dataset, "dset-a")
        await dataset_lifecycle.restore_dataset(s, ds_a)
        await s.commit()
    async with session_factory() as s:
        job = await s.get(Job, "job-shared")
        assert job.deleted_at is not None
        assert job.deleted_by_dataset_id == "dset-b"


async def test_recycle_bin_admin_only(client, session_factory) -> None:
    """回收站是超管专属:普通用户 403,匿名 401。"""
    from app.services.auth import sign_token

    client.cookies.set("adp_session", sign_token("user"))
    assert (await client.get("/api/v1/recycle-bin/datasets")).status_code == 403
    client.cookies.delete("adp_session")
    assert (await client.get("/api/v1/recycle-bin/datasets")).status_code == 401
