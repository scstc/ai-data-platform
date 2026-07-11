"""数据入湖服务 - 实现数据湖 ODS 层的核心逻辑。

数据湖职责：
1. 原样接入：所有外部数据源数据原样存储，不加工
2. 版本固化：每次接入产生不可变的 source_v 快照
3. 血缘追踪：记录完整的数据源元信息
4. 格式归档：结构化数据→Parquet，文档/多媒体→原格式
"""

from __future__ import annotations

import asyncio
import hashlib
import io
import logging
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from minio.error import S3Error
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.ids import uuid7_hex
from app.models.data_lake import DataLake, DataLakeObject, DataLakeSnapshot
from app.services.external_store import ExternalStoreError, client_for, parse_s3_uri

logger = logging.getLogger(__name__)


def derive_identity_key(
    datasource_id: str | None,
    source_metadata: dict | None,
    original_filename: str | None = None,
) -> tuple[str, str]:
    """按来源推导文件身份键与展示名，用于同一文件多次入湖时找到已有 DataLakeObject。

    规则（回填迁移 `0044_lake_objects` 内拷贝了同一份规则，改动需同步）：
    1. `source_metadata.db_table` 存在 → 数据库表：
       key=f"{datasource_id or 'ds'}:{db_table}"，display=db_table
    2. `source_metadata.obj_key` 存在 → 对象存储/HDFS 单文件：
       key=f"{datasource_id or 'ds'}:{bucket_name}/{obj_key}"，
       display=obj_key 的最后一段（basename）
    3. `source_metadata.hdfs_path` 存在 → HDFS：
       key=f"{datasource_id or 'ds'}:{hdfs_path}"，display=路径最后一段
    4. 否则（本地上传/API 推送）→
       key=f"local:{original_filename}"，display=同名

    湖内合并产出的文件不走本函数，身份键固定为 `merged:{用户命名}`。

    Returns:
        (identity_key, display_name)
    """
    meta = source_metadata or {}
    ds = datasource_id or "ds"

    db_table = meta.get("db_table")
    if db_table:
        return f"{ds}:{db_table}", db_table

    obj_key = meta.get("obj_key")
    if obj_key:
        bucket_name = meta.get("bucket_name") or ""
        display = obj_key.rsplit("/", 1)[-1]
        return f"{ds}:{bucket_name}/{obj_key}", display

    hdfs_path = meta.get("hdfs_path")
    if hdfs_path:
        display = hdfs_path.rsplit("/", 1)[-1]
        return f"{ds}:{hdfs_path}", display

    name = original_filename or meta.get("original_filename")
    return f"local:{name}", name or ""


# identity_key 中非此集合的字符替换为下划线,再做长度截断,拼进 MinIO 对象键
_UNSAFE_IDENTITY_CHARS_RE = re.compile(r"[^A-Za-z0-9._-]")


def _safe_identity_path(identity_key: str) -> str:
    """把 identity_key 转成可安全拼进对象存储路径的片段。

    非 `[A-Za-z0-9._-]` 字符替换为 `_`;超过 100 字符时截断并加 8 位内容
    hash 后缀,避免不同 identity_key 截断后碰撞。
    """
    safe = _UNSAFE_IDENTITY_CHARS_RE.sub("_", identity_key)
    if len(safe) > 100:
        suffix = hashlib.md5(identity_key.encode("utf-8")).hexdigest()[:8]
        safe = f"{safe[:100]}_{suffix}"
    return safe


