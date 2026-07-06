"""数据湖抽取层 - 从数据湖读取原始数据并转换为数据集。

第二层职责：
- 从数据湖中按需筛选、抽取指定版本的原始数据
- 完成格式解析与初步结构化（Parquet → 记录、csv/xlsx/jsonl → 解析为记录）
- 注入 DJ 标准 meta 元数据（src/date/version 三元组，嵌套在 "meta" 键下）
- 生成中间 JSONL 供数据集落地
"""

from __future__ import annotations

import asyncio
import io
import logging
from typing import Any

import pyarrow.parquet as pq
from minio.error import S3Error
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.data_lake import DataLakeSnapshot
from app.models.dataset import Dataset
from app.services.data_lake import get_snapshot_by_version
from app.services.external_store import ExternalStoreError, client_for, parse_s3_uri
from app.services.landing import (
    BINARY_FORMATS,
    LANDABLE_FORMATS,
    DocSegmentOptions,
    LandingError,
    land_media_manifest,
    media_kind,
    normalize_to_records,
)

_logger = logging.getLogger(__name__)

# 元数据统一嵌套在记录的 "meta" 键下(DJ 标准格式,如 Arxiv 数据集的
# {"text": ..., "meta": {"src": ..., "date": ..., "version": ...}}),由
# _inject_lineage_fields 注入(仅 src/date/version 三元组,version 可反查
# 快照追溯血缘)。字段映射裁列时只保留 text + meta,丢弃原始源列。
_KEPT_KEYS = frozenset({"text", "meta"})


async def _apply_field_mapping_transform(
    records: list[dict[str, Any]], template: str
) -> list[dict[str, Any]]:
    """对记录应用字段映射模板,生成 text 字段并裁列为 text + meta。

    Args:
        records: 原始记录列表(已注入 meta 元数据)
        template: 映射模板(如 "用户提问：{question}，客服回答：{answer}")

    Returns:
        裁列后的记录列表:只含 text(算子生成)+ meta(src/date/version),
        丢弃 question/answer/category 等原始源列。

    Raises:
        ExternalStoreError: 算子执行失败

    适用于表格类数据(database/tabular):数据库表、CSV、TSV、Excel、JSONL 等。
    """
    if not records or not template.strip():
        return records

    import re

    # 构造 lambda_str(与 ingest_tasks._apply_field_mapping 同逻辑)
    fields = re.findall(r"\{(\w+)\}", template)
    safe_template = template
    for field in fields:
        safe_template = safe_template.replace(
            f"{{{field}}}", f'{{row.get("{field}", "")}}'
        )
    lambda_str = f"lambda row: {{**row, 'text': f'{safe_template}'}}"

    operators = [{"name": "python_lambda_mapper", "params": {"lambda_str": lambda_str}}]

    # 调用 engine.filter_records 执行算子
    from app.services.engine import EngineError, filter_records

    try:
        result, _log = await filter_records(
            records, operators, project_name="lake-extract"
        )
    except EngineError as exc:
        raise ExternalStoreError(f"字段映射失败: {exc}") from exc

    # 裁列:只留 text + meta,丢弃原始源列(question/answer 等)。
    # 缺 text 键(理论上算子必产)时保留原记录,不静默吞成空。
    return [
        {k: v for k, v in rec.items() if k in _KEPT_KEYS}
        if "text" in rec
        else rec
        for rec in result
    ]


