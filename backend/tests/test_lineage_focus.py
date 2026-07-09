"""血缘焦点探索(OpenMetadata 式,血缘追溯重构)专项用例。

覆盖:
- `GET /lineage/focus`:up/down 深度分别生效(非对称深度互不干扰)、焦点节点
  `isFocus=True`、边界节点 `moreUp`/`moreDown` 计数(含 K>1 场景)、内部节点两者
  均为 0、`members=true` 展开成员节点。
- `GET /lineage/neighbors`:单方向单跳展开,`moreUp`/`moreDown` 计数与
  `/lineage/focus` 一致。
- 6 类 kind(source/lake_object/lake_snapshot/dataset_version/job/member)各自
  解析为焦点均正确;缺参 400、未知实体 404。
- kind=job 在 up=0&down=0 时仍可见(BFS 深度 0 摸不到任务节点本身,
  `_resolve_focus_target` 手工补的 `extra_nodes` 兜底)。

不复述 `tests/test_lineage_panorama.py` 已锁的 `GET /lineage?kind=...`(非焦点、
单方向)既有行为。
"""

from __future__ import annotations

import pytest

from app.models.data_lake import DataLake, DataLakeObject, DataLakeSnapshot
from app.models.dataset import Dataset
from app.models.dataset_version import DatasetVersion
from app.models.dataset_version_table import DatasetVersionTable
from app.models.datasource import DataSource
from app.models.ingest_task import IngestTask
from app.models.job import Job
from app.models.job_input import JobInput

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# 主链路种子:m2 --jm0--> m1 --jm1--> r0(FOCUS) --j1--> r1 --j2--> r2
#           --{j3a,j3b,j3c}--> r3a/b/c
# 上游两跳(m1/m2)、下游两跳(r1/r2)+ r2 有 3 个独立下游消费任务(K=3 边界计数)。
# ---------------------------------------------------------------------------


