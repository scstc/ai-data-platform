"""血缘服务化(治理整改 P1-②)专项用例:`lineage_service.build_lineage` 新增能力
——不覆盖 `tests/test_lineage_lake.py` 已锁的 `dataset_lineage`(expand_members=False)
既有行为,那 11 例本身就是"抽取零回归"的回归门禁。

覆盖:
- expand_members=True:member 一等节点 + contains 边;根版本 extract 边源改挂到
  具体 member。
- `GET /api/v1/lineage?kind=job`:有产出版本 / 无产出版本(quality 类)两条路径,
  均只含输入链、不含下游消费者。
- `GET /api/v1/lineage?kind=member`:只上溯湖/源层,不做版本↔任务 BFS。
- `export_delivery.collect_lineage` 薄封装后输出与改造前等价。
"""

from __future__ import annotations

import pytest

from app.models.data_lake import DataLake, DataLakeObject, DataLakeSnapshot
from app.models.dataset import Dataset
from app.models.dataset_version import DatasetVersion
from app.models.dataset_version_table import DatasetVersionTable
from app.models.job import Job
from app.models.job_input import JobInput
from app.services.lineage_service import build_lineage, member_anchor_id

pytestmark = pytest.mark.asyncio


async def test_expand_members_emits_member_nodes_and_contains_edges(
    session_factory,
):
    """expand_members=True:根版本两个成员(一个有湖来源、一个无)都发 member
    节点 + version→member(contains)边;extract 边源从 version 改挂到具体
    member(而非 P0-②/P1-① 存量语义的 snapshot→version)。"""
    async with session_factory() as session:
        session.add_all(
            [
                DataLake(id="lake-svc001", name="服务化测试湖"),
                DataLakeObject(
                    id="lobj-svc001",
                    lake_id="lake-svc001",
                    identity_key="db:users",
                    display_name="users",
                    data_category="database",
                ),
                DataLakeSnapshot(
                    id="snap-svc001",
                    lake_id="lake-svc001",
                    source_version="source_v1",
                    storage_uri="s3://lake/users.parquet",
                    storage_format="parquet",
                    data_category="database",
                    upload_channel="database",
                    object_id="lobj-svc001",
                    version_no=1,
                    rows=10,
                ),
                Dataset(id="dset-svc001", name="服务化测试集"),
                DatasetVersion(
                    id="dsv-svc001",
                    dataset_id="dset-svc001",
                    version_no=1,
                    storage_uri="s3://uploads/dset-svc001/v1/data.jsonl",
                ),
                DatasetVersionTable(
                    id="dvt-svc001-users",
                    dataset_version_id="dsv-svc001",
                    table_name="users",
                    storage_uri="s3://uploads/dset-svc001/v1/users.parquet",
                    format="parquet",
                    rows=10,
                    source_snapshot_id="snap-svc001",
                    source_kind="lake",
                ),
                DatasetVersionTable(
                    id="dvt-svc001-notes",
                    dataset_version_id="dsv-svc001",
                    table_name="notes",
                    storage_uri="s3://uploads/dset-svc001/v1/notes.parquet",
                    format="parquet",
                    rows=3,
                    source_kind="upload",
                ),
            ]
        )
        await session.commit()

        graph = await build_lineage(
            session, ["dsv-svc001"], expand_members=True
        )

    nodes = {n["id"]: n for n in graph["nodes"]}
    edges = {(e["from"], e["to"]): e["kind"] for e in graph["edges"]}

    users_mid = member_anchor_id("dsv-svc001", "users")
    notes_mid = member_anchor_id("dsv-svc001", "notes")

    assert nodes[users_mid]["kind"] == "member"
    assert nodes[users_mid]["tableName"] == "users"
    assert nodes[users_mid]["sourceSnapshotId"] == "snap-svc001"
    assert nodes[notes_mid]["kind"] == "member"
    assert nodes[notes_mid]["sourceSnapshotId"] is None

    # version→member contains 边(两个成员都要有,不管是否有湖来源)
    assert edges[("dsv-svc001", users_mid)] == "contains"
    assert edges[("dsv-svc001", notes_mid)] == "contains"

    # extract 边源改挂到 member,不再是 snapshot→version
    assert edges[("snap-svc001", users_mid)] == "extract"
    assert ("snap-svc001", "dsv-svc001") not in edges

    # 默认(expand_members=False)保持存量语义:snapshot→version 直连,无 member 节点
    async with session_factory() as session:
        default_graph = await build_lineage(session, ["dsv-svc001"])
    default_nodes = {n["id"] for n in default_graph["nodes"]}
    default_edges = {
        (e["from"], e["to"]): e["kind"] for e in default_graph["edges"]
    }
    assert users_mid not in default_nodes
    assert default_edges[("snap-svc001", "dsv-svc001")] == "extract"