async def extract_from_lake_snapshot(
    db: AsyncSession,
    lake_id: str,
    source_version: str,
    *,
    inject_lineage: bool = True,
    doc_options: DocSegmentOptions | None = None,
) -> list[dict[str, Any]]:
    """从数据湖快照中抽取数据并注入血缘字段。

    Args:
        db: 数据库会话
        lake_id: 数据湖 ID
        source_version: 源头快照版本号（如 source_v20260701_01_mysql）
        inject_lineage: 是否注入血缘追踪字段
        doc_options: 文档类快照的分段/清洗配置(仅 word/pdf 等文档格式生效)

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
    elif snapshot.storage_format in LANDABLE_FORMATS:
        # 原格式文件(含 pdf/doc/docx/ppt/pptx/html):从 MinIO 读原始字节
        # → normalize_to_records 解析(文档类内部走 markitdown/OCR)
        records = await _read_raw_from_snapshot(snapshot, doc_options=doc_options)
    else:
        raise ExternalStoreError(
            f"不支持的存储格式: {snapshot.storage_format}"
            "(当前支持 parquet 及 landing.LANDABLE_FORMATS 覆盖的文本/文档格式)"
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


async def _read_raw_from_snapshot(
    snapshot: DataLakeSnapshot,
    *,
    doc_options: DocSegmentOptions | None = None,
) -> list[dict[str, Any]]:
    """从快照的原格式文件(csv/xlsx/jsonl 等)中读取并解析为记录。

    数据湖存原格式,抽取时才解析 → records。

    Args:
        snapshot: 数据湖快照
        doc_options: 文档类格式的分段/清洗配置(透传 normalize_to_records)

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
        raw_bytes = await asyncio.to_thread(_get)
    except S3Error as exc:
        raise ExternalStoreError(f"读取快照文件失败: {exc}") from exc

    # 4. 根据扩展名解析(调用 landing.normalize_to_records)
    ext = snapshot.storage_format
    try:
        return normalize_to_records(raw_bytes, ext, doc_options=doc_options)
    except Exception as exc:  # noqa: BLE001 解析失败统一上报
        raise ExternalStoreError(
            f"解析 {ext} 文件失败: {exc}(快照 {snapshot.id})"
        ) from exc


def _lake_file_name(snapshot: DataLakeSnapshot) -> str:
    """取快照在数据湖中的原始文件名(对象键末段),用于命名数据集内的文件/成员。

    数据湖的对象键约定(见 data_lake.py):
    - 原格式文件(本地/API/对象存储上传):
      ``data-lake/{lake_id}/{source_version}/{original_filename}`` → 末段即原文件名
    - 结构化入湖(DB → parquet):
      ``data-lake/{lake_id}/{source_version}/data.parquet`` → 末段恒为 ``data.parquet``,
      无区分度,故对 DB 类改用源表名 ``db_table``(如 orders)命名。

    取名优先级:original_filename(上传原文件名/用户改名)> db_table(DB 类
    源表名)> storage_uri 末段 > source_version(兜底,保证永远有非空名字)。
    DB 采集快照原本没有 original_filename,仅在用户改名后写入——此时改名优先,
    db_table 作为血缘字段保留不动。
    """
    if snapshot.source_metadata:
        for key in ("original_filename", "db_table"):
            name = snapshot.source_metadata.get(key)
            if name:
                return name
    tail = snapshot.storage_uri.rstrip("/").rsplit("/", 1)[-1]
    return tail or snapshot.source_version


async def _download_snapshot_bytes(snapshot: DataLakeSnapshot) -> bytes:
    """从快照的 storage_uri 下载原始字节(供二进制原样落地场景复用)。"""
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
        return await asyncio.to_thread(_get)
    except S3Error as exc:
        raise ExternalStoreError(f"读取快照文件失败: {exc}") from exc


