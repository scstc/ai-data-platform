"""数据入湖服务 - 实现数据湖 ODS 层的核心逻辑。

数据湖职责：
1. 原样接入：所有外部数据源数据原样存储，不加工
2. 版本固化：每次接入产生不可变的 source_v 快照
3. 血缘追踪：记录完整的数据源元信息
4. 格式归档：结构化数据→Parquet，文档/多媒体→原格式
"""

from __future__ import annotations

import asyncio
import io
import re
from datetime import UTC, datetime
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from minio.error import S3Error
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.ids import uuid7_hex
from app.models.data_lake import DataLake, DataLakeSnapshot
from app.services.external_store import ExternalStoreError, client_for


def generate_source_version(
    date: datetime | None = None, batch: int = 1, source_type: str = "default"
) -> str:
    """生成源头快照版本号：source_v年月日_批次_类型。

    Args:
        date: 快照日期，默认当前日期
        batch: 当天批次序号，默认 01
        source_type: 数据源类型（mysql/pg/s3/hdfs/local等）

    Returns:
        如 source_v20260701_01_mysql
    """
    if date is None:
        date = datetime.now(UTC)
    date_str = date.strftime("%Y%m%d")
    return f"source_v{date_str}_{batch:02d}_{source_type}"


# 匹配 source_v{yyyymmdd}_{batch}_{source_type} 中的 batch 段(2+ 位数字,兼容
# 未来溢出到 3 位的批次)
_BATCH_RE = re.compile(r"^source_v\d{8}_(\d+)_")


async def _next_batch_no(
    db: AsyncSession, lake_id: str, date: datetime, source_type: str
) -> int:
    """算某湖当天某 source_type 下一个可用批次号(1 起)。

    通过前缀 LIKE 匹配当天所有同 source_type 的快照,解析批次段取 max + 1;
    无匹配返回 1。**并发竞态**:两个并发调用可能算到同一批次,依赖
    `uq_lake_source_version` 唯一约束兜底(见 `_ingest_with_retry`)。
    """
    date_str = date.strftime("%Y%m%d")
    prefix = f"source_v{date_str}_"
    suffix = f"_{source_type}"
    result = await db.execute(
        select(DataLakeSnapshot.source_version).where(
            DataLakeSnapshot.lake_id == lake_id,
            DataLakeSnapshot.source_version.like(f"{prefix}%{suffix}"),
        )
    )
    versions = [row[0] for row in result.all()]
    max_batch = 0
    for v in versions:
        m = _BATCH_RE.match(v)
        if m:
            try:
                max_batch = max(max_batch, int(m.group(1)))
            except ValueError:
                continue
    return max_batch + 1


async def create_data_lake(
    db: AsyncSession,
    *,
    name: str,
    description: str | None = None,
    owner: str = "admin",
    creator: str = "admin",
    dept_id: str | None = None,
) -> DataLake:
    """创建数据湖容器（多源汇聚，不绑定类型）。

    Args:
        db: 数据库会话
        name: 数据湖名称
        description: 描述
        owner: 所有者
        creator: 创建人
        dept_id: 所属部门

    Returns:
        创建的数据湖对象
    """
    # UUIDv7:前 48 bit 是毫秒时间戳,字典序 = 创建顺序;跟随 dset- 约定用完整
    # 32 位 hex,避免 6 位截断在快照量大时冲撞(datasource/task/job 等旧资源
    # 沿用 token_hex(3) 是历史遗留,数据湖是与 dataset 同链路的新表,统一走完整 UUIDv7)。
    lake_id = f"lake-{uuid7_hex()}"
    lake = DataLake(
        id=lake_id,
        name=name,
        description=description,
        owner=owner,
        creator=creator,
        dept_id=dept_id,
    )
    db.add(lake)
    await db.commit()
    await db.refresh(lake)
    return lake