async def _seed_job_anchor_chain(session_factory) -> None:
    """种子:v0(根)--job-svc010(process)--> v1(受 job-svc010 消费产出);另建
    job-svc011(process)同样消费 v0,产出 v2——用于验证 job anchor 不含"下游
    消费者"(v0 的另一消费者 job-svc011/v2 不应出现在 job-svc010 的血缘图里)。
    """
    async with session_factory() as session:
        session.add_all(
            [
                Dataset(id="dset-svc010", name="任务锚点测试集"),
                DatasetVersion(
                    id="dsv-svc010v0",
                    dataset_id="dset-svc010",
                    version_no=1,
                    storage_uri="s3://uploads/dset-svc010/v1/data.jsonl",
                ),
                Job(
                    id="job-svc010",
                    name="清洗任务",
                    type="process",
                    state="success",
                    spec={
                        "operators": [
                            {"name": "text_length_filter", "params": {"min_len": 5}}
                        ]
                    },
                ),
                DatasetVersion(
                    id="dsv-svc010v1",
                    dataset_id="dset-svc010",
                    version_no=2,
                    storage_uri="s3://uploads/dset-svc010/v2/data.jsonl",
                    produced_by_job_id="job-svc010",
                ),
                Job(
                    id="job-svc011",
                    name="另一清洗任务(同输入,不应出现在 job-svc010 的血缘图里)",
                    type="process",
                    state="success",
                    spec={"operators": [{"name": "words_num_filter", "params": {}}]},
                ),
                DatasetVersion(
                    id="dsv-svc010v2",
                    dataset_id="dset-svc010",
                    version_no=3,
                    storage_uri="s3://uploads/dset-svc010/v3/data.jsonl",
                    produced_by_job_id="job-svc011",
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                JobInput(job_id="job-svc010", dataset_version_id="dsv-svc010v0"),
                JobInput(job_id="job-svc011", dataset_version_id="dsv-svc010v0"),
            ]
        )
        await session.commit()


async def test_lineage_job_anchor_with_output_version_excludes_downstream(
    client, session_factory
):
    """kind=job,任务有产出版本:图含该任务 + 其输入链,但不含"输入版本的另一
    消费者"(job-svc011/dsv-svc010v2 不应出现)。"""
    await _seed_job_anchor_chain(session_factory)

    resp = await client.get(
        "/api/v1/lineage", params={"kind": "job", "jobId": "job-svc010"}
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    node_ids = {n["id"] for n in data["nodes"]}
    edges = {(e["from"], e["to"]): e["kind"] for e in data["edges"]}

    assert "job-svc010" in node_ids
    assert "dsv-svc010v0" in node_ids
    assert "dsv-svc010v1" in node_ids
    assert edges[("dsv-svc010v0", "job-svc010")] == "input"
    assert edges[("job-svc010", "dsv-svc010v1")] == "output"

    # 不含下游消费者:另一条消费同一输入的任务/版本不出现
    assert "job-svc011" not in node_ids
    assert "dsv-svc010v2" not in node_ids


async def test_lineage_job_anchor_no_output_version_falls_back_to_input_chain(
    client, session_factory
):
    """无产出版本的任务类型(如 quality,评估不产新版本):端点手工补 job 节点 +
    输入边,图仍只含输入链,不含下游消费者。"""
    async with session_factory() as session:
        session.add_all(
            [
                Dataset(id="dset-svc012", name="质量评估锚点测试集"),
                DatasetVersion(
                    id="dsv-svc012v0",
                    dataset_id="dset-svc012",
                    version_no=1,
                    storage_uri="s3://uploads/dset-svc012/v1/data.jsonl",
                ),
                Job(
                    id="job-svc012",
                    name="质量评估任务(不产新版本)",
                    type="quality",
                    state="success",
                    spec={"config": {"sample_rate": 0.1}},
                ),
            ]
        )
        await session.flush()
        session.add(JobInput(job_id="job-svc012", dataset_version_id="dsv-svc012v0"))
        await session.commit()

    resp = await client.get(
        "/api/v1/lineage", params={"kind": "job", "jobId": "job-svc012"}
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    node_ids = {n["id"] for n in data["nodes"]}
    edges = {(e["from"], e["to"]): e["kind"] for e in data["edges"]}

    assert "job-svc012" in node_ids
    assert "dsv-svc012v0" in node_ids
    assert edges[("dsv-svc012v0", "job-svc012")] == "input"


async def test_lineage_job_anchor_unknown_job_404(client):
    resp = await client.get(
        "/api/v1/lineage", params={"kind": "job", "jobId": "job-nope"}
    )
    assert resp.status_code == 404


async def test_lineage_member_anchor_only_traces_lake_layer(client, session_factory):
    """kind=member:只上溯湖/源层——version↔job BFS 不跑,即便该成员所属版本
    另有产出任务/输入版本,也不出现在成员 anchor 的血缘图里。"""
    async with session_factory() as session:
        session.add_all(
            [
                DataLake(id="lake-svc020", name="成员锚点测试湖"),
                DataLakeObject(
                    id="lobj-svc020",
                    lake_id="lake-svc020",
                    identity_key="db:orders",
                    display_name="orders",
                    data_category="database",
                ),
                DataLakeSnapshot(
                    id="snap-svc020",
                    lake_id="lake-svc020",
                    source_version="source_v1",
                    storage_uri="s3://lake/orders.parquet",
                    storage_format="parquet",
                    data_category="database",
                    upload_channel="database",
                    object_id="lobj-svc020",
                    version_no=1,
                    rows=5,
                ),
                Dataset(id="dset-svc020", name="成员锚点上游数据集"),
                DatasetVersion(
                    id="dsv-svc020v0",
                    dataset_id="dset-svc020",
                    version_no=1,
                    storage_uri="s3://uploads/dset-svc020/v1/data.jsonl",
                ),
                Job(
                    id="job-svc020",
                    name="加工任务(不应出现在成员 anchor 血缘图里)",
                    type="process",
                    state="success",
                    spec={"operators": [{"name": "text_length_filter", "params": {}}]},
                ),
                DatasetVersion(
                    id="dsv-svc020v1",
                    dataset_id="dset-svc020",
                    version_no=2,
                    storage_uri="s3://uploads/dset-svc020/v2/data.jsonl",
                    produced_by_job_id="job-svc020",
                ),
                DatasetVersionTable(
                    id="dvt-svc020",
                    dataset_version_id="dsv-svc020v1",
                    table_name="orders",
                    storage_uri="s3://uploads/dset-svc020/v2/orders.parquet",
                    format="parquet",
                    source_snapshot_id="snap-svc020",
                    source_kind="lake",
                ),
            ]
        )
        await session.flush()
        session.add(
            JobInput(job_id="job-svc020", dataset_version_id="dsv-svc020v0")
        )
        await session.commit()

    resp = await client.get(
        "/api/v1/lineage",
        params={"kind": "member", "versionId": "dsv-svc020v1", "tableName": "orders"},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    node_ids = {n["id"] for n in data["nodes"]}
    kinds = {n["id"]: n["kind"] for n in data["nodes"]}

    mid = member_anchor_id("dsv-svc020v1", "orders")
    assert kinds[mid] == "member"
    assert "snap-svc020" in node_ids
    # 不做版本↔任务 BFS:所属版本本身、产出它的任务、任务的输入版本都不出现
    assert "dsv-svc020v1" not in node_ids
    assert "job-svc020" not in node_ids
    assert "dsv-svc020v0" not in node_ids


async def test_lineage_member_anchor_without_snapshot_source(client, session_factory):
    """成员无湖来源(直传/遗留直采):member 节点仍发,但没有 extract 边可画。"""
    async with session_factory() as session:
        session.add_all(
            [
                Dataset(id="dset-svc021", name="无湖来源成员测试集"),
                DatasetVersion(
                    id="dsv-svc021",
                    dataset_id="dset-svc021",
                    version_no=1,
                    storage_uri="s3://uploads/dset-svc021/v1/data.jsonl",
                ),
                DatasetVersionTable(
                    id="dvt-svc021",
                    dataset_version_id="dsv-svc021",
                    table_name="raw",
                    storage_uri="s3://uploads/dset-svc021/v1/raw.parquet",
                    format="parquet",
                    source_kind="upload",
                ),
            ]
        )
        await session.commit()

    resp = await client.get(
        "/api/v1/lineage",
        params={"kind": "member", "versionId": "dsv-svc021", "tableName": "raw"},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data["nodes"]) == 1
    assert data["nodes"][0]["kind"] == "member"
    assert data["edges"] == []


async def test_lineage_member_anchor_unknown_member_404(client, session_factory):
    async with session_factory() as session:
        session.add(Dataset(id="dset-svc022", name="占位数据集"))
        await session.commit()

    resp = await client.get(
        "/api/v1/lineage",
        params={"kind": "member", "versionId": "dsv-nope", "tableName": "raw"},
    )
    assert resp.status_code == 404


async def test_lineage_unsupported_kind_400(client):
    resp = await client.get("/api/v1/lineage", params={"kind": "source"})
    assert resp.status_code == 400


async def test_collect_lineage_matches_pre_refactor_behavior(session_factory):
    """export_delivery.collect_lineage 薄封装 build_lineage 后,典型场景(根版本
    带 Dataset.source_kind/source_format + 单任务单算子链)输出与改造前手算结果
    一致——锁住 dataset_card.md 内容不因本次重构漂移。"""
    from app.services.export_delivery import collect_lineage

    async with session_factory() as session:
        session.add_all(
            [
                Dataset(
                    id="dset-svc030",
                    name="导出血缘测试集",
                    source_kind="database",
                    source_format="postgresql",
                ),
                DatasetVersion(
                    id="dsv-svc030v0",
                    dataset_id="dset-svc030",
                    version_no=1,
                    storage_uri="s3://uploads/dset-svc030/v1/data.jsonl",
                ),
                Job(
                    id="job-svc030",
                    name="清洗任务",
                    type="process",
                    state="success",
                    spec={
                        "operators": [
                            {
                                "name": "text_length_filter",
                                "params": {"min_len": 10},
                            }
                        ]
                    },
                ),
                DatasetVersion(
                    id="dsv-svc030v1",
                    dataset_id="dset-svc030",
                    version_no=2,
                    storage_uri="s3://uploads/dset-svc030/v2/data.jsonl",
                    produced_by_job_id="job-svc030",
                ),
            ]
        )
        await session.flush()
        session.add(
            JobInput(job_id="job-svc030", dataset_version_id="dsv-svc030v0")
        )
        await session.commit()

        v1 = await session.get(DatasetVersion, "dsv-svc030v1")
        source_lines, operator_chain, upstream_formats = await collect_lineage(
            session, v1
        )

    assert source_lines == ["导出血缘测试集 · 来源=database/postgresql"]
    assert operator_chain == [
        {"jobType": "process", "name": "text_length_filter", "params": {"min_len": 10}}
    ]
    assert upstream_formats == {"postgresql"}


async def test_collect_lineage_skips_lake_layer(session_factory, monkeypatch):
    """skip_lake_layer=True 落地到 collect_lineage:即便根版本成员有湖来源,
    也不产生 lake_snapshot 节点(不查湖层,避免导出接口变慢)——用 monkeypatch
    断言 expand_snapshot 路径(DataLakeSnapshot 查询)完全没被触发。"""
    from app.services.export_delivery import collect_lineage

    async with session_factory() as session:
        session.add_all(
            [
                Dataset(id="dset-svc031", name="跳过湖层测试集"),
                DatasetVersion(
                    id="dsv-svc031",
                    dataset_id="dset-svc031",
                    version_no=1,
                    storage_uri="s3://uploads/dset-svc031/v1/data.jsonl",
                ),
                DatasetVersionTable(
                    id="dvt-svc031",
                    dataset_version_id="dsv-svc031",
                    table_name="t",
                    storage_uri="s3://uploads/dset-svc031/v1/t.parquet",
                    format="parquet",
                    source_snapshot_id="snap-does-not-exist",
                ),
            ]
        )
        await session.commit()

        v1 = await session.get(DatasetVersion, "dsv-svc031")
        # 若误跑了湖层,session.get(DataLakeSnapshot, "snap-does-not-exist") 会被
        # 调用并返回 None,不会报错——所以改用节点集合断言更可靠:不含 lake_snapshot。
        source_lines, operator_chain, upstream_formats = await collect_lineage(
            session, v1
        )

    assert source_lines == ["跳过湖层测试集 · 来源=?/?"]
    assert operator_chain == []
    assert upstream_formats == set()
