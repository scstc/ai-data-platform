"""数据集 ACL 三级语义执法测试(view=只读数据 / edit=可发起加工 / admin=管授权)。

测试意图(为何重要):
- view 只能查看:能读质量成员/报告类端点,但发起加工任务、改版本元数据一律 403——
  权限含义不再"形同虚设"(此前加工端点仅超管可用,ACL edit 无实际能力);
- edit 能加工:同样的建任务请求过了 ACL 门(栽在后续算子校验 400,而非 403),
  证明校验顺序 ACL 先于业务校验,且不会真的 spawn 任务;
- 无授权用户对读端点(预览/下载/质量)也 403——数据不因"知道版本 id"而泄露;
- 匿名(无 cookie)沿用现状放行,存量集成方式不被破坏。
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio

_JOB_BODY = {
    "name": "acl-probe",
    "type": "clean",
    "datasetVersionId": "dsv-acl1",
    "operators": [{"name": "nonexistent_probe_op"}],
}
_QUALITY_BODY = {
    "name": "acl-probe-q",
    "datasetVersionId": "dsv-acl1",
    "operators": [{"name": "nonexistent_probe_op"}],
}


async def _make_dataset_with_version(session_factory) -> None:
    """u-mgr 私有数据集 + 一个 jsonl 版本(storage 指向不存在路径,够 DB 层校验用)。"""
    from app.models.dataset import Dataset
    from app.models.dataset_version import DatasetVersion

    async with session_factory() as s:
        s.add(
            Dataset(id="dset-acl1", name="acl 集", owner="u-mgr", creator="u-mgr")
        )
        s.add(
            DatasetVersion(
                id="dsv-acl1",
                dataset_id="dset-acl1",
                version_no=1,
                storage_uri="/nonexistent/data.jsonl",
                format="jsonl",
            )
        )
        await s.commit()


async def _grant(session_factory, level: str) -> None:
    from app.models.dataset_acl import DatasetAcl

    async with session_factory() as s:
        s.add(
            DatasetAcl(
                id=f"dac-enf-{level}",
                dataset_id="dset-acl1",
                subject_type="user",
                subject_id="u-staff",
                level=level,
            )
        )
        await s.commit()


async def test_view_can_read_but_not_process(
    client, session_factory, seed_rbac
) -> None:
    """view 级:质量成员可读(200);建加工/质量任务、改版本元数据 → 403。"""
    from app.services.auth import sign_token

    await _make_dataset_with_version(session_factory)
    await _grant(session_factory, "view")
    client.cookies.set("adp_session", sign_token("u-staff"))

    ok = await client.get("/api/v1/dataset-versions/dsv-acl1/quality-members")
    assert ok.status_code == 200, ok.text

    job = await client.post("/api/v1/jobs", json=_JOB_BODY)
    assert job.status_code == 403, job.text

    q = await client.post("/api/v1/quality/jobs", json=_QUALITY_BODY)
    assert q.status_code == 403, q.text

    patch = await client.patch(
        "/api/v1/dataset-versions/dsv-acl1", json={"note": "x"}
    )
    assert patch.status_code == 403, patch.text


async def test_edit_passes_acl_gate(client, session_factory, seed_rbac) -> None:
    """edit 级:同样的建任务请求过 ACL 门,栽在算子校验(400 而非 403);
    版本元数据可改(200)。未知算子保证不真的 spawn 任务。"""
    from app.services.auth import sign_token

    await _make_dataset_with_version(session_factory)
    await _grant(session_factory, "edit")
    client.cookies.set("adp_session", sign_token("u-staff"))

    job = await client.post("/api/v1/jobs", json=_JOB_BODY)
    assert job.status_code == 400, job.text
    assert "未知算子" in job.json()["message"]

    q = await client.post("/api/v1/quality/jobs", json=_QUALITY_BODY)
    assert q.status_code == 400, q.text

    patch = await client.patch(
        "/api/v1/dataset-versions/dsv-acl1", json={"note": "edited"}
    )
    assert patch.status_code == 200, patch.text


async def test_no_grant_cannot_read_data(client, session_factory, seed_rbac) -> None:
    """无授权:预览/下载/质量成员/成员签名 URL 全部 403——数据不因版本 id 泄露。"""
    from app.services.auth import sign_token

    await _make_dataset_with_version(session_factory)
    client.cookies.set("adp_session", sign_token("u-staff"))

    for url in (
        "/api/v1/dataset-versions/dsv-acl1/preview",
        "/api/v1/dataset-versions/dsv-acl1/download",
        "/api/v1/dataset-versions/dsv-acl1/quality-members",
        "/api/v1/dataset-versions/dsv-acl1/member-url?key=dset-acl1/x.png",
        "/api/v1/datasets/dset-acl1/lineage",
    ):
        resp = await client.get(url)
        assert resp.status_code == 403, f"{url} -> {resp.status_code}: {resp.text}"


async def test_job_list_scoped_to_visible_datasets(
    client, session_factory, seed_rbac
) -> None:
    """任务列表行级裁剪:非超管只看到「我建的 + 授权数据集上的」任务。

    为什么:任务名/输入输出概要会泄露他人数据集的存在与元信息;
    列表必须与数据集可见性同一口径,而不是全表返回。
    """
    from app.models.job import Job
    from app.models.job_input import JobInput
    from app.services.auth import sign_token

    await _make_dataset_with_version(session_factory)
    async with session_factory() as s:
        # u-mgr 数据集上的任务(经 job_inputs 关联)
        s.add(
            Job(
                id="job-aclmgr",
                name="mgr 的清洗",
                type="clean",
                state="success",
                progress=100,
                created_by="u-mgr",
            )
        )
        s.add(JobInput(job_id="job-aclmgr", dataset_version_id="dsv-acl1"))
        # u-staff 自己建的孤儿任务(无输入关联)
        s.add(
            Job(
                id="job-aclown",
                name="staff 的任务",
                type="clean",
                state="failed",
                progress=0,
                created_by="u-staff",
            )
        )
        await s.commit()

    # 无授权:只看到自己建的
    client.cookies.set("adp_session", sign_token("u-staff"))
    resp = await client.get("/api/v1/jobs?current=1&pageSize=50")
    assert resp.status_code == 200, resp.text
    ids = {j["id"] for j in resp.json()["data"]}
    assert "job-aclown" in ids
    assert "job-aclmgr" not in ids, "无授权不应看到他人数据集上的任务"

    # 授 view 后:该数据集上的任务进入列表
    await _grant(session_factory, "view")
    resp2 = await client.get("/api/v1/jobs?current=1&pageSize=50")
    ids2 = {j["id"] for j in resp2.json()["data"]}
    assert {"job-aclown", "job-aclmgr"} <= ids2

    # 超管:全量可见(不裁剪)
    client.cookies.set("adp_session", sign_token("u-super"))
    resp3 = await client.get("/api/v1/jobs?current=1&pageSize=50")
    ids3 = {j["id"] for j in resp3.json()["data"]}
    assert {"job-aclown", "job-aclmgr"} <= ids3


async def test_anonymous_passthrough_preserved(
    client, session_factory, seed_rbac
) -> None:
    """匿名(无 cookie):读端点沿用现状放行(can_access 对 user=None 直接 True)。"""
    await _make_dataset_with_version(session_factory)
    client.cookies.delete("adp_session")

    ok = await client.get("/api/v1/dataset-versions/dsv-acl1/quality-members")
    assert ok.status_code == 200, ok.text