async def _put_object_to_lake_minio(
    object_key: str, data: bytes, content_type: str = "application/octet-stream"
) -> None:
    """上传对象到数据湖专用 MinIO 桶（异步包装）。

    数据湖使用独立桶 `storage_minio_lake_bucket`(默认 adp-data-lake),
    与数据集加工产物桶 `storage_minio_upload_bucket` 物理隔离:
    - 湖桶:ODS 原始归档,不可变,长期保留,单独生命周期/配额/备份
    - 加工桶:数据集产物,可回收

    Args:
        object_key: 对象键（不含 bucket）
        data: 文件内容（字节）
        content_type: MIME 类型

    Raises:
        ExternalStoreError: MinIO 未配置或上传失败
    """
    if not all(
        [
            settings.storage_minio_endpoint,
            settings.storage_minio_access_key,
            settings.storage_minio_secret_key,
        ]
    ):
        raise ExternalStoreError("平台 MinIO 未配置")

    config = {
        "endpoint": settings.storage_minio_endpoint,
        "accessKey": settings.storage_minio_access_key,
        "secretKey": settings.storage_minio_secret_key,
    }
    client = client_for(config)
    bucket = settings.storage_minio_lake_bucket

    # 确保桶存在(启动期 ensure_lake_bucket 已建过,此处兜底幂等)
    def _ensure_bucket():
        if not client.bucket_exists(bucket):
            client.make_bucket(bucket)

    await asyncio.to_thread(_ensure_bucket)

    # 上传对象
    def _put():
        client.put_object(
            bucket,
            object_key,
            io.BytesIO(data),
            length=len(data),
            content_type=content_type,
        )

    try:
        await asyncio.to_thread(_put)
    except S3Error as exc:
        raise ExternalStoreError(f"上传到 MinIO 失败: {exc}") from exc


async def ingest_to_lake_parquet(
    db: AsyncSession,
    *,
    lake_id: str,
    data: list[dict],
    source_type: str,
    source_metadata: dict | None = None,
    upload_channel: str = "database",
    data_category: str = "database",
    datasource_id: str | None = None,
    ingest_task_id: str | None = None,
) -> DataLakeSnapshot:
    """将结构化数据以 Parquet 格式入湖。

    批次号从数据库现有快照自增(`_next_batch_no`),同湖同天同 source_type 多次
    入湖会得到 batch=1,2,3...;并发时依赖 `uq_lake_source_version` 唯一约束
    兜底 + 最多 3 次重试。

    Args:
        db: 数据库会话
        lake_id: 数据湖 ID
        data: 结构化数据（list of dict）
        source_type: 数据源类型（mysql/pg/oceanbase等,决定 source_version 尾缀)
        source_metadata: 源头元数据（db_schema/db_table/db_engine 等）
        upload_channel: 上传渠道（database/oss/obs/minio/api/local）
        data_category: 数据类型标签(默认 "database" 兼容旧调用;本地上传结构化
            文件传 "tabular",与 PG 拉的 DB 数据区分统计口径)
        ingest_task_id: 关联的采集任务 ID

    Returns:
        创建的数据湖快照对象
    """
    # 1. 转换为 Parquet(可复用,不含 source_version)
    df = pd.DataFrame(data)
    table = pa.Table.from_pandas(df)
    parquet_buffer = io.BytesIO()
    pq.write_table(table, parquet_buffer)
    parquet_buffer.seek(0)
    parquet_bytes = parquet_buffer.getvalue()

    now = datetime.now(UTC)

    async def _build_snapshot(batch_no: int) -> DataLakeSnapshot:
        source_version = generate_source_version(
            date=now, batch=batch_no, source_type=source_type
        )
        object_key = f"data-lake/{lake_id}/{source_version}/data.parquet"
        await _put_object_to_lake_minio(
            object_key=object_key,
            data=parquet_bytes,
            content_type="application/octet-stream",
        )
        bucket = settings.storage_minio_lake_bucket
        return DataLakeSnapshot(
            id=f"snap-{uuid7_hex()}",
            lake_id=lake_id,
            source_version=source_version,
            storage_uri=f"s3://{bucket}/{object_key}",
            storage_format="parquet",
            data_category=data_category,
            upload_channel=upload_channel,
            datasource_id=datasource_id,
            source_metadata=source_metadata,
            rows=len(data),
            size=len(parquet_bytes),
            ingest_task_id=ingest_task_id,
        )

    return await _persist_snapshot_with_retry(
        db, lake_id=lake_id, date=now, source_type=source_type, build=_build_snapshot
    )


