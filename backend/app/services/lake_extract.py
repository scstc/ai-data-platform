"""数据湖抽取层 - 从数据湖读取原始数据并转换为数据集。

第二层职责：
- 从数据湖中按需筛选、抽取指定版本的原始数据
- 完成格式解析与初步结构化（Parquet → 记录、文档 → 文本块）
- 注入血缘追踪字段（source_version, source_category, upload_channel 等）
- 生成中间 JSONL 供数据集落地
"""

from __future__ import annotations

import asyncio
import io
from typing import Any

import pyarrow.parquet as pq
from minio.error import S3Error
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.data_lake import DataLakeSnapshot
from app.services.data_lake import get_snapshot_by_version
from app.services.external_store import ExternalStoreError, client_for, parse_s3_uri


async def extract_from_lake_snapshot(
    db: AsyncSession,
    lake_id: str,
    source_version: str,
    *,
    inject_lineage: bool = True,
) -> list[dict[str, Any]]:
    """从数据湖快照中抽取数据并注入血缘字段。

    Args:
        db: 数据库会话
        lake_id: 数据湖 ID
        source_version: 源头快照版本号（如 source_v20260701_01_mysql）
        inject_lineage: 是否注入血缘追踪字段

    Returns:
        记录列表（已注入血缘字段）

    Raises:
        ExternalStoreError: 快照不存在或读取失败
    """
    # 1. 获取快照
    snapshot = await get_snapshot_by_version(db, lake_id, source_version)
    if not snapshot:
        raise ExternalStoreError(f"快照不存在: {lake_id}/{source_version}")

    # 2. 根据存储格式读取数据
    if snapshot.storage_format == "parquet":
        records = await _read_parquet_from_snapshot(snapshot)
    else:
        raise ExternalStoreError(
            f"不支持的存储格式: {snapshot.storage_format}（当前仅支持 parquet）"
        )

    # 3. 注入血缘追踪字段
    if inject_lineage:
        records = _inject_lineage_fields(records, snapshot)

    return records


async def _read_parquet_from_snapshot(
    snapshot: DataLakeSnapshot,
) -> list[dict[str, Any]]:
    """从快照的 Parquet 文件中读取数据。

    Args:
        snapshot: 数据湖快照

    Returns:
        记录列表
    """
    # 1. 解析 S3 URI
    bucket, key = parse_s3_uri(snapshot.storage_uri)

    # 2. 从平台 MinIO 下载
    config = {
        "endpoint": settings.storage_minio_endpoint,
        "accessKey": settings.storage_minio_access_key,
        "secretKey": settings.storage_minio_secret_key,
    }
    client = client_for(config)

    # 3. 下载到内存
    def _get():
        response = client.get_object(bucket, key)
        return response.read()

    try:
        parquet_bytes = await asyncio.to_thread(_get)
    except S3Error as exc:
        raise ExternalStoreError(f"读取快照文件失败: {exc}") from exc

    # 4. 解析 Parquet
    parquet_buffer = io.BytesIO(parquet_bytes)
    table = pq.read_table(parquet_buffer)
    df = table.to_pandas()

    # 5. 转换为记录列表
    return df.to_dict(orient="records")


def _inject_lineage_fields(
    records: list[dict[str, Any]], snapshot: DataLakeSnapshot
) -> list[dict[str, Any]]:
    """向记录中注入血缘追踪字段。

    根据数据治理规范，注入以下通用血缘字段：
    - source_version: 源头快照版本号
    - source_category: 数据源类型（database/file/media）
    - upload_channel: 上传渠道（oss/obs/minio/api/local/database）
    - data_lake_snapshot_id: 快照 ID（用于追溯）

    差异化溯源字段从 source_metadata 提取：
    - 数据库数据：db_schema, db_table, db_engine
    - 对象存储数据：bucket_name, obj_key
    - 本地/API 文件：original_filename
    """
    enriched = []
    for record in records:
        # 深拷贝避免修改原记录
        enriched_record = {**record}

        # 通用血缘字段
        enriched_record["source_version"] = snapshot.source_version
        enriched_record["source_category"] = snapshot.data_category
        enriched_record["upload_channel"] = snapshot.upload_channel
        enriched_record["data_lake_snapshot_id"] = snapshot.id

        # 差异化溯源字段（从 source_metadata 提取）
        if snapshot.source_metadata:
            # 数据库数据
            if "db_schema" in snapshot.source_metadata:
                enriched_record["db_schema"] = snapshot.source_metadata["db_schema"]
            if "db_table" in snapshot.source_metadata:
                enriched_record["db_table"] = snapshot.source_metadata["db_table"]
            if "db_engine" in snapshot.source_metadata:
                enriched_record["db_engine"] = snapshot.source_metadata["db_engine"]

            # 对象存储数据
            if "bucket_name" in snapshot.source_metadata:
                enriched_record["bucket_name"] = snapshot.source_metadata[
                    "bucket_name"
                ]
            if "obj_key" in snapshot.source_metadata:
                enriched_record["obj_key"] = snapshot.source_metadata["obj_key"]

            # 本地/API 文件
            if "original_filename" in snapshot.source_metadata:
                enriched_record["original_filename"] = snapshot.source_metadata[
                    "original_filename"
                ]

        enriched.append(enriched_record)

    return enriched


async def extract_and_land_from_lake(
    db: AsyncSession,
    lake_id: str,
    source_version: str,
    dataset_id: str,
    table_name: str = "data",
    *,
    note: str | None = None,
    produced_by_job_id: str | None = None,
) -> tuple[Any, Any]:
    """从数据湖抽取数据并落地到数据集（一站式）。

    这是"数据湖 → 数据集"的标准流程：
    1. 从湖中读取快照数据
    2. 注入血缘追踪字段
    3. 落地为数据集版本的表成员

    Args:
        db: 数据库会话
        lake_id: 数据湖 ID
        source_version: 源头快照版本号
        dataset_id: 目标数据集 ID
        table_name: 表成员名称
        note: 版本说明
        produced_by_job_id: 产出该版本的 job ID

    Returns:
        (DatasetVersion, DatasetVersionTable) 元组
    """
    from app.services.landing import add_table_member

    # 1. 从湖中抽取数据（已注入血缘）
    records = await extract_from_lake_snapshot(db, lake_id, source_version)

    # 2. 获取快照以提取语义信息
    snapshot = await get_snapshot_by_version(db, lake_id, source_version)
    if not snapshot:
        raise ExternalStoreError(f"快照不存在: {lake_id}/{source_version}")

    # 3. 落地为数据集版本成员
    # semantic_type 从 data_category 推断：database → structured
    semantic_type = (
        "structured" if snapshot.data_category == "database" else "unstructured"
    )

    version, member = await add_table_member(
        db,
        dataset_id,
        records,
        table_name=table_name,
        semantic_type=semantic_type,
        source_format=snapshot.storage_format,
        note=note,
        produced_by_job_id=produced_by_job_id,
        storage_format="parquet",
    )

    return version, member