async def _resolve_object(
    db: AsyncSession,
    *,
    lake_id: str,
    identity_key: str,
    display_name: str,
    data_category: str,
    storage_format: str | None = None,
    origin: str = "ingested",
) -> DataLakeObject:
    """按 `(lake_id, identity_key)` 查找已有文件身份行,不存在则新建。

    并发下两次调用都可能读到"不存在"而尝试各自插入,`uq_lake_object_identity`
    挡下后到者(IntegrityError);捕获后回滚重查一次——此时应能查到先到者插入的
    行,查不到说明另有其它异常,原样抛出。
    """
    result = await db.execute(
        select(DataLakeObject).where(
            DataLakeObject.lake_id == lake_id,
            DataLakeObject.identity_key == identity_key,
        )
    )
    obj = result.scalar_one_or_none()
    if obj is not None:
        return obj

    obj = DataLakeObject(
        lake_id=lake_id,
        identity_key=identity_key,
        display_name=display_name,
        origin=origin,
        data_category=data_category,
        storage_format=storage_format,
    )
    db.add(obj)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        result = await db.execute(
            select(DataLakeObject).where(
                DataLakeObject.lake_id == lake_id,
                DataLakeObject.identity_key == identity_key,
            )
        )
        obj = result.scalar_one_or_none()
        if obj is None:
            raise
        # 并发下他人已抢先插入同 identity_key 的文件身份行,唯一约束挡下本次插入;
        # 回滚后复用已有对象。明示而非静默,便于排查并发入湖的身份归并行为。
        logger.info(
            "并发入湖:identity_key=%s 已由其他请求创建 DataLakeObject(id=%s),复用之",
            identity_key,
            obj.id,
        )
        return obj
    await db.refresh(obj)
    return obj


async def _next_object_version_no(db: AsyncSession, object_id: str) -> int:
    """算文件下一个可用版本号(1 起)。取该 object_id 下已有快照的 max(version_no)+1。"""
    result = await db.execute(
        select(func.max(DataLakeSnapshot.version_no)).where(
            DataLakeSnapshot.object_id == object_id
        )
    )
    max_no = result.scalar_one_or_none()
    return (max_no or 0) + 1


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
    与数据集加工产物桶 `storage_minio_datasets_bucket` 物理隔离:
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


