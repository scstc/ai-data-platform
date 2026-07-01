"""数据源 → 数据湖 → 数据集的一站式接入流程。

按数据治理规范（docs/数据治理.md），所有外部数据源应先入数据湖归档，
再从湖中抽取到数据集。本模块提供该完整链路的高层封装：

    外部数据源
      ↓ (connector.fetch_records)
    原始记录
      ↓ (ingest_to_lake_parquet)
    数据湖快照（source_v 版本）
      ↓ (extract_from_lake_snapshot + 血缘注入)
    带血缘的记录
      ↓ (add_table_member)
    数据集版本成员（dataset_v）

设计要点：
- 湖集分离：数据入湖后即固化归档，永不修改
- 全链路血缘：血缘字段在抽取时自动注入
- 追溯锚点：数据集 note 记录 lake_id + source_version，便于反向追溯
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.data_lake import DataLake, DataLakeSnapshot
from app.models.dataset_version import DatasetVersion
from app.models.dataset_version_table import DatasetVersionTable
from app.services.data_lake import ingest_to_lake_parquet
from app.services.lake_extract import extract_from_lake_snapshot


async def ingest_pg_to_dataset_via_lake(
    db: AsyncSession,
    *,
    lake_id: str,
    records: list[dict[str, Any]],
    dataset_id: str,
    table_name: str = "data",
    source_type: str = "postgresql",
    source_metadata: dict[str, Any] | None = None,
    datasource_id: str | None = None,
    task_name: str = "",
    datasource_name: str = "",
    produced_by_job_id: str | None = None,
    ingest_task_id: str | None = None,
) -> tuple[DataLakeSnapshot, DatasetVersion, DatasetVersionTable]:
    """PostgreSQL 记录 → 数据湖 → 数据集（一站式）。

    这是 PG 族数据源经湖接入的标准入口。相较于直接 `run_pg_ingest`，本函数：
    1. 先将原始记录以 Parquet 格式入湖（source_v 快照）
    2. 从湖中读取并注入血缘字段
    3. 落地为数据集版本的表成员

    Args:
        db: 数据库会话
        lake_id: 目标数据湖 ID
        records: 从 PG 拉取的原始记录（PgConnector.fetch_records 产出）
        dataset_id: 目标数据集 ID
        table_name: 表成员名称（多表时用表名，单查询用 "data"）
        source_type: 数据源类型（postgresql/hologres/kingbase 等）
        source_metadata: 源头元数据（db_schema/db_table/db_engine）
        task_name: 采集任务名（记入数据集版本 note）
        datasource_name: 数据源名（记入数据集版本 note）
        produced_by_job_id: 产出该版本的 job ID
        ingest_task_id: 关联的采集任务 ID（记入快照追溯）

    Returns:
        (数据湖快照, 数据集版本, 数据集版本成员) 三元组
    """
    from app.services.landing import add_table_member

    # 1. 入湖（原样归档）
    snapshot = await ingest_to_lake_parquet(
        db,
        lake_id=lake_id,
        data=records,
        source_type=source_type,
        source_metadata=source_metadata,
        upload_channel="database",
        datasource_id=datasource_id,
        ingest_task_id=ingest_task_id,
    )

    # 2. 从湖中抽取（注入血缘）
    enriched_records = await extract_from_lake_snapshot(
        db, lake_id, snapshot.source_version, inject_lineage=True
    )

    # 3. 落地为数据集版本成员
    note_parts = []
    if task_name:
        note_parts.append(f"采集落地: {task_name}")
    if datasource_name:
        note_parts.append(f"来源 {datasource_name}")
    note_parts.append(f"经湖 {snapshot.source_version}")
    note = " | ".join(note_parts)

    version, member = await add_table_member(
        db,
        dataset_id,
        enriched_records,
        table_name=table_name,
        semantic_type="structured",  # PG 表数据统一为 structured
        source_format="db",
        note=note,
        produced_by_job_id=produced_by_job_id,
        storage_format="parquet",
    )

    return snapshot, version, member


async def ingest_file_to_dataset_via_lake(
    db: AsyncSession,
    *,
    lake_id: str,
    file_content: bytes,
    original_filename: str,
    data_category: str,
    dataset_id: str,
    table_name: str | None = None,
    upload_channel: str = "local",
    datasource_id: str | None = None,
    source_metadata: dict[str, Any] | None = None,
    produced_by_job_id: str | None = None,
    ingest_task_id: str | None = None,
) -> DataLakeSnapshot:
    """文档/多媒体文件 → 数据湖 → 数据集（一站式）。

    对于非结构化文件（PDF/DOCX/图片/音视频），流程：
    1. 原格式入湖（source_v 快照，保留原扩展名）
    2. 数据集侧的落地需要额外的解析步骤（PDF → 文本块、图片 → 描述等）
       该步骤由 landing.py 的现有逻辑处理，本函数只负责入湖归档

    Args:
        db: 数据库会话
        lake_id: 目标数据湖 ID
        file_content: 文件原始内容（字节）
        original_filename: 原始文件名
        data_category: 数据类型（document/image/audio/video/text）
        dataset_id: 目标数据集 ID（预留，本期文件入湖后由现有流程落集）
        table_name: 表成员名称（默认取文件名去扩展）
        upload_channel: 上传渠道（oss/obs/minio/api/local）
        source_metadata: 源头元数据（bucket_name/obj_key 等）
        produced_by_job_id: 产出该版本的 job ID
        ingest_task_id: 关联的采集任务 ID

    Returns:
        数据湖快照对象

    Note:
        本函数仅完成入湖归档；从湖抽取到数据集的解析逻辑由第二层（数据抽取
        与前置解析层）承担，涉及 markitdown、ASR、OCR 等外部依赖，本期仅
        做接口预留。参见 docs/数据治理.md §2.2。
    """
    from app.services.data_lake import ingest_to_lake_raw

    # 1. 原格式入湖
    snapshot = await ingest_to_lake_raw(
        db,
        lake_id=lake_id,
        file_content=file_content,
        original_filename=original_filename,
        data_category=data_category,
        upload_channel=upload_channel,
        datasource_id=datasource_id,
        source_metadata=source_metadata,
        ingest_task_id=ingest_task_id,
    )

    # 2. 从湖抽取到数据集（本期仅返回 snapshot，落集由调用方按原流程处理）
    # 未来实现：markitdown 解析文档 → 文本块 → JSONL → add_table_member
    # 详见 docs/数据治理.md §2.2 数据抽取与前置解析层
    return snapshot


async def ensure_default_lake(
    db: AsyncSession,
    *,
    owner: str = "admin",
    creator: str = "admin",
    dept_id: str | None = None,
    name: str = "默认数据湖",
) -> DataLake:
    """按 owner 拿或建一个默认数据湖（多源汇聚，不与单一数据源绑定）。

    治理文档语义调整后：湖 = 多源汇聚的容器，一个湖可以承接数据库/对象存储/
    HDFS/本地/API 多种来源的快照。这里按 owner 维度维护一个默认湖，供未指定
    目标湖的接入任务落地；用户也可以显式创建业务专用湖并在接入时指定。

    Args:
        db: 数据库会话
        owner: 所有者（用于查找已存在的默认湖）
        creator: 创建人（首次创建时写入）
        dept_id: 所属部门
        name: 默认湖名称

    Returns:
        数据湖对象（新建或已存在）
    """
    from sqlalchemy import select

    from app.services.data_lake import create_data_lake

    # 查询该 owner 名下的默认湖
    result = await db.execute(
        select(DataLake).where(DataLake.owner == owner, DataLake.name == name)
    )
    lake = result.scalar_one_or_none()
    if lake:
        return lake

    return await create_data_lake(
        db,
        name=name,
        description=f"{owner} 的默认数据湖（多源汇聚，自动创建）",
        owner=owner,
        creator=creator,
        dept_id=dept_id,
    )