def _inject_lineage_fields(
    records: list[dict[str, Any]], snapshot: DataLakeSnapshot
) -> list[dict[str, Any]]:
    """向记录中注入嵌套 "meta" 元数据（DJ 标准格式）。

    meta 含 DJ 标准三元组（对齐 data-juicer 样例数据集的 {"src", "date", "version"}）
    加一个精确血缘键：
    - src: 数据来源（原文件名/源表名，见 _lake_file_name）
    - date: 入湖日期（快照 created_at，YYYY-MM-DD）
    - version: 源头快照版本号（source_version，仅湖内唯一，兼容展示）
    - snapshot_id: 快照主键（全局唯一，消除 source_version 跨湖歧义；
      经 snapshot.object_id/version_no 可定位"哪个湖文件的哪一版"）
    """
    meta: dict[str, Any] = {
        "src": _lake_file_name(snapshot),
        "date": (
            snapshot.created_at.strftime("%Y-%m-%d") if snapshot.created_at else None
        ),
        "version": snapshot.source_version,
        "snapshot_id": snapshot.id,
    }

    # 每条记录持有独立的 meta 副本，避免共享引用被下游误改
    return [{**record, "meta": dict(meta)} for record in records]


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
    """从数据湖抽取数据并落地到已有数据集（一站式）。

    这是"数据湖 → 数据集"的标准流程：
    1. 从湖中读取快照数据
    2. 注入血缘追踪字段
    3. 落地为数据集版本的表成员

    Args:
        db: 数据库会话
        lake_id: 数据湖 ID
        source_version: 源头快照版本号
        dataset_id: 目标数据集 ID(需已存在)
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
        storage_format="jsonl",
        source_snapshot_id=snapshot.id,
    )

    return version, member


async def extract_to_new_dataset(
    db: AsyncSession,
    *,
    lake_id: str,
    snapshot_ids: list[str],
    dataset_name: str | None = None,
    dataset_id: str | None = None,
    description: str | None = None,
    creator: str = "admin",
    field_mapping: dict[str, str] | None = None,
    doc_segment: DocSegmentOptions | None = None,
) -> Any:
    """从若干湖快照抽取生成数据集,目标数据集**新建或追加到已有**(治理改造契约地基)。

    数据湖 → 数据集的核心链路:
    1. 建空数据集(dataset_name 指定 name/description)或取已有数据集(dataset_id,
       此时复用其既有 semantic_type,不再按快照类别重新推断,保证追加语义一致)
    2. 每个快照作为一个表成员落进数据集(文件名 = 数据湖中的原始文件名,
       见 _lake_file_name;同名冲突时追加 _2/_3… 后缀避免覆盖)
    3. 血缘追踪字段(source_version 等)在 add_table_member 前已由
       extract_from_lake_snapshot 注入到 records
    4. 字段映射(可选):为每个快照单独配置模板,拼接多字段为 text

    Args:
        db: 数据库会话
        lake_id: 数据湖 ID
        snapshot_ids: 参与抽取的快照 id 列表(必须都属于 lake_id)
        dataset_name: 新数据集名称(与 dataset_id 二选一,由路由层校验)
        dataset_id: 目标已有数据集 id(与 dataset_name 二选一)
        description: 数据集描述(仅新建时生效)
        creator: 创建人
        field_mapping: 字段映射模板字典(key=快照ID, value=模板字符串)
        doc_segment: 文档类快照(word/pdf 等)的分段/清洗配置,本次抽取内
            所有文档快照共用;ppt/pptx 固定一页一条 text,不受分段参数影响

    Returns:
        目标 Dataset 对象(新建或已有)

    Raises:
        ExternalStoreError: 湖不存在 / 快照不存在 / 快照跨湖 / 空快照列表 /
            目标数据集不存在
    """
    from sqlalchemy import select

    from app.services.data_lake import get_lake_by_id
    from app.services.landing import add_table_member, create_dataset

    if not snapshot_ids:
        raise ExternalStoreError("至少选择一个快照")

    lake = await get_lake_by_id(db, lake_id)
    if not lake:
        raise ExternalStoreError(f"数据湖不存在: {lake_id}")

    # 一次性把选中的快照都取出,校验都属于 lake_id
    result = await db.execute(
        select(DataLakeSnapshot).where(DataLakeSnapshot.id.in_(snapshot_ids))
    )
    snapshots = list(result.scalars().all())
    if len(snapshots) != len(snapshot_ids):
        raise ExternalStoreError("部分快照不存在或已被删除")
    stray = [s.id for s in snapshots if s.lake_id != lake_id]
    if stray:
        raise ExternalStoreError(f"以下快照不属于 {lake_id}: {stray}")

    if dataset_id:
        dataset = await db.get(Dataset, dataset_id)
        if dataset is None:
            raise ExternalStoreError(f"目标数据集不存在: {dataset_id}")
        # 追加到已有数据集:复用其既有 semantic_type,不再按本次快照类别重新推断
        semantic_type = dataset.semantic_type
    else:
        # 语义类型推断:全 database 类快照 → structured;含非 database → unstructured
        categories = {s.data_category for s in snapshots}
        semantic_type = (
            "structured" if categories == {"database"} else "unstructured"
        )
        dataset = await create_dataset(
            db,
            name=dataset_name or "未命名数据集",
            semantic_type=semantic_type,
            description=description,
            creator=creator,
        )

    # 二进制快照(图片/音频/视频)按模态分组,各自落一个 manifest 版本(DJ 可读
    # 契约,见 landing.land_media_manifest);前置解析(OCR/ASR/关键帧,见
    # docs/数据治理.md §2.2)尚未实现,manifest 里的 text 先是占位 token。
    binary_snapshots = [s for s in snapshots if s.storage_format in BINARY_FORMATS]
    other_snapshots = [s for s in snapshots if s.storage_format not in BINARY_FORMATS]

    by_kind: dict[str, list[DataLakeSnapshot]] = {}
    for snapshot in binary_snapshots:
        by_kind.setdefault(media_kind(snapshot.storage_format), []).append(snapshot)

    for kind, group in by_kind.items():
        items = [
            (
                _lake_file_name(snapshot),
                await _download_snapshot_bytes(snapshot),
            )
            for snapshot in group
        ]
        try:
            await land_media_manifest(db, dataset.id, files=items, data_type=kind)
        except LandingError as exc:
            # 本函数对外只承诺 ExternalStoreError(见函数 docstring),统一转换
            raise ExternalStoreError(str(exc)) from exc

    # 其余(数据库/文本/文档)逐快照抽取并落表成员;成员名取数据湖原始文件名
    # (_lake_file_name → _safe_table_name 去扩展名/非法字符)。原文件名可能重复
    # (如同一文件多次入湖得到不同 source_version),而 table_name 版本内必须唯一
    # (add_table_member 同名会覆盖),故追加 _2/_3… 后缀去重,避免静默丢数据。
    from app.services.landing import _safe_table_name

    used_names: set[str] = set()
    for snapshot in other_snapshots:
        records = await extract_from_lake_snapshot(
            db,
            lake_id,
            snapshot.source_version,
            inject_lineage=True,
            doc_options=doc_segment,
        )

        # 应用字段映射(表格类快照:database/tabular + 该快照配置了模板时执行)
        # tabular 包含 csv/tsv/xlsx/xls/jsonl 等结构化文件
        template = field_mapping.get(snapshot.id) if field_mapping else None
        if (
            template
            and template.strip()
            and snapshot.data_category in ("database", "tabular")
        ):
            _logger.info(
                "[字段映射] snapshot=%s 应用模板,裁列为 text + 血缘字段", snapshot.id
            )
            records = await _apply_field_mapping_transform(records, template)

        base = _safe_table_name(_lake_file_name(snapshot))
        table_name = base
        seq = 2
        while table_name in used_names:
            table_name = f"{base}_{seq}"
            seq += 1
        used_names.add(table_name)
        await add_table_member(
            db,
            dataset.id,
            records,
            table_name=table_name,
            semantic_type=semantic_type,
            source_format=snapshot.storage_format,
            note=f"从湖 {lake.name} 快照 {snapshot.source_version} 抽取",
            storage_format="jsonl",
            source_snapshot_id=snapshot.id,
        )

    return dataset
