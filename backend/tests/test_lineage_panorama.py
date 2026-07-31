"""全景森林血缘 + 放开 entity-agnostic anchor(治理整改 P2)专项用例。

覆盖:
- `build_panorama_lineage`:多根森林(含孤立数据源/孤立湖快照)、lake_id/kinds/
  since 三种过滤各自正确且不留悬空边、超量截断(truncated+totalEstimated)。
- `build_lineage(direction="down")`:新增方向,验证它确实抑制了"沿途版本的
  产出任务→该任务的其它输入"这段上游污染(正对照:direction="both" 时污染
  发生,证明测试不是空转)。
- `GET /api/v1/lineage?kind=...` 新放开的四种 anchor(source/lake_object/
  lake_snapshot/dataset_version):各自解析正确 + 未知实体 404 + 缺参 400。

不复述 `tests/test_lineage_service.py`/`tests/test_lineage_lake.py` 已锁的
kind=job/member 既有行为。
"""

from __future__ import annotations

from datetime import datetime

import pytest

from app.models.data_lake import DataLake, DataLakeObject, DataLakeSnapshot
from app.models.dataset import Dataset
from app.models.dataset_version import DatasetVersion
from app.models.dataset_version_table import DatasetVersionTable
from app.models.datasource import DataSource
from app.models.job import Job
from app.models.job_input import JobInput
from app.services.lineage_service import build_lineage, build_panorama_lineage

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# build_panorama_lineage
# ---------------------------------------------------------------------------


async def _seed_panorama_forest(session_factory) -> None:
    """两个数据源(一个有下游、一个孤立)+ 两个湖(各一条正常链 + 一个孤立快照)
    + 一个数据集版本,构成多根森林。"""
    async with session_factory() as session:
        session.add_all(
            [
                DataSource(
                    id="ds-pan001",
                    name="业务库",
                    type="database",
                    db_kind="postgresql",
                    status="connected",
                    config={},
                ),
                # 孤立数据源:从未产生快照,也没有托管直连版本
                DataSource(
                    id="ds-pan002",
                    name="孤立源",
                    type="s3",
                    status="connected",
                    config={},
                ),
                DataLake(id="lake-pan001", name="全景测试湖"),
                DataLakeObject(
                    id="lobj-pan001",
                    lake_id="lake-pan001",
                    identity_key="db:orders",
                    display_name="orders",
                    data_category="database",
                ),
                DataLakeSnapshot(
                    id="snap-pan001a",
                    lake_id="lake-pan001",
                    source_version="source_v1",
                    storage_uri="s3://lake/orders_v1.parquet",
                    storage_format="parquet",
                    data_category="database",
                    upload_channel="database",
                    datasource_id="ds-pan001",
                    object_id="lobj-pan001",
                    version_no=1,
                ),
                # 孤立快照:无 datasource_id、无 merge_inputs、未被任何数据集抽取
                DataLakeObject(
                    id="lobj-pan001b",
                    lake_id="lake-pan001",
                    identity_key="db:unused",
                    display_name="unused",
                    data_category="database",
                ),
                DataLakeSnapshot(
                    id="snap-pan001c",
                    lake_id="lake-pan001",
                    source_version="source_v2",
                    storage_uri="s3://lake/unused.parquet",
                    storage_format="parquet",
                    data_category="database",
                    upload_channel="local",
                    object_id="lobj-pan001b",
                    version_no=1,
                ),
                Dataset(id="dset-pan001", name="全景测试集"),
                DatasetVersion(
                    id="dsv-pan001",
                    dataset_id="dset-pan001",
                    version_no=1,
                    storage_uri="s3://uploads/dset-pan001/v1/data.jsonl",
                ),
                DatasetVersionTable(
                    id="dvt-pan001",
                    dataset_version_id="dsv-pan001",
                    table_name="orders",
                    storage_uri="s3://uploads/dset-pan001/v1/orders.jsonl",
                    format="jsonl",
                    source_snapshot_id="snap-pan001a",
                ),
            ]
        )
        await session.commit()


