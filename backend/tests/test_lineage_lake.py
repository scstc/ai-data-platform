"""数据血缘湖/源层扩展(治理整改)端到端用例。

GET /api/v1/datasets/{id}/lineage 在版本↔任务图之上补齐上游:
- 根版本经成员 source_snapshot_id → lake_snapshot 节点 + extract 边;
- 快照 merge_inputs → 上游快照 + merge 边;datasource_id → datasource 节点 + ingest 边;
- 兜底:根版本无湖快照但有 source_datasource_id → hosted_source 边。
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


async def _seed_lake_chain(session_factory) -> None:
    """种子:数据源 → 采集任务 → 湖(源快照 + 合并快照)→ 数据集根版本成员。"""
    async with session_factory() as session:
        session.add_all(
            [
                DataSource(
                    id="ds-lin001",
                    name="业务库 PG",
                    type="database",
                    db_kind="postgresql",
                    status="connected",
                    config={},
                ),
                IngestTask(
                    id="task-lin001",
                    name="每日订单采集",
                    datasource_id="ds-lin001",
                    datasource_name="业务库 PG",
                    schedule={"mode": "once"},
                    status="success",
                    logs=[],
                ),
                DataLake(id="lake-lin001", name="订单湖"),
                DataLakeObject(
                    id="lobj-lin001",
                    lake_id="lake-lin001",
                    identity_key="db:orders",
                    display_name="orders",
                    data_category="database",
                ),
                # 源快照:数据库采集入湖(有 datasource/ingest_task)
                DataLakeSnapshot(
                    id="snap-lin-src",
                    lake_id="lake-lin001",
                    source_version="source_v20260701_01_pg",
                    storage_uri="s3://lake/orders_v1.parquet",
                    storage_format="parquet",
                    data_category="database",
                    upload_channel="database",
                    datasource_id="ds-lin001",
                    ingest_task_id="task-lin001",
                    source_metadata={"db_table": "orders", "db_engine": "postgresql"},
                    object_id="lobj-lin001",
                    version_no=1,
                    rows=100,
                ),
                # 合并快照:merge_inputs 指向源快照
                DataLakeSnapshot(
                    id="snap-lin-merged",
                    lake_id="lake-lin001",
                    source_version="source_v20260702_01_merge",
                    storage_uri="s3://lake/orders_merged.parquet",
                    storage_format="parquet",
                    data_category="database",
                    upload_channel="merge",
                    object_id="lobj-lin001",
                    version_no=2,
                    merge_inputs=[
                        {
                            "object_id": "lobj-lin001",
                            "snapshot_id": "snap-lin-src",
                            "version_no": 1,
                        }
                    ],
                    rows=100,
                ),
                Dataset(id="dset-lin001", name="订单数据集"),
                # 根版本(无产出任务),成员抽取自合并快照
                DatasetVersion(
                    id="dsv-lin001",
                    dataset_id="dset-lin001",
                    version_no=1,
                    storage_uri="s3://uploads/dset-lin001/v1/data.jsonl",
                ),
                DatasetVersionTable(
                    id="dvt-lin001",
                    dataset_version_id="dsv-lin001",
                    table_name="orders",
                    storage_uri="s3://uploads/dset-lin001/v1/orders.jsonl",
                    format="jsonl",
                    source_snapshot_id="snap-lin-merged",
                ),
            ]
        )
        await session.commit()


async def test_lineage_extends_to_lake_and_datasource(client, session_factory):
    """根版本 → extract 边 → 湖快照 → merge/ingest 边 → 上游快照/数据源。"""
    await _seed_lake_chain(session_factory)

    resp = await client.get("/api/v1/datasets/dset-lin001/lineage")
    assert resp.status_code == 200
    data = resp.json()["data"]
    nodes = {n["id"]: n for n in data["nodes"]}
    edges = {(e["from"], e["to"]): e["kind"] for e in data["edges"]}

    # 湖快照节点(合并快照 + 其 merge 上游源快照)
    merged = nodes["snap-lin-merged"]
    assert merged["kind"] == "lake_snapshot"
    assert merged["lakeName"] == "订单湖"
    assert merged["name"] == "orders"
    assert merged["versionNo"] == 2
    src = nodes["snap-lin-src"]
    assert src["kind"] == "lake_snapshot"
    assert src["sourceSummary"] == "表 orders"
    assert src["ingestTaskName"] == "每日订单采集"

    # 数据源节点
    ds = nodes["ds-lin001"]
    assert ds["kind"] == "datasource"
    assert ds["sourceType"] == "database"
    assert ds["dbKind"] == "postgresql"

    # 边:快照→根版本(extract)、源快照→合并快照(merge)、数据源→源快照(ingest)
    assert edges[("snap-lin-merged", "dsv-lin001")] == "extract"
    assert edges[("snap-lin-src", "snap-lin-merged")] == "merge"
    assert edges[("ds-lin001", "snap-lin-src")] == "ingest"


async def test_lineage_hosted_source_fallback(client, session_factory):
    """根版本无湖快照成员但有 source_datasource_id → hosted_source 兜底边。"""
    async with session_factory() as session:
        session.add_all(
            [
                DataSource(
                    id="ds-lin002",
                    name="外部 S3",
                    type="s3",
                    status="connected",
                    config={},
                ),
                Dataset(id="dset-lin002", name="托管数据集"),
                DatasetVersion(
                    id="dsv-lin002",
                    dataset_id="dset-lin002",
                    version_no=1,
                    storage_uri="s3://ext/data.jsonl",
                    origin="hosted",
                    source_datasource_id="ds-lin002",
                ),
            ]
        )
        await session.commit()

    resp = await client.get("/api/v1/datasets/dset-lin002/lineage")
    assert resp.status_code == 200
    data = resp.json()["data"]
    kinds = {n["id"]: n["kind"] for n in data["nodes"]}
    edges = {(e["from"], e["to"]): e["kind"] for e in data["edges"]}

    assert kinds["ds-lin002"] == "datasource"
    assert edges[("ds-lin002", "dsv-lin002")] == "hosted_source"


async def test_lineage_ingest_job_links_datasource(client, session_factory):
    """采集任务节点(jobType=ingest)经 ingest_task 回指数据源 → ingest 边。"""
    async with session_factory() as session:
        session.add_all(
            [
                DataSource(
                    id="ds-lin003",
                    name="订单库",
                    type="database",
                    db_kind="mysql",
                    status="connected",
                    config={},
                ),
                IngestTask(
                    id="task-lin003",
                    name="订单直落采集",
                    datasource_id="ds-lin003",
                    datasource_name="订单库",
                    schedule={"mode": "once"},
                    status="success",
                    logs=[],
                ),
                Job(
                    id="job-lin003",
                    name="订单直落采集 #1",
                    type="ingest",
                    ingest_task_id="task-lin003",
                    state="success",
                ),
                Dataset(id="dset-lin004", name="直落数据集"),
                # 绕湖直落:版本由 ingest job 产出(produced_by_job_id)
                DatasetVersion(
                    id="dsv-lin004",
                    dataset_id="dset-lin004",
                    version_no=1,
                    storage_uri="s3://uploads/dset-lin004/v1/data.jsonl",
                    produced_by_job_id="job-lin003",
                ),
            ]
        )
        await session.commit()

    resp = await client.get("/api/v1/datasets/dset-lin004/lineage")
    assert resp.status_code == 200
    data = resp.json()["data"]
    nodes = {n["id"]: n for n in data["nodes"]}
    edges = {(e["from"], e["to"]): e["kind"] for e in data["edges"]}

    assert nodes["job-lin003"]["kind"] == "job"
    assert nodes["ds-lin003"]["kind"] == "datasource"
    # 边方向:数据源 → 采集任务(kind=ingest);任务 → 版本(kind=output)沿用旧逻辑
    assert edges[("ds-lin003", "job-lin003")] == "ingest"
    assert edges[("job-lin003", "dsv-lin004")] == "output"


async def test_lineage_shared_merge_ancestor_not_depth_locked(
    client, session_factory
):
    """两个根版本经不同长度 merge 链摸到同一祖先:后到但预算更充裕的路径必须能
    继续上溯(回归钉:snap_depth 按最小 ldepth 记忆化,不能只记'访问过')。"""
    async with session_factory() as session:
        # 合并链 S0 ← S1 ← S2 ← S3 ← S4 ← S5(S_n 的 merge_inputs 指向 S_{n-1})
        snaps = []
        for i in range(6):
            snaps.append(
                DataLakeSnapshot(
                    id=f"snap-chain-{i}",
                    lake_id="lake-lin002",
                    source_version=f"source_v2026070{i}_01_m",
                    storage_uri=f"s3://lake/chain_{i}.parquet",
                    storage_format="parquet",
                    data_category="database",
                    upload_channel="merge" if i else "database",
                    object_id="lobj-lin002",
                    version_no=i + 1,
                    merge_inputs=(
                        [
                            {
                                "object_id": "lobj-lin002",
                                "snapshot_id": f"snap-chain-{i - 1}",
                                "version_no": i,
                            }
                        ]
                        if i
                        else None
                    ),
                )
            )
        session.add_all(
            [
                DataLake(id="lake-lin002", name="链路湖"),
                DataLakeObject(
                    id="lobj-lin002",
                    lake_id="lake-lin002",
                    identity_key="db:chain",
                    display_name="chain",
                    data_category="database",
                ),
                *snaps,
                Dataset(id="dset-lin005", name="链路数据集"),
                # 根版本 A 成员指向 S5(深链):S5(0)→S4(1)→S3(2)→S2(3)→S1(4) 停,
                # S0 在 A 的预算内不可达
                DatasetVersion(
                    id="dsv-lin005a",
                    dataset_id="dset-lin005",
                    version_no=1,
                    storage_uri="s3://uploads/dset-lin005/v1/data.jsonl",
                ),
                DatasetVersionTable(
                    id="dvt-lin005a",
                    dataset_version_id="dsv-lin005a",
                    table_name="data",
                    storage_uri="s3://uploads/dset-lin005/v1/data.jsonl",
                    format="jsonl",
                    source_snapshot_id="snap-chain-5",
                ),
                # 根版本 B 成员指向 S3:以 ldepth=0 重入 S3,预算足以摸到 S0
                DatasetVersion(
                    id="dsv-lin005b",
                    dataset_id="dset-lin005",
                    version_no=2,
                    storage_uri="s3://uploads/dset-lin005/v2/data.jsonl",
                ),
                DatasetVersionTable(
                    id="dvt-lin005b",
                    dataset_version_id="dsv-lin005b",
                    table_name="data",
                    storage_uri="s3://uploads/dset-lin005/v2/data.jsonl",
                    format="jsonl",
                    source_snapshot_id="snap-chain-3",
                ),
            ]
        )
        await session.commit()

    resp = await client.get("/api/v1/datasets/dset-lin005/lineage")
    assert resp.status_code == 200
    data = resp.json()["data"]
    node_ids = {n["id"] for n in data["nodes"]}
    edges = {(e["from"], e["to"]): e["kind"] for e in data["edges"]}

    # 无论 A/B 谁先遍历,S0 都必须经 B(ldepth=0 起步)可达
    assert "snap-chain-0" in node_ids
    assert edges[("snap-chain-0", "snap-chain-1")] == "merge"
    assert edges[("snap-chain-5", "dsv-lin005a")] == "extract"
    assert edges[("snap-chain-3", "dsv-lin005b")] == "extract"


async def test_lineage_plain_root_version_unchanged(client, session_factory):
    """无任何来源线索的根版本:图退化为单版本节点,不多吐节点/边(向后兼容)。"""
    async with session_factory() as session:
        session.add_all(
            [
                Dataset(id="dset-lin003", name="裸上传数据集"),
                DatasetVersion(
                    id="dsv-lin003",
                    dataset_id="dset-lin003",
                    version_no=1,
                    storage_uri="s3://uploads/dset-lin003/v1/data.jsonl",
                ),
            ]
        )
        await session.commit()

    resp = await client.get("/api/v1/datasets/dset-lin003/lineage")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert [n["kind"] for n in data["nodes"]] == ["version"]
    assert data["edges"] == []


async def test_lineage_member_level_operators_and_sources(client, session_factory):
    """多表版本 + member_configs 任务:血缘须保留「成员↔算子」归属,且输入版本
    节点须能看到各成员来自哪个湖快照(回归钉:job_node 曾按算子名跨成员去重
    压平算子链,丢失成员归属;版本节点也不曾暴露逐成员来源)。"""
    async with session_factory() as session:
        session.add_all(
            [
                DataLake(id="lake-lin006", name="会员湖"),
                DataLakeObject(
                    id="lobj-lin006",
                    lake_id="lake-lin006",
                    identity_key="db:users",
                    display_name="users",
                    data_category="database",
                ),
                DataLakeSnapshot(
                    id="snap-lin006",
                    lake_id="lake-lin006",
                    source_version="source_v20260703_01_pg",
                    storage_uri="s3://lake/users_v1.parquet",
                    storage_format="parquet",
                    data_category="database",
                    upload_channel="database",
                    object_id="lobj-lin006",
                    version_no=3,
                    rows=50,
                ),
                Dataset(id="dset-lin006", name="成员级血缘数据集"),
                DatasetVersion(
                    id="dsv-lin006v1",
                    dataset_id="dset-lin006",
                    version_no=1,
                    storage_uri="s3://uploads/dset-lin006/v1/data.jsonl",
                ),
                DatasetVersionTable(
                    id="dvt-lin006-users",
                    dataset_version_id="dsv-lin006v1",
                    table_name="users",
                    storage_uri="s3://uploads/dset-lin006/v1/users.parquet",
                    format="parquet",
                    rows=50,
                    source_snapshot_id="snap-lin006",
                    source_upload_channel="database",
                ),
                DatasetVersionTable(
                    id="dvt-lin006-orders",
                    dataset_version_id="dsv-lin006v1",
                    table_name="orders",
                    storage_uri="s3://uploads/dset-lin006/v1/orders.parquet",
                    format="parquet",
                    rows=80,
                ),
                Job(
                    id="job-lin006",
                    name="成员级算子任务",
                    type="process",
                    state="success",
                    spec={
                        "member_configs": [
                            {
                                "member_name": "users",
                                "operators": [
                                    {
                                        "name": "text_length_filter",
                                        "params": {"min_len": 5},
                                    }
                                ],
                            },
                            {
                                "member_name": "orders",
                                "operators": [
                                    {
                                        "name": "text_length_filter",
                                        "params": {"min_len": 20},
                                    },
                                    {
                                        "name": "words_num_filter",
                                        "params": {"min_num": 2},
                                    },
                                ],
                            },
                        ]
                    },
                ),
                DatasetVersion(
                    id="dsv-lin006v2",
                    dataset_id="dset-lin006",
                    version_no=2,
                    storage_uri="s3://uploads/dset-lin006/v2/data.jsonl",
                    produced_by_job_id="job-lin006",
                ),
            ]
        )
        await session.flush()
        session.add(JobInput(job_id="job-lin006", dataset_version_id="dsv-lin006v1"))
        await session.commit()

    resp = await client.get("/api/v1/datasets/dset-lin006/lineage")
    assert resp.status_code == 200
    data = resp.json()["data"]
    nodes = {n["id"]: n for n in data["nodes"]}

    # 成员级算子链:各成员独立分组,同名算子不同参数没有被跨成员去重吞掉
    job_node = nodes["job-lin006"]
    member_ops = {
        m["memberName"]: m["operators"] for m in job_node["memberOperators"]
    }
    assert member_ops["users"] == [
        {"name": "text_length_filter", "params": {"min_len": 5}}
    ]
    assert member_ops["orders"] == [
        {"name": "text_length_filter", "params": {"min_len": 20}},
        {"name": "words_num_filter", "params": {"min_num": 2}},
    ]

    # 输入版本节点的成员:tableName + sourceSnapshotId/sourceName
    v1 = nodes["dsv-lin006v1"]
    members_by_table = {m["tableName"]: m for m in v1["members"]}
    assert members_by_table["users"]["sourceSnapshotId"] == "snap-lin006"
    assert members_by_table["users"]["sourceName"] == "users @v3"
    assert members_by_table["orders"]["sourceSnapshotId"] is None