async def _remove_object_from_lake_minio(object_key: str) -> None:
    """删除数据湖桶内单个对象(孤儿回收用,§10)。

    与 ``_put_object_to_lake_minio`` 对称,面向同一湖桶;MinIO 未配置或删除失败
    抛 ``ExternalStoreError``,由调用方 best-effort 吞掉(孤儿回收失败不应污染
    上层错误)。
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
    try:
        await asyncio.to_thread(client.remove_object, bucket, object_key)
    except S3Error as exc:
        raise ExternalStoreError(f"删除 MinIO 对象失败: {exc}") from exc


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
    job_id: str | None = None,
) -> DataLakeSnapshot:
    """将结构化数据以 Parquet 格式入湖。

    入湖前按 `derive_identity_key` 解析身份键找/建 `DataLakeObject`(同一张表/
    文件多次入湖落到同一对象,版本号在对象维度自增);`source_version` 展示标签
    仍按湖维度的批次号生成(`_next_batch_no`),两套编号互不影响,冲突各自由
    `_persist_snapshot_with_retry` 重试。

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
        job_id: 产出该版本的任务运行 ID(手动上传/无任务上下文可为空)

    Returns:
        创建的数据湖快照对象
    """
    # 1. 转换为 Parquet(可复用,不含 source_version/存储路径)
    # §2 缺陷修复:直接 pyarrow.Table.from_pylist,不经 pandas。
    # 经 pandas 会把含 NULL 的整数列升为 float64,大整数(> 2^53)丢精度;
    # 且 DataFrame→Table 多一份内存拷贝。from_pylist 逐列推断类型,NULL 保持
    # 为 null、整数保持 int64,精度与内存都更优。
    table = pa.Table.from_pylist(data)
    parquet_buffer = io.BytesIO()
    pq.write_table(table, parquet_buffer)
    parquet_bytes = parquet_buffer.getvalue()

    # 2. 解析文件身份,找/建 DataLakeObject
    identity_key, display_name = derive_identity_key(datasource_id, source_metadata)
    obj = await _resolve_object(
        db,
        lake_id=lake_id,
        identity_key=identity_key,
        display_name=display_name,
        data_category=data_category,
        storage_format="parquet",
    )
    safe_identity = _safe_identity_path(identity_key)

    now = datetime.now(UTC)

    async def _build_snapshot(batch_no: int, version_no: int) -> DataLakeSnapshot:
        source_version = generate_source_version(
            date=now, batch=batch_no, source_type=source_type
        )
        object_key = f"data-lake/{lake_id}/{safe_identity}/v{version_no}/data.parquet"
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
            object_id=obj.id,
            version_no=version_no,
            job_id=job_id,
        )

    return await _persist_snapshot_with_retry(
        db,
        lake_id=lake_id,
        date=now,
        source_type=source_type,
        obj=obj,
        build=_build_snapshot,
    )


async def _persist_snapshot_with_retry(
    db: AsyncSession,
    *,
    lake_id: str,
    date: datetime,
    source_type: str,
    obj: DataLakeObject,
    build,  # Callable[[int, int], Awaitable[DataLakeSnapshot]] (batch_no, version_no)
    max_retries: int = 3,
) -> DataLakeSnapshot:
    """批次号(展示标签)与文件版本号(判重主键)各自自增 + 唯一约束冲突重试。

    两套编号服务不同的唯一约束:`source_version` 服务旧 `uq_lake_source_version`
    (兼容展示标签),`(object_id, version_no)` 服务新 `uq_lake_object_version`
    (判重主键)。并发下任一冲突都会在同一提交里报 IntegrityError;回滚后两个号
    都重算再试,最多重试 3 次。成功后把 `obj.latest_version_no` /
    `latest_snapshot_id` / `data_category` / `storage_format` 推进到本次快照。
    """
    last_exc: Exception | None = None
    # §10:每次尝试的 build() 会先把 parquet/原文件写进湖桶(commit 之前);冲突回滚后
    # 该次 version_no 对应的对象可能成为孤儿。记录本次写入的 storage_uri,最终失败时
    # 尽力回收——但仅回收「无任何已提交快照引用」的对象,避免误删并发赢家的同键对象。
    written_uris: list[str] = []
    for attempt in range(max_retries):
        batch_no = await _next_batch_no(db, lake_id, date, source_type)
        version_no = await _next_object_version_no(db, obj.id)
        snapshot = await build(batch_no, version_no)
        written_uris.append(snapshot.storage_uri)
        db.add(snapshot)
        obj.latest_version_no = version_no
        obj.latest_snapshot_id = snapshot.id
        obj.data_category = snapshot.data_category
        obj.storage_format = snapshot.storage_format
        try:
            await db.commit()
            await db.refresh(snapshot)
            return snapshot
        except IntegrityError as exc:
            await db.rollback()
            last_exc = exc
            if attempt == max_retries - 1:
                break
    # 重试耗尽:本请求未能提交任何快照。回收本请求写入的、无快照引用的孤儿对象。
    await _gc_orphan_lake_objects(db, written_uris)
    raise ExternalStoreError(
        f"数据湖 {lake_id} 版本号冲突,重试 {max_retries} 次仍失败,请稍后重试"
    ) from last_exc


async def _gc_orphan_lake_objects(db: AsyncSession, uris: list[str]) -> None:
    """best-effort 回收孤儿湖对象(§10):仅删无任何已提交快照引用的对象。

    version_no 冲突常源于并发对同一文件入湖,双方算到同一 ``version_no`` → 写到
    同一 object_key。此时赢家的已提交快照仍引用该对象,绝不能删(否则丢赢家数据)。
    故逐个校验:``storage_uri`` 无 DataLakeSnapshot 引用才删。删除失败仅记日志,
    不向上抛(回收失败不应改变调用方要抛的版本冲突错误)。
    """
    for uri in uris:
        referenced = await db.scalar(
            select(func.count())
            .select_from(DataLakeSnapshot)
            .where(DataLakeSnapshot.storage_uri == uri)
        )
        if referenced:
            continue
        try:
            _bucket, key = parse_s3_uri(uri)
            await _remove_object_from_lake_minio(key)
            logger.info("回收孤儿湖对象(版本冲突重试失败):%s", uri)
        except (ExternalStoreError, ValueError) as exc:
            logger.warning("回收孤儿湖对象失败(已忽略):%s(%s)", uri, exc)


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
    job_id: str | None = None,
) -> DataLakeSnapshot:
    """将文档/多媒体文件原格式入湖。

    身份解析/版本自增语义同 `ingest_to_lake_parquet`;`source_version` 展示标签
    的 source_type 段仍用文件扩展名。

    Args:
        db: 数据库会话
        lake_id: 数据湖 ID
        file_content: 文件原始内容（字节）
        original_filename: 原始文件名
        data_category: 数据类型（document/image/audio/video/text）
        upload_channel: 上传渠道（oss/obs/minio/api/local）
        source_metadata: 源头元数据（bucket_name/obj_key/original_filename等）
        ingest_task_id: 关联的采集任务 ID
        job_id: 产出该版本的任务运行 ID(手动上传/无任务上下文可为空)

    Returns:
        创建的数据湖快照对象
    """
    file_ext = Path(original_filename).suffix.lstrip(".").lower() or "bin"

    # 补充 source_metadata:确保 original_filename 一定在里面
    metadata = dict(source_metadata or {})
    metadata["original_filename"] = original_filename

    # 解析文件身份,找/建 DataLakeObject
    identity_key, display_name = derive_identity_key(
        datasource_id, metadata, original_filename=original_filename
    )
    obj = await _resolve_object(
        db,
        lake_id=lake_id,
        identity_key=identity_key,
        display_name=display_name,
        data_category=data_category,
        storage_format=file_ext,
    )
    safe_identity = _safe_identity_path(identity_key)

    now = datetime.now(UTC)

    async def _build_snapshot(batch_no: int, version_no: int) -> DataLakeSnapshot:
        source_version = generate_source_version(
            date=now, batch=batch_no, source_type=file_ext
        )
        object_key = (
            f"data-lake/{lake_id}/{safe_identity}/v{version_no}/{original_filename}"
        )
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
            object_id=obj.id,
            version_no=version_no,
            job_id=job_id,
        )

    return await _persist_snapshot_with_retry(
        db,
        lake_id=lake_id,
        date=now,
        source_type=file_ext,
        obj=obj,
        build=_build_snapshot,
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


async def _read_parquet_df(snapshot: DataLakeSnapshot) -> pd.DataFrame:
    """读回快照的 Parquet 内容为 DataFrame,供湖内合并读取各输入文件用。

    读法与 `lake_extract._read_parquet_from_snapshot` 一致(下载字节 +
    `pq.read_table`),本模块内单独实现一份,避免 `lake_extract` 反向 import
    本模块造成循环引用(`lake_extract` 已经 import 本模块的 `get_snapshot_by_version`)。
    """
    bucket, key = parse_s3_uri(snapshot.storage_uri)
    config = {
        "endpoint": settings.storage_minio_endpoint,
        "accessKey": settings.storage_minio_access_key,
        "secretKey": settings.storage_minio_secret_key,
    }
    client = client_for(config)

    def _get():
        response = client.get_object(bucket, key)
        return response.read()

    try:
        parquet_bytes = await asyncio.to_thread(_get)
    except S3Error as exc:
        raise ExternalStoreError(f"读取快照文件失败: {exc}") from exc

    return pq.read_table(io.BytesIO(parquet_bytes)).to_pandas()


async def merge_lake_objects(
    db: AsyncSession,
    *,
    lake_id: str,
    mode: str,
    inputs: list[dict[str, Any]],
    join_keys: list[str] | None = None,
    name: str | None = None,
    target_object_id: str | None = None,
    creator: str = "admin",
) -> DataLakeSnapshot:
    """湖内合并:多个结构化文件的某个版本 union/join 成一个宽表文件的新版本。

    只增不改:产出 merged 文件的新版本,参与合并的源文件及其历史版本不动。
    重新合并可追加到已有 merged 文件(`target_object_id`,版本+1)或另存为新文件
    (`name`),二者二选一,交互对齐"湖抽取生成数据集"的新建/追加(commit 3ff100b)。

    Args:
        db: 数据库会话
        lake_id: 所属数据湖
        mode: "union"(纵向拼接,列并集缺失补 null)| "join"(按 join_keys 关联,
            为保守保留数据用 how="outer",而非可能丢行的 inner/left)
        inputs: `[{"object_id": ..., "snapshot_id": 可选}]`,snapshot_id 缺省
            取该对象的 latest_snapshot_id
        join_keys: mode="join" 时必填,各输入都须含这些列
        name: 新建 merged 文件时的展示名(与 target_object_id 二选一)
        target_object_id: 追加到已有 merged 文件时的对象 id(与 name 二选一)
        creator: 操作人(当前 DataLakeObject 无 creator 列,预留形参供未来扩展)

    Returns:
        新产出的合并快照

    Raises:
        ExternalStoreError: 输入少于 2 个 / 不支持的 mode / join 缺 join_keys
            或输入缺列 / name 与 target_object_id 未二选一 / 输入不存在或跨湖
            或非 parquet / 目标文件不存在或不是 merged 类型
    """
    if len(inputs) < 2:
        raise ExternalStoreError("湖内合并至少需要选择 2 个文件")
    if mode not in ("union", "join"):
        raise ExternalStoreError(f"不支持的合并方式: {mode}")
    if mode == "join" and not join_keys:
        raise ExternalStoreError("join 合并必须指定 join_keys")
    if bool(name) == bool(target_object_id):
        raise ExternalStoreError("name 与 target_object_id 必须二选一")

    # 1. 解析各输入的对象,校验都属于该湖
    object_ids = [item["object_id"] for item in inputs]
    result = await db.execute(
        select(DataLakeObject).where(DataLakeObject.id.in_(object_ids))
    )
    objects_by_id = {o.id: o for o in result.scalars().all()}
    missing = [oid for oid in object_ids if oid not in objects_by_id]
    if missing:
        raise ExternalStoreError(f"以下文件不存在: {missing}")
    stray = [oid for oid, o in objects_by_id.items() if o.lake_id != lake_id]
    if stray:
        raise ExternalStoreError(f"以下文件不属于 {lake_id}: {stray}")

    # 2. 解析各输入的快照(缺省取对象最新版本),校验都为 parquet
    resolved_snapshot_ids: list[str] = []
    for item in inputs:
        target = objects_by_id[item["object_id"]]
        sid = item.get("snapshot_id") or target.latest_snapshot_id
        if not sid:
            raise ExternalStoreError(f"文件 {item['object_id']} 尚无可用版本")
        resolved_snapshot_ids.append(sid)

    result = await db.execute(
        select(DataLakeSnapshot).where(DataLakeSnapshot.id.in_(resolved_snapshot_ids))
    )
    snapshots_by_id = {s.id: s for s in result.scalars().all()}
    missing_snap = [sid for sid in resolved_snapshot_ids if sid not in snapshots_by_id]
    if missing_snap:
        raise ExternalStoreError(f"以下版本不存在: {missing_snap}")

    ordered_snapshots = [snapshots_by_id[sid] for sid in resolved_snapshot_ids]
    non_parquet = [s.id for s in ordered_snapshots if s.storage_format != "parquet"]
    if non_parquet:
        raise ExternalStoreError(
            f"仅支持合并结构化(parquet)文件,以下版本不符合: {non_parquet}"
        )

    # 3. 读回各输入为 DataFrame,按 mode 合并
    dfs = [await _read_parquet_df(s) for s in ordered_snapshots]

    if mode == "join":
        bad = [
            (oid, missing_cols)
            for oid, df in zip(object_ids, dfs, strict=True)
            if (missing_cols := [k for k in join_keys if k not in df.columns])
        ]
        if bad:
            raise ExternalStoreError(f"以下文件缺少 join_keys 列: {bad}")
        merged_df = dfs[0]
        for df in dfs[1:]:
            merged_df = merged_df.merge(df, on=join_keys, how="outer")
    else:
        merged_df = pd.concat(dfs, ignore_index=True, sort=False)
        merged_df = merged_df.where(pd.notna(merged_df), None)

    # 4. 解析/新建 merged 对象
    if target_object_id:
        result = await db.execute(
            select(DataLakeObject).where(DataLakeObject.id == target_object_id)
        )
        target_obj = result.scalar_one_or_none()
        if target_obj is None or target_obj.lake_id != lake_id:
            raise ExternalStoreError(f"目标合并文件不存在: {target_object_id}")
        if target_obj.origin != "merged":
            raise ExternalStoreError(f"目标文件不是合并文件: {target_object_id}")
        obj = target_obj
    else:
        identity_key = f"merged:{name}"
        obj = await _resolve_object(
            db,
            lake_id=lake_id,
            identity_key=identity_key,
            display_name=name or "",
            data_category="database",
            storage_format="parquet",
            origin="merged",
        )

    merge_config = {
        "mode": mode,
        "join_keys": join_keys,
        "inputs": [{"object_id": oid} for oid in object_ids],
    }

    # 5. 落 Parquet + 快照(版本号在 merged 对象维度自增,与源文件版本互不影响)
    table = pa.Table.from_pandas(merged_df)
    parquet_buffer = io.BytesIO()
    pq.write_table(table, parquet_buffer)
    parquet_buffer.seek(0)
    parquet_bytes = parquet_buffer.getvalue()

    safe_identity = _safe_identity_path(obj.identity_key)
    merge_inputs = [
        {
            "object_id": s.object_id,
            "snapshot_id": s.id,
            "version_no": s.version_no,
        }
        for s in ordered_snapshots
    ]
    now = datetime.now(UTC)

    async def _build_snapshot(batch_no: int, version_no: int) -> DataLakeSnapshot:
        # 与 obj.latest_* 一样属于"需要在每次重试提交前重新生效"的对象层改动:
        # 撞 uq_lake_object_version 回滚后, 上一次未提交的 merge_config 赋值
        # 会被 rollback 丢弃, 必须在重试循环的每次构建里重新赋值(而不是在
        # 调用 _persist_snapshot_with_retry 之前赋一次)。
        obj.merge_config = merge_config
        object_key = f"data-lake/{lake_id}/{safe_identity}/v{version_no}/data.parquet"
        await _put_object_to_lake_minio(
            object_key=object_key,
            data=parquet_bytes,
            content_type="application/octet-stream",
        )
        bucket = settings.storage_minio_lake_bucket
        source_version = generate_source_version(
            date=now, batch=batch_no, source_type="merge"
        )
        return DataLakeSnapshot(
            id=f"snap-{uuid7_hex()}",
            lake_id=lake_id,
            source_version=source_version,
            storage_uri=f"s3://{bucket}/{object_key}",
            storage_format="parquet",
            data_category="database",
            upload_channel="merge",
            datasource_id=None,
            source_metadata={"merge_mode": mode, "input_count": len(inputs)},
            rows=len(merged_df),
            size=len(parquet_bytes),
            object_id=obj.id,
            version_no=version_no,
            merge_inputs=merge_inputs,
        )

    return await _persist_snapshot_with_retry(
        db,
        lake_id=lake_id,
        date=now,
        source_type="merge",
        obj=obj,
        build=_build_snapshot,
    )