async def _seed_focus_chain(session_factory) -> None:
    async with session_factory() as session:
        session.add(Dataset(id="dset-fx001", name="焦点测试集"))
        session.add_all(
            [
                DatasetVersion(
                    id="dsv-fx-m2",
                    dataset_id="dset-fx001",
                    version_no=1,
                    storage_uri="s3://uploads/dset-fx001/v1/data.jsonl",
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                Job(
                    id="job-fx-mm0",
                    name="上上游任务",
                    type="process",
                    state="success",
                    spec={"operators": [{"name": "text_length_filter", "params": {}}]},
                ),
            ]
        )
        await session.flush()
        session.add(
            DatasetVersion(
                id="dsv-fx-m1",
                dataset_id="dset-fx001",
                version_no=2,
                storage_uri="s3://uploads/dset-fx001/v2/data.jsonl",
                produced_by_job_id="job-fx-mm0",
            )
        )
        await session.flush()
        session.add(JobInput(job_id="job-fx-mm0", dataset_version_id="dsv-fx-m2"))

        session.add(
            Job(
                id="job-fx-m0",
                name="上游任务",
                type="process",
                state="success",
                spec={"operators": [{"name": "text_length_filter", "params": {}}]},
            )
        )
        await session.flush()
        session.add(
            DatasetVersion(
                id="dsv-fx-r0",
                dataset_id="dset-fx001",
                version_no=3,
                storage_uri="s3://uploads/dset-fx001/v3/data.jsonl",
                produced_by_job_id="job-fx-m0",
            )
        )
        await session.flush()
        session.add(JobInput(job_id="job-fx-m0", dataset_version_id="dsv-fx-m1"))

        session.add(
            Job(
                id="job-fx-1",
                name="下游任务1",
                type="process",
                state="success",
                spec={"operators": [{"name": "text_length_filter", "params": {}}]},
            )
        )
        await session.flush()
        session.add(
            DatasetVersion(
                id="dsv-fx-r1",
                dataset_id="dset-fx001",
                version_no=4,
                storage_uri="s3://uploads/dset-fx001/v4/data.jsonl",
                produced_by_job_id="job-fx-1",
            )
        )
        await session.flush()
        session.add(JobInput(job_id="job-fx-1", dataset_version_id="dsv-fx-r0"))

        session.add(
            Job(
                id="job-fx-2",
                name="下游任务2",
                type="process",
                state="success",
                spec={"operators": [{"name": "text_length_filter", "params": {}}]},
            )
        )
        await session.flush()
        session.add(
            DatasetVersion(
                id="dsv-fx-r2",
                dataset_id="dset-fx001",
                version_no=5,
                storage_uri="s3://uploads/dset-fx001/v5/data.jsonl",
                produced_by_job_id="job-fx-2",
            )
        )
        await session.flush()
        session.add(JobInput(job_id="job-fx-2", dataset_version_id="dsv-fx-r1"))

        # r2 的 3 个独立下游消费任务(K=3),均在 down=2 的边界之外——验证
        # moreDown 是真实计数而非恒为 1。
        for i, suffix in enumerate(("a", "b", "c")):
            jid = f"job-fx-3{suffix}"
            vid = f"dsv-fx-r3{suffix}"
            session.add(
                Job(
                    id=jid,
                    name=f"末梢消费任务{suffix}",
                    type="process",
                    state="success",
                    spec={"operators": []},
                )
            )
            await session.flush()
            session.add(
                DatasetVersion(
                    id=vid,
                    dataset_id="dset-fx001",
                    version_no=6 + i,
                    storage_uri=f"s3://uploads/dset-fx001/{vid}/data.jsonl",
                    produced_by_job_id=jid,
                )
            )
            await session.flush()
            session.add(JobInput(job_id=jid, dataset_version_id="dsv-fx-r2"))
        await session.commit()


async def test_focus_up_down_depth_apply_independently_with_boundary_counts(
    client, session_factory
):
    await _seed_focus_chain(session_factory)

    resp = await client.get(
        "/api/v1/lineage/focus",
        params={
            "kind": "dataset_version",
            "versionId": "dsv-fx-r0",
            "up": 1,
            "down": 2,
        },
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    nodes = {n["id"]: n for n in data["nodes"]}

    # up=1:只到 m1,摸不到再上一跳的 m2/job-fx-mm0
    assert "dsv-fx-m1" in nodes
    assert "job-fx-m0" in nodes
    assert "dsv-fx-m2" not in nodes
    assert "job-fx-mm0" not in nodes

    # down=2:到 r1(1跳)、r2(2跳),摸不到 r2 之后的 3 个末梢任务/版本
    assert "dsv-fx-r1" in nodes
    assert "job-fx-1" in nodes
    assert "dsv-fx-r2" in nodes
    assert "job-fx-2" in nodes
    for suffix in ("a", "b", "c"):
        assert f"job-fx-3{suffix}" not in nodes
        assert f"dsv-fx-r3{suffix}" not in nodes

    # 焦点节点标记
    assert nodes["dsv-fx-r0"]["isFocus"] is True
    assert nodes["dsv-fx-m1"]["isFocus"] is False

    # 上游边界节点 m1:自身产出任务(job-fx-mm0)未入图 → moreUp=1;它没有下游
    # 未含邻居(其消费者 job-fx-m0 已入图)→ moreDown=0。
    assert nodes["dsv-fx-m1"]["moreUp"] == 1
    assert nodes["dsv-fx-m1"]["moreDown"] == 0

    # 下游边界节点 r2:3 个消费任务均未入图 → moreDown=3(K 场景,非恒为1);
    # 自身产出任务 job-fx-2 已入图 → moreUp=0。
    assert nodes["dsv-fx-r2"]["moreUp"] == 0
    assert nodes["dsv-fx-r2"]["moreDown"] == 3

    # 内部节点(焦点自身 + r1):双向邻居均已入图,moreUp/moreDown 均为 0。
    assert nodes["dsv-fx-r0"]["moreUp"] == 0
    assert nodes["dsv-fx-r0"]["moreDown"] == 0
    assert nodes["dsv-fx-r1"]["moreUp"] == 0
    assert nodes["dsv-fx-r1"]["moreDown"] == 0


async def test_neighbors_single_direction_one_hop_matches_focus_counts(
    client, session_factory
):
    await _seed_focus_chain(session_factory)

    up_resp = await client.get(
        "/api/v1/lineage/neighbors",
        params={"kind": "dataset_version", "versionId": "dsv-fx-r1", "direction": "up"},
    )
    assert up_resp.status_code == 200
    up_nodes = {n["id"]: n for n in up_resp.json()["data"]["nodes"]}
    # 紧邻一层:r1 自身 + 产出它的任务 job-fx-1 + 该任务的输入 r0(1 跳),
    # 摸不到 r0 之后的 m1。
    assert set(up_nodes) == {"dsv-fx-r1", "job-fx-1", "dsv-fx-r0"}
    assert up_nodes["dsv-fx-r0"]["moreUp"] == 1  # job-fx-m0 未入图

    down_resp = await client.get(
        "/api/v1/lineage/neighbors",
        params={
            "kind": "dataset_version",
            "versionId": "dsv-fx-r1",
            "direction": "down",
        },
    )
    assert down_resp.status_code == 200
    down_nodes = {n["id"]: n for n in down_resp.json()["data"]["nodes"]}
    assert set(down_nodes) == {"dsv-fx-r1", "job-fx-2", "dsv-fx-r2"}
    assert down_nodes["dsv-fx-r2"]["moreDown"] == 3  # 三个末梢任务未入图


async def test_focus_members_true_includes_member_nodes_and_contains_edges(
    client, session_factory
):
    async with session_factory() as session:
        session.add_all(
            [
                DataLake(id="lake-fx002", name="成员测试湖"),
                DataLakeObject(
                    id="lobj-fx002",
                    lake_id="lake-fx002",
                    identity_key="db:users",
                    display_name="users",
                    data_category="database",
                ),
                DataLakeSnapshot(
                    id="snap-fx002",
                    lake_id="lake-fx002",
                    source_version="source_v1",
                    storage_uri="s3://lake/users.parquet",
                    storage_format="parquet",
                    data_category="database",
                    upload_channel="database",
                    object_id="lobj-fx002",
                    version_no=1,
                ),
                Dataset(id="dset-fx002", name="成员测试集"),
                DatasetVersion(
                    id="dsv-fx002",
                    dataset_id="dset-fx002",
                    version_no=1,
                    storage_uri="s3://uploads/dset-fx002/v1/data.jsonl",
                ),
                DatasetVersionTable(
                    id="dvt-fx002-users",
                    dataset_version_id="dsv-fx002",
                    table_name="users",
                    storage_uri="s3://uploads/dset-fx002/v1/users.parquet",
                    format="parquet",
                    source_snapshot_id="snap-fx002",
                    source_kind="lake",
                ),
                DatasetVersionTable(
                    id="dvt-fx002-notes",
                    dataset_version_id="dsv-fx002",
                    table_name="notes",
                    storage_uri="s3://uploads/dset-fx002/v1/notes.parquet",
                    format="parquet",
                ),
            ]
        )
        await session.commit()

    resp = await client.get(
        "/api/v1/lineage/focus",
        params={
            "kind": "dataset_version",
            "versionId": "dsv-fx002",
            "members": "true",
        },
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    node_ids = {n["id"] for n in data["nodes"]}
    kinds = {n["id"]: n["kind"] for n in data["nodes"]}

    users_mid = "member:dsv-fx002:users"
    notes_mid = "member:dsv-fx002:notes"
    assert users_mid in node_ids and kinds[users_mid] == "member"
    assert notes_mid in node_ids and kinds[notes_mid] == "member"
    contains_edges = {
        (e["from"], e["to"]) for e in data["edges"] if e["kind"] == "contains"
    }
    assert ("dsv-fx002", users_mid) in contains_edges
    assert ("dsv-fx002", notes_mid) in contains_edges


# ---------------------------------------------------------------------------
# 6 类 kind 焦点解析 + 缺参 400 / 未知 404
# ---------------------------------------------------------------------------


async def _seed_source_chain(session_factory) -> None:
    async with session_factory() as session:
        session.add_all(
            [
                DataSource(
                    id="ds-fx010",
                    name="焦点测试源",
                    type="database",
                    db_kind="mysql",
                    status="connected",
                    config={},
                ),
                DataLake(id="lake-fx010", name="焦点测试湖"),
                DataLakeObject(
                    id="lobj-fx010",
                    lake_id="lake-fx010",
                    identity_key="db:t",
                    display_name="t",
                    data_category="database",
                ),
                DataLakeSnapshot(
                    id="snap-fx010",
                    lake_id="lake-fx010",
                    source_version="source_v1",
                    storage_uri="s3://lake/t.parquet",
                    storage_format="parquet",
                    data_category="database",
                    upload_channel="database",
                    datasource_id="ds-fx010",
                    object_id="lobj-fx010",
                    version_no=1,
                ),
                Dataset(id="dset-fx010", name="焦点测试集"),
                DatasetVersion(
                    id="dsv-fx010",
                    dataset_id="dset-fx010",
                    version_no=1,
                    storage_uri="s3://uploads/dset-fx010/v1/data.jsonl",
                ),
                DatasetVersionTable(
                    id="dvt-fx010",
                    dataset_version_id="dsv-fx010",
                    table_name="t",
                    storage_uri="s3://uploads/dset-fx010/v1/t.jsonl",
                    format="jsonl",
                    source_snapshot_id="snap-fx010",
                ),
                Job(
                    id="job-fx010",
                    name="下游清洗任务",
                    type="process",
                    state="success",
                    spec={"operators": [{"name": "text_length_filter", "params": {}}]},
                ),
            ]
        )
        await session.flush()
        session.add(JobInput(job_id="job-fx010", dataset_version_id="dsv-fx010"))
        session.add(
            DatasetVersion(
                id="dsv-fx010v2",
                dataset_id="dset-fx010",
                version_no=2,
                storage_uri="s3://uploads/dset-fx010/v2/data.jsonl",
                produced_by_job_id="job-fx010",
            )
        )
        await session.commit()


async def test_focus_kind_source_resolves_downstream_and_is_focus(
    client, session_factory
):
    await _seed_source_chain(session_factory)

    resp = await client.get(
        "/api/v1/lineage/focus", params={"kind": "source", "sourceId": "ds-fx010"}
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    nodes = {n["id"]: n for n in data["nodes"]}
    assert nodes["ds-fx010"]["isFocus"] is True
    assert "snap-fx010" in nodes
    assert "dsv-fx010" in nodes
    assert "job-fx010" in nodes
    assert "dsv-fx010v2" in nodes


async def test_focus_kind_lake_marks_all_lake_snapshots_as_focus(
    client, session_factory
):
    """kind=lake:选中数据湖 → 其名下全部快照标 isFocus,向下游追到消费数据集
    (数据湖详情「查看血缘」入口 ?lakeId= 走此)。"""
    await _seed_source_chain(session_factory)

    resp = await client.get(
        "/api/v1/lineage/focus", params={"kind": "lake", "lakeId": "lake-fx010"}
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    nodes = {n["id"]: n for n in data["nodes"]}
    assert nodes["snap-fx010"]["isFocus"] is True
    assert "dsv-fx010" in nodes


async def test_focus_kind_lake_missing_param_400(client):
    resp = await client.get("/api/v1/lineage/focus", params={"kind": "lake"})
    assert resp.status_code == 400


async def test_focus_kind_lake_object_marks_all_snapshots_as_focus(
    client, session_factory
):
    await _seed_source_chain(session_factory)

    resp = await client.get(
        "/api/v1/lineage/focus",
        params={"kind": "lake_object", "objectId": "lobj-fx010"},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    nodes = {n["id"]: n for n in data["nodes"]}
    assert nodes["snap-fx010"]["isFocus"] is True
    assert "dsv-fx010" in nodes


async def test_focus_kind_lake_snapshot_resolves_up_and_down(
    client, session_factory
):
    await _seed_source_chain(session_factory)

    resp = await client.get(
        "/api/v1/lineage/focus",
        params={"kind": "lake_snapshot", "snapshotId": "snap-fx010"},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    nodes = {n["id"]: n for n in data["nodes"]}
    assert nodes["snap-fx010"]["isFocus"] is True
    assert "dsv-fx010" in nodes


async def test_focus_kind_dataset_version_resolves_bidirectional(
    client, session_factory
):
    await _seed_source_chain(session_factory)

    resp = await client.get(
        "/api/v1/lineage/focus",
        params={"kind": "dataset_version", "versionId": "dsv-fx010"},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    nodes = {n["id"]: n for n in data["nodes"]}
    assert nodes["dsv-fx010"]["isFocus"] is True
    assert "job-fx010" in nodes
    assert "dsv-fx010v2" in nodes


async def test_focus_kind_member_resolves_source_snapshot(client, session_factory):
    await _seed_source_chain(session_factory)

    resp = await client.get(
        "/api/v1/lineage/focus",
        params={"kind": "member", "versionId": "dsv-fx010", "tableName": "t"},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    mid = "member:dsv-fx010:t"
    nodes = {n["id"]: n for n in data["nodes"]}
    assert nodes[mid]["isFocus"] is True
    assert "snap-fx010" in nodes
    # member 无下游概念,moreDown 恒为 0(不会误导前端渲染下游 +N 按钮)
    assert nodes[mid]["moreDown"] == 0


async def test_focus_kind_job_visible_even_at_zero_depth(client, session_factory):
    """kind=job 且 up=0&down=0:BFS 深度 0 本摸不到任务节点自身(它经其产出版本
    的深度 0 才发现),`_resolve_focus_target` 的 extra_nodes 兜底必须仍让它出现。
    """
    await _seed_source_chain(session_factory)

    resp = await client.get(
        "/api/v1/lineage/focus",
        params={"kind": "job", "jobId": "job-fx010", "up": 0, "down": 0},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    nodes = {n["id"]: n for n in data["nodes"]}
    assert "job-fx010" in nodes
    assert nodes["job-fx010"]["isFocus"] is True
    assert nodes["job-fx010"]["kind"] == "job"
    # 产出版本仍在(它本身就是 up/down anchor),但更上游的输入版本(dsv-fx010)
    # 在 up=0 时摸不到——job 的兜底不应连带把整条链都拉进来。
    assert "dsv-fx010v2" in nodes


async def test_focus_missing_param_400(client):
    resp = await client.get(
        "/api/v1/lineage/focus", params={"kind": "dataset_version"}
    )
    assert resp.status_code == 400


async def test_focus_unknown_entity_404(client):
    resp = await client.get(
        "/api/v1/lineage/focus",
        params={"kind": "dataset_version", "versionId": "dsv-nope"},
    )
    assert resp.status_code == 404


async def _seed_ingest_datasource(session_factory) -> None:
    """数据源 D 下两条采集链,验证 moreUp/moreDown 邻居计数只认"快照自身
    datasource_id"(与 expand_snapshot 画 ingest 边的判据一致):
    - Ja→Sa:Sa.datasource_id=D → 图画 D→Ja→Sa,Ja 是 D 的真实下游邻居。
    - Jb→Sb:Sb.datasource_id 为空 → 图从不画 D→Jb,Jb 不是 D 的邻居;此前按
      ingest_task 回指的 ingest_jobs_by_datasource 会把 Jb 误计入 → moreDown 虚高、
      "+N"点开无物。两个 job 的 ingest_task 都回指 D(重现旧 join 的触发条件)。
    """
    async with session_factory() as session:
        session.add_all(
            [
                DataSource(
                    id="ds-ig",
                    name="采集源",
                    type="database",
                    db_kind="mysql",
                    status="connected",
                    config={},
                ),
                IngestTask(
                    id="it-ig",
                    name="采集任务",
                    datasource_id="ds-ig",
                    datasource_name="采集源",
                    schedule={},
                    status="success",
                    logs=[],
                ),
                DataLake(id="lake-ig", name="采集湖"),
                DataLakeObject(
                    id="lobj-ig",
                    lake_id="lake-ig",
                    identity_key="db:t",
                    display_name="t",
                    data_category="database",
                ),
                Job(
                    id="job-ig-a",
                    name="采集任务A",
                    type="ingest",
                    state="success",
                    ingest_task_id="it-ig",
                    spec={},
                ),
                Job(
                    id="job-ig-b",
                    name="采集任务B",
                    type="ingest",
                    state="success",
                    ingest_task_id="it-ig",
                    spec={},
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                # Sa 带 datasource_id → 图连 D→Ja→Sa
                DataLakeSnapshot(
                    id="snap-ig-a",
                    lake_id="lake-ig",
                    source_version="v1",
                    storage_uri="s3://lake/a.parquet",
                    storage_format="parquet",
                    data_category="database",
                    upload_channel="database",
                    datasource_id="ds-ig",
                    object_id="lobj-ig",
                    job_id="job-ig-a",
                    version_no=1,
                ),
                # Sb 无 datasource_id → 图不连 D→Jb(即便 Jb 的 ingest_task 回指 D)
                DataLakeSnapshot(
                    id="snap-ig-b",
                    lake_id="lake-ig",
                    source_version="v2",
                    storage_uri="s3://lake/b.parquet",
                    storage_format="parquet",
                    data_category="database",
                    upload_channel="database",
                    object_id="lobj-ig",
                    job_id="job-ig-b",
                    version_no=2,
                ),
            ]
        )
        await session.commit()


async def test_datasource_moredown_excludes_unconnected_ingest_jobs(
    client, session_factory
):
    """数据源下游邻居计数只认"快照带 datasource_id"派生的边,不把 ingest_task
    回指但快照无 datasource_id、图中不相连的采集任务算进 moreDown。"""
    await _seed_ingest_datasource(session_factory)

    resp = await client.get(
        "/api/v1/lineage/focus", params={"kind": "source", "sourceId": "ds-ig"}
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    nodes = {n["id"]: n for n in data["nodes"]}

    # 真实链路 D→Ja→Sa 入图;D→Ja 有 ingest 边
    assert "job-ig-a" in nodes
    assert "snap-ig-a" in nodes
    ingest_edges = {
        (e["from"], e["to"]) for e in data["edges"] if e["kind"] == "ingest"
    }
    assert ("ds-ig", "job-ig-a") in ingest_edges

    # 断链的采集任务 B / 其快照都不出现,且不计入数据源 moreDown(修复前会 =1)
    assert "job-ig-b" not in nodes
    assert "snap-ig-b" not in nodes
    assert nodes["ds-ig"]["moreDown"] == 0


async def test_job_moreup_excludes_datasource_when_snapshot_unlinked(
    client, session_factory
):
    """采集任务的上游数据源邻居同样由"其产出快照带 datasource_id"派生:Jb 的快照
    无 datasource_id,图中无 D→Jb 边,故 Jb.moreUp 不应把 D 算作未展开上游。"""
    await _seed_ingest_datasource(session_factory)

    resp = await client.get(
        "/api/v1/lineage/focus",
        params={"kind": "job", "jobId": "job-ig-b", "up": 0, "down": 0},
    )
    assert resp.status_code == 200
    nodes = {n["id"]: n for n in resp.json()["data"]["nodes"]}
    assert nodes["job-ig-b"]["isFocus"] is True
    # 修复前:job.ingest_task_id→D 使 moreUp=1(幽灵上游);修复后应为 0
    assert nodes["job-ig-b"]["moreUp"] == 0


async def test_neighbors_invalid_direction_400(client):
    resp = await client.get(
        "/api/v1/lineage/neighbors",
        params={
            "kind": "dataset_version",
            "versionId": "dsv-nope",
            "direction": "sideways",
        },
    )
    assert resp.status_code == 400