async def test_panorama_multi_root_forest_includes_isolated_nodes(
    client, session_factory
):
    await _seed_panorama_forest(session_factory)

    resp = await client.get("/api/v1/lineage/panorama")
    assert resp.status_code == 200
    data = resp.json()["data"]
    nodes = {n["id"]: n for n in data["nodes"]}

    # 多根:两个数据源都作为独立根出现(一个有下游 ingest 边,一个完全孤立)
    assert nodes["ds-pan001"]["kind"] == "datasource"
    assert nodes["ds-pan002"]["kind"] == "datasource"
    # 孤立湖快照:未被任何数据集抽取,仍必须出现(全量播种,不靠 BFS 摸到)
    assert nodes["snap-pan001c"]["kind"] == "lake_snapshot"
    assert data["truncated"] is False
    assert data["totalEstimated"] == len(data["nodes"])


async def test_panorama_lake_id_filter_scopes_to_lake_and_no_dangling_edges(
    client, session_factory
):
    """lake_id 过滤:只留该湖快照 + 其下游可达节点,不含另一个湖/无关数据源。"""
    await _seed_panorama_forest(session_factory)
    async with session_factory() as session:
        session.add_all(
            [
                DataLake(id="lake-pan002", name="另一测试湖"),
                DataLakeObject(
                    id="lobj-pan002",
                    lake_id="lake-pan002",
                    identity_key="db:users",
                    display_name="users",
                    data_category="database",
                ),
                DataLakeSnapshot(
                    id="snap-pan002a",
                    lake_id="lake-pan002",
                    source_version="source_v1",
                    storage_uri="s3://lake2/users_v1.parquet",
                    storage_format="parquet",
                    data_category="database",
                    upload_channel="local",
                    object_id="lobj-pan002",
                    version_no=1,
                ),
            ]
        )
        await session.commit()

    resp = await client.get(
        "/api/v1/lineage/panorama", params={"lakeId": "lake-pan002"}
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    node_ids = {n["id"] for n in data["nodes"]}

    assert "snap-pan002a" in node_ids
    assert "snap-pan001a" not in node_ids
    assert "snap-pan001c" not in node_ids
    assert "ds-pan001" not in node_ids
    assert "dsv-pan001" not in node_ids

    for e in data["edges"]:
        assert e["from"] in node_ids
        assert e["to"] in node_ids


async def test_panorama_emits_lake_nodes_with_contains_edges(
    client, session_factory
):
    """全景可读性整改:数据湖以一等节点入图,contains 边连到该湖全部快照——
    全局视角能直接看出"哪些快照属于哪个湖"。"""
    await _seed_panorama_forest(session_factory)

    resp = await client.get("/api/v1/lineage/panorama")
    assert resp.status_code == 200
    data = resp.json()["data"]
    nodes = {n["id"]: n for n in data["nodes"]}

    lake = nodes["lake-pan001"]
    assert lake["kind"] == "lake"
    assert lake["name"] == "全景测试湖"
    assert lake["snapshotCount"] == 2
    # 该湖两个快照(含孤立快照)都有 contains 边挂到湖节点
    contains = {
        (e["from"], e["to"]) for e in data["edges"] if e["kind"] == "contains"
    }
    assert ("lake-pan001", "snap-pan001a") in contains
    assert ("lake-pan001", "snap-pan001c") in contains


async def test_panorama_lake_id_filter_keeps_lake_node(client, session_factory):
    """lake_id 过滤后湖节点自身仍在图里(contains 边由湖指向快照,湖节点必须
    进可达性种子,否则被前向 BFS 过滤掉)。"""
    await _seed_panorama_forest(session_factory)

    resp = await client.get(
        "/api/v1/lineage/panorama", params={"lakeId": "lake-pan001"}
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    node_ids = {n["id"] for n in data["nodes"]}

    assert "lake-pan001" in node_ids
    assert "snap-pan001a" in node_ids
    for e in data["edges"]:
        assert e["from"] in node_ids
        assert e["to"] in node_ids


async def test_panorama_kinds_filter_no_dangling_edges(client, session_factory):
    await _seed_panorama_forest(session_factory)

    resp = await client.get(
        "/api/v1/lineage/panorama", params={"kinds": "datasource,lake_snapshot"}
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    node_ids = {n["id"] for n in data["nodes"]}
    kinds = {n["kind"] for n in data["nodes"]}

    assert kinds <= {"datasource", "lake_snapshot"}
    # 版本节点被过滤掉了,连带其 extract 边不应残留(否则悬边)
    assert "dsv-pan001" not in node_ids
    for e in data["edges"]:
        assert e["from"] in node_ids
        assert e["to"] in node_ids


async def test_panorama_since_filter_excludes_old_nodes_no_dangling_edges(
    client, session_factory
):
    async with session_factory() as session:
        session.add_all(
            [
                DataSource(
                    id="ds-pan010",
                    name="时间过滤源",
                    type="database",
                    status="connected",
                    config={},
                ),
                DataLake(id="lake-pan010", name="时间过滤湖"),
                DataLakeObject(
                    id="lobj-pan010",
                    lake_id="lake-pan010",
                    identity_key="db:old",
                    display_name="old",
                    data_category="database",
                ),
                # 旧快照:显式钉死创建时间在 since 之前
                DataLakeSnapshot(
                    id="snap-pan010-old",
                    lake_id="lake-pan010",
                    source_version="source_v_old",
                    storage_uri="s3://lake/old.parquet",
                    storage_format="parquet",
                    data_category="database",
                    upload_channel="database",
                    datasource_id="ds-pan010",
                    object_id="lobj-pan010",
                    version_no=1,
                    created_at=datetime(2020, 1, 1),
                ),
                # 新快照:created_at 用默认 now(),晚于 since 下限
                DataLakeSnapshot(
                    id="snap-pan010-new",
                    lake_id="lake-pan010",
                    source_version="source_v_new",
                    storage_uri="s3://lake/new.parquet",
                    storage_format="parquet",
                    data_category="database",
                    upload_channel="database",
                    object_id="lobj-pan010",
                    version_no=2,
                ),
            ]
        )
        await session.commit()

    resp = await client.get(
        "/api/v1/lineage/panorama", params={"since": "2025-01-01T00:00:00"}
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    node_ids = {n["id"] for n in data["nodes"]}

    assert "snap-pan010-old" not in node_ids
    assert "snap-pan010-new" in node_ids
    # datasource 无 createdAt,不受 since 过滤,仍应出现(即便它只连了旧快照)
    assert "ds-pan010" in node_ids

    for e in data["edges"]:
        assert e["from"] in node_ids
        assert e["to"] in node_ids


async def test_panorama_truncation_marks_truncated_with_total_estimated(
    session_factory,
):
    """超量保护:max_nodes 收窄后仍如实报告截断前的总数(不悄悄丢数据)。"""
    async with session_factory() as session:
        session.add_all(
            [
                DataSource(
                    id=f"ds-trunc{i:03d}",
                    name=f"截断测试源{i}",
                    type="s3",
                    status="connected",
                    config={},
                )
                for i in range(5)
            ]
        )
        await session.commit()

        graph = await build_panorama_lineage(session, max_nodes=3)

    assert graph["truncated"] is True
    assert len(graph["nodes"]) == 3
    assert graph["totalEstimated"] >= 5
    kept_ids = {n["id"] for n in graph["nodes"]}
    for e in graph["edges"]:
        assert e["from"] in kept_ids
        assert e["to"] in kept_ids


# ---------------------------------------------------------------------------
# build_lineage(direction="down"):抑制上游污染
# ---------------------------------------------------------------------------


async def _seed_direction_down_chain(session_factory) -> None:
    """根版本 A(来自"目标源")与无关根版本 B 一起喂给同一个合并任务,产出版本 C
    (C 的成员"结转" A 的 source_snapshot_id,模拟存量血缘里非根版本也带
    source_snapshot_id 的场景——见 lineage_service 模块 docstring)。"""
    async with session_factory() as session:
        session.add_all(
            [
                DataLake(id="lake-dd001", name="方向测试湖"),
                DataLakeObject(
                    id="lobj-dd001",
                    lake_id="lake-dd001",
                    identity_key="db:a",
                    display_name="a",
                    data_category="database",
                ),
                DataLakeSnapshot(
                    id="snap-dd001",
                    lake_id="lake-dd001",
                    source_version="source_v1",
                    storage_uri="s3://lake/a.parquet",
                    storage_format="parquet",
                    data_category="database",
                    upload_channel="database",
                    object_id="lobj-dd001",
                    version_no=1,
                ),
                Dataset(id="dset-dd001", name="方向测试集A"),
                DatasetVersion(
                    id="dsv-dd001a",
                    dataset_id="dset-dd001",
                    version_no=1,
                    storage_uri="s3://uploads/dset-dd001/v1/data.jsonl",
                ),
                DatasetVersionTable(
                    id="dvt-dd001a",
                    dataset_version_id="dsv-dd001a",
                    table_name="a",
                    storage_uri="s3://uploads/dset-dd001/v1/a.jsonl",
                    format="jsonl",
                    source_snapshot_id="snap-dd001",
                ),
                # 无关根版本 B:与 A 毫无关系,只是被同一个合并任务消费
                Dataset(id="dset-dd002", name="无关测试集B"),
                DatasetVersion(
                    id="dsv-dd001b",
                    dataset_id="dset-dd002",
                    version_no=1,
                    storage_uri="s3://uploads/dset-dd002/v1/data.jsonl",
                ),
                Job(
                    id="job-dd001",
                    name="合并任务",
                    type="process",
                    state="success",
                    spec={"operators": [{"name": "text_length_filter", "params": {}}]},
                ),
                # C:由 job-dd001 合并 A + B 产出,成员"结转"了 A 的 source_snapshot_id
                DatasetVersion(
                    id="dsv-dd001c",
                    dataset_id="dset-dd001",
                    version_no=2,
                    storage_uri="s3://uploads/dset-dd001/v2/data.jsonl",
                    produced_by_job_id="job-dd001",
                ),
                DatasetVersionTable(
                    id="dvt-dd001c",
                    dataset_version_id="dsv-dd001c",
                    table_name="a",
                    storage_uri="s3://uploads/dset-dd001/v2/a.jsonl",
                    format="jsonl",
                    source_snapshot_id="snap-dd001",
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                JobInput(job_id="job-dd001", dataset_version_id="dsv-dd001a"),
                JobInput(job_id="job-dd001", dataset_version_id="dsv-dd001b"),
            ]
        )
        await session.commit()


async def test_direction_down_excludes_producer_sibling_inputs(session_factory):
    """direction="down":从"结转"版本 C 出发,不应牵出 C 的产出任务(job-dd001)
    的另一个无关输入 B——即便 C 先于 A 被处理(anchor 顺序刻意把 C 放前面,
    确保 job-dd001 在处理 C 时还没被别的路径标记为已访问)。"""
    await _seed_direction_down_chain(session_factory)

    async with session_factory() as session:
        down_graph = await build_lineage(
            session, ["dsv-dd001c", "dsv-dd001a"], direction="down"
        )
    down_ids = {n["id"] for n in down_graph["nodes"]}
    assert "dsv-dd001b" not in down_ids

    # 正对照:同样的 anchor 顺序,direction="both" 时确实会牵出无关输入 B
    # (证明上面的断言不是因为图本来就摸不到 B,而是 direction="down" 真起了作用)。
    async with session_factory() as session:
        both_graph = await build_lineage(
            session, ["dsv-dd001c", "dsv-dd001a"], direction="both"
        )
    both_ids = {n["id"] for n in both_graph["nodes"]}
    assert "dsv-dd001b" in both_ids


# ---------------------------------------------------------------------------
# GET /api/v1/lineage?kind=source|lake_object|lake_snapshot|dataset_version
# ---------------------------------------------------------------------------


async def _seed_source_anchor_chain(session_factory) -> None:
    async with session_factory() as session:
        session.add_all(
            [
                DataSource(
                    id="ds-anc001",
                    name="锚点测试源",
                    type="database",
                    db_kind="mysql",
                    status="connected",
                    config={},
                ),
                DataLake(id="lake-anc001", name="锚点测试湖"),
                DataLakeObject(
                    id="lobj-anc001",
                    lake_id="lake-anc001",
                    identity_key="db:t",
                    display_name="t",
                    data_category="database",
                ),
                DataLakeSnapshot(
                    id="snap-anc001",
                    lake_id="lake-anc001",
                    source_version="source_v1",
                    storage_uri="s3://lake/t.parquet",
                    storage_format="parquet",
                    data_category="database",
                    upload_channel="database",
                    datasource_id="ds-anc001",
                    object_id="lobj-anc001",
                    version_no=1,
                ),
                Dataset(id="dset-anc001", name="锚点测试集"),
                DatasetVersion(
                    id="dsv-anc001",
                    dataset_id="dset-anc001",
                    version_no=1,
                    storage_uri="s3://uploads/dset-anc001/v1/data.jsonl",
                ),
                DatasetVersionTable(
                    id="dvt-anc001",
                    dataset_version_id="dsv-anc001",
                    table_name="t",
                    storage_uri="s3://uploads/dset-anc001/v1/t.jsonl",
                    format="jsonl",
                    source_snapshot_id="snap-anc001",
                ),
                Job(
                    id="job-anc001",
                    name="下游清洗任务",
                    type="process",
                    state="success",
                    spec={"operators": [{"name": "text_length_filter", "params": {}}]},
                ),
                DatasetVersion(
                    id="dsv-anc001v2",
                    dataset_id="dset-anc001",
                    version_no=2,
                    storage_uri="s3://uploads/dset-anc001/v2/data.jsonl",
                    produced_by_job_id="job-anc001",
                ),
            ]
        )
        await session.flush()
        session.add(JobInput(job_id="job-anc001", dataset_version_id="dsv-anc001"))
        await session.commit()


async def test_lineage_anchor_source_shows_downstream_chain(client, session_factory):
    await _seed_source_anchor_chain(session_factory)

    resp = await client.get(
        "/api/v1/lineage", params={"kind": "source", "sourceId": "ds-anc001"}
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    node_ids = {n["id"] for n in data["nodes"]}

    assert "ds-anc001" in node_ids
    assert "snap-anc001" in node_ids
    assert "dsv-anc001" in node_ids
    # 下游消费任务及其产出版本也应可见("该源流向哪些集/任务")
    assert "job-anc001" in node_ids
    assert "dsv-anc001v2" in node_ids


async def test_lineage_anchor_source_unknown_404(client):
    resp = await client.get(
        "/api/v1/lineage", params={"kind": "source", "sourceId": "ds-nope"}
    )
    assert resp.status_code == 404


async def test_lineage_anchor_source_missing_param_400(client):
    resp = await client.get("/api/v1/lineage", params={"kind": "source"})
    assert resp.status_code == 400


async def test_lineage_anchor_lake_object_resolves_all_snapshots_and_downstream(
    client, session_factory
):
    await _seed_source_anchor_chain(session_factory)

    resp = await client.get(
        "/api/v1/lineage", params={"kind": "lake_object", "objectId": "lobj-anc001"}
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    node_ids = {n["id"] for n in data["nodes"]}

    assert "snap-anc001" in node_ids
    assert "dsv-anc001" in node_ids
    assert "job-anc001" in node_ids


async def test_lineage_anchor_lake_object_unknown_404(client):
    resp = await client.get(
        "/api/v1/lineage", params={"kind": "lake_object", "objectId": "lobj-nope"}
    )
    assert resp.status_code == 404


async def test_lineage_anchor_lake_object_missing_param_400(client):
    resp = await client.get("/api/v1/lineage", params={"kind": "lake_object"})
    assert resp.status_code == 400


async def test_lineage_anchor_lake_snapshot_shows_upstream_and_downstream(
    client, session_factory
):
    """kind=lake_snapshot:单快照为根,既能看到自身上游(merge 祖先,expand_snapshot
    内建行为)也能看到下游(被哪个数据集抽取及其后续消费任务)。"""
    async with session_factory() as session:
        session.add_all(
            [
                DataLake(id="lake-anc002", name="快照锚点测试湖"),
                DataLakeObject(
                    id="lobj-anc002",
                    lake_id="lake-anc002",
                    identity_key="db:t2",
                    display_name="t2",
                    data_category="database",
                ),
                DataLakeSnapshot(
                    id="snap-anc002-src",
                    lake_id="lake-anc002",
                    source_version="source_v1",
                    storage_uri="s3://lake/t2_v1.parquet",
                    storage_format="parquet",
                    data_category="database",
                    upload_channel="database",
                    object_id="lobj-anc002",
                    version_no=1,
                ),
                DataLakeSnapshot(
                    id="snap-anc002-merged",
                    lake_id="lake-anc002",
                    source_version="source_v2",
                    storage_uri="s3://lake/t2_merged.parquet",
                    storage_format="parquet",
                    data_category="database",
                    upload_channel="merge",
                    object_id="lobj-anc002",
                    version_no=2,
                    merge_inputs=[
                        {
                            "object_id": "lobj-anc002",
                            "snapshot_id": "snap-anc002-src",
                            "version_no": 1,
                        }
                    ],
                ),
                Dataset(id="dset-anc002", name="快照锚点测试集"),
                DatasetVersion(
                    id="dsv-anc002",
                    dataset_id="dset-anc002",
                    version_no=1,
                    storage_uri="s3://uploads/dset-anc002/v1/data.jsonl",
                ),
                DatasetVersionTable(
                    id="dvt-anc002",
                    dataset_version_id="dsv-anc002",
                    table_name="t2",
                    storage_uri="s3://uploads/dset-anc002/v1/t2.jsonl",
                    format="jsonl",
                    source_snapshot_id="snap-anc002-merged",
                ),
            ]
        )
        await session.commit()

    resp = await client.get(
        "/api/v1/lineage",
        params={"kind": "lake_snapshot", "snapshotId": "snap-anc002-merged"},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    node_ids = {n["id"] for n in data["nodes"]}

    assert "snap-anc002-src" in node_ids  # 上游(merge 祖先)
    assert "dsv-anc002" in node_ids  # 下游(被抽取的数据集版本)


async def test_lineage_anchor_lake_snapshot_unknown_404(client):
    resp = await client.get(
        "/api/v1/lineage", params={"kind": "lake_snapshot", "snapshotId": "snap-nope"}
    )
    assert resp.status_code == 404


async def test_lineage_anchor_lake_snapshot_missing_param_400(client):
    resp = await client.get("/api/v1/lineage", params={"kind": "lake_snapshot"})
    assert resp.status_code == 400


async def test_lineage_anchor_dataset_version_is_bidirectional(
    client, session_factory
):
    """kind=dataset_version:单版本为根,direction=both——既能看到产出它的任务
    (及其输入),也能看到消费它的下游任务。"""
    async with session_factory() as session:
        session.add_all(
            [
                Dataset(id="dset-anc003", name="版本锚点测试集"),
                DatasetVersion(
                    id="dsv-anc003v0",
                    dataset_id="dset-anc003",
                    version_no=1,
                    storage_uri="s3://uploads/dset-anc003/v1/data.jsonl",
                ),
                Job(
                    id="job-anc003up",
                    name="上游产出任务",
                    type="process",
                    state="success",
                    spec={"operators": [{"name": "text_length_filter", "params": {}}]},
                ),
                DatasetVersion(
                    id="dsv-anc003v1",
                    dataset_id="dset-anc003",
                    version_no=2,
                    storage_uri="s3://uploads/dset-anc003/v2/data.jsonl",
                    produced_by_job_id="job-anc003up",
                ),
                Job(
                    id="job-anc003down",
                    name="下游消费任务",
                    type="process",
                    state="success",
                    spec={"operators": [{"name": "words_num_filter", "params": {}}]},
                ),
                DatasetVersion(
                    id="dsv-anc003v2",
                    dataset_id="dset-anc003",
                    version_no=3,
                    storage_uri="s3://uploads/dset-anc003/v3/data.jsonl",
                    produced_by_job_id="job-anc003down",
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                JobInput(
                    job_id="job-anc003up", dataset_version_id="dsv-anc003v0"
                ),
                JobInput(
                    job_id="job-anc003down", dataset_version_id="dsv-anc003v1"
                ),
            ]
        )
        await session.commit()

    resp = await client.get(
        "/api/v1/lineage",
        params={"kind": "dataset_version", "versionId": "dsv-anc003v1"},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    node_ids = {n["id"] for n in data["nodes"]}

    assert "job-anc003up" in node_ids
    assert "dsv-anc003v0" in node_ids
    assert "job-anc003down" in node_ids
    assert "dsv-anc003v2" in node_ids


async def test_lineage_anchor_dataset_version_unknown_404(client):
    resp = await client.get(
        "/api/v1/lineage",
        params={"kind": "dataset_version", "versionId": "dsv-nope"},
    )
    assert resp.status_code == 404


async def test_lineage_anchor_dataset_version_missing_param_400(client):
    resp = await client.get("/api/v1/lineage", params={"kind": "dataset_version"})
    assert resp.status_code == 400