async def _persist_snapshot_with_retry(
    db: AsyncSession,
    *,
    lake_id: str,
    date: datetime,
    source_type: str,
    build,  # Callable[[int], Awaitable[DataLakeSnapshot]]
    max_retries: int = 3,
) -> DataLakeSnapshot:
    """批次号自增 + 唯一约束冲突重试。

    并发场景下两次调用可能算到同一 batch_no,DB 层 `uq_lake_source_version`
    会挡下第二次插入(IntegrityError);捕获后回滚重算下一批次,最多重试 3 次。
    """
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        batch_no = await _next_batch_no(db, lake_id, date, source_type)
        snapshot = await build(batch_no)
        db.add(snapshot)
        try:
            await db.commit()
            await db.refresh(snapshot)
            return snapshot
        except IntegrityError as exc:
            await db.rollback()
            last_exc = exc
            if attempt == max_retries - 1:
                break
    raise ExternalStoreError(
        f"数据湖 {lake_id} 批次号冲突,重试 {max_retries} 次仍失败,请稍后重试"
    ) from last_exc


async def ingest_to_lake_raw(
    db: AsyncSession,
    *,
    lake_id: str,
    file_content: bytes,
    original_filename: str,
    data_category: str,
    upload_channel: str = "local",
    datasource_id: str | None = None,
    source_metadata: dict | None = None,
    ingest_task_id: str | None = None,
) -> DataLakeSnapshot:
    """将文档/多媒体文件原格式入湖。

    批次号自增语义同 `ingest_to_lake_parquet`——source_type 用文件扩展名,
    同湖同天同扩展名多次入湖批次递增。

    Args:
        db: 数据库会话
        lake_id: 数据湖 ID
        file_content: 文件原始内容（字节）
        original_filename: 原始文件名
        data_category: 数据类型（document/image/audio/video/text）
        upload_channel: 上传渠道（oss/obs/minio/api/local）
        source_metadata: 源头元数据（bucket_name/obj_key/original_filename等）
        ingest_task_id: 关联的采集任务 ID

    Returns:
        创建的数据湖快照对象
    """
    file_ext = Path(original_filename).suffix.lstrip(".").lower() or "bin"

    # 补充 source_metadata:确保 original_filename 一定在里面
    metadata = dict(source_metadata or {})
    metadata["original_filename"] = original_filename

    now = datetime.now(UTC)

    async def _build_snapshot(batch_no: int) -> DataLakeSnapshot:
        source_version = generate_source_version(
            date=now, batch=batch_no, source_type=file_ext
        )
        object_key = f"data-lake/{lake_id}/{source_version}/{original_filename}"
        await _put_object_to_lake_minio(
            object_key=object_key,
            data=file_content,
            content_type="application/octet-stream",
        )
        bucket = settings.storage_minio_lake_bucket
        return DataLakeSnapshot(
            id=f"snap-{uuid7_hex()}",
            lake_id=lake_id,
            source_version=source_version,
            storage_uri=f"s3://{bucket}/{object_key}",
            storage_format=file_ext,
            data_category=data_category,
            upload_channel=upload_channel,
            datasource_id=datasource_id,
            source_metadata=metadata,
            rows=None,  # 非结构化数据无行数
            size=len(file_content),
            ingest_task_id=ingest_task_id,
        )

    return await _persist_snapshot_with_retry(
        db, lake_id=lake_id, date=now, source_type=file_ext, build=_build_snapshot
    )


async def get_lake_by_id(db: AsyncSession, lake_id: str) -> DataLake | None:
    """根据 ID 获取数据湖。"""
    result = await db.execute(select(DataLake).where(DataLake.id == lake_id))
    return result.scalar_one_or_none()


async def list_lake_snapshots(
    db: AsyncSession, lake_id: str, limit: int = 100
) -> list[DataLakeSnapshot]:
    """列出数据湖的所有快照（按创建时间倒序）。"""
    result = await db.execute(
        select(DataLakeSnapshot)
        .where(DataLakeSnapshot.lake_id == lake_id)
        .order_by(DataLakeSnapshot.created_at.desc())
        .limit(limit)
    )
    return list(result.scalars().all())


async def get_snapshot_by_version(
    db: AsyncSession, lake_id: str, source_version: str
) -> DataLakeSnapshot | None:
    """根据版本号获取快照。"""
    result = await db.execute(
        select(DataLakeSnapshot)
        .where(DataLakeSnapshot.lake_id == lake_id)
        .where(DataLakeSnapshot.source_version == source_version)
    )
    return result.scalar_one_or_none()
