"""接入落地契约:把任意来源规范化为 DJ 可读的 jsonl,落地为受管 DatasetVersion。

这是 M0 地基的"输入前提":任何连接器(上传 / S3 / HDFS / DB)最终都调用本服务,
产出 `origin=managed` 的不可变版本。当前实现首个连接器——本地上传。
首次落地无 Job,故 `produced_by_job_id` 为空(模型允许)。
"""

from __future__ import annotations

import csv
import io
import json
import secrets
from pathlib import Path

import openpyxl
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.dataset import Dataset
from app.models.dataset_version import DatasetVersion
# 注意:external_store 反向 import 本模块的 BINARY_FORMATS,故此处用函数内延迟
# import(见 land_records / land_upload_raw),避免模块加载期循环导入。
from app.services.semantic_registry import (
    apply_semantic_spec,
    coerce_semantic_type,
    infer_semantic_from_data_type,
)

# 文档类:用 markitdown 提取文本,按段落落地
DOC_FORMATS = {"pdf", "doc", "docx", "ppt", "pptx", "html"}
# 可直接落地的源格式(覆盖需求 #3 列出的全部常见格式)
LANDABLE_FORMATS = {
    "jsonl",
    "json",
    "csv",
    "tsv",
    "txt",
    "log",
    "xlsx",
    "xls",
    *DOC_FORMATS,
}

# 二进制类:原样存储,不规范化(图像 / 音频 / 视频)。
# 按模态拆三组,BINARY_FORMATS 取并集——单一事实源,媒体分类(media_kind)复用,
# 避免「能否落地」与「属哪种模态」两处枚举漂移。
IMAGE_FORMATS = {"png", "jpg", "jpeg", "gif", "bmp", "webp"}
AUDIO_FORMATS = {"mp3", "wav", "flac", "m4a", "aac", "ogg"}
VIDEO_FORMATS = {"mp4", "avi", "mov", "mkv", "webm"}
BINARY_FORMATS = IMAGE_FORMATS | AUDIO_FORMATS | VIDEO_FORMATS

# 媒体扩展名 → 模态名(image/audio/video);非媒体 → None。
_MEDIA_KIND_BY_FORMAT = {
    **dict.fromkeys(IMAGE_FORMATS, "image"),
    **dict.fromkeys(AUDIO_FORMATS, "audio"),
    **dict.fromkeys(VIDEO_FORMATS, "video"),
}


def media_kind(fmt: str) -> str | None:
    """媒体扩展名 → 模态名(image/audio/video);非媒体格式 → None。"""
    return _MEDIA_KIND_BY_FORMAT.get(fmt.lower())

# 数据接入可受理的全部格式(可规范化 + 二进制零拷贝)
INGESTABLE_FORMATS = LANDABLE_FORMATS | BINARY_FORMATS

# 媒体批量接入版本 format:manifest jsonl(每行引用对象存储媒体),
# 物化时下载成员并改写本地路径喂给 dj-process(见 external_store.materialized_version)。
MANIFEST_FORMAT = "manifest"

# markitdown 实例(懒加载,首次处理文档时才初始化,避免拖慢后端启动)
_markitdown = None


def _get_markitdown():  # noqa: ANN202
    """懒加载并缓存 MarkItDown 实例。"""
    global _markitdown
    if _markitdown is None:
        from markitdown import MarkItDown

        _markitdown = MarkItDown()
    return _markitdown


class LandingError(ValueError):
    """落地失败的基类。"""


class UnsupportedFormatError(LandingError):
    """源格式当前不支持直接落地(如 PDF/Office,见 #3 文档解析)。"""


class ParseError(LandingError):
    """源文件内容无法按其格式解析。"""


def _new_dataset_id() -> str:
    """形如 ``dset-`` + 6 位 hex。"""
    return f"dset-{secrets.token_hex(3)}"


def _new_version_id() -> str:
    """形如 ``dsv-`` + 6 位 hex。"""
    return f"dsv-{secrets.token_hex(3)}"


def _xlsx_to_records(content: bytes) -> list[dict]:
    """xlsx → 记录列表:首行为表头,其余每行一条(空行跳过)。"""
    try:
        wb = openpyxl.load_workbook(
            io.BytesIO(content), read_only=True, data_only=True
        )
    except Exception as exc:  # noqa: BLE001 解析失败统一上报
        raise ParseError(f"xlsx 解析失败:{exc}") from exc
    ws = wb.active
    rows = ws.iter_rows(values_only=True) if ws is not None else iter(())
    try:
        header = next(rows)
    except StopIteration:
        wb.close()
        return []
    columns = [
        str(h) if h is not None else f"col{i}" for i, h in enumerate(header)
    ]
    records: list[dict] = []
    for row in rows:
        if all(cell is None for cell in row):
            continue
        records.append(dict(zip(columns, row, strict=False)))
    wb.close()
    return records


def _xls_to_records(content: bytes) -> list[dict]:
    """xls(旧版 Excel)→ 记录列表,用 xlrd 逐行读。"""
    import xlrd

    try:
        book = xlrd.open_workbook(file_contents=content)
    except Exception as exc:  # noqa: BLE001 解析失败统一上报
        raise ParseError(f"xls 解析失败:{exc}") from exc
    sheet = book.sheet_by_index(0)
    if sheet.nrows == 0:
        return []
    header = sheet.row_values(0)
    columns = [
        str(h) if h not in (None, "") else f"col{i}"
        for i, h in enumerate(header)
    ]
    records: list[dict] = []
    for r in range(1, sheet.nrows):
        row = sheet.row_values(r)
        if all(c in (None, "") for c in row):
            continue
        records.append(dict(zip(columns, row, strict=False)))
    return records


def _doc_to_records(content: bytes, ext: str) -> list[dict]:
    """文档(pdf/doc/docx/ppt/pptx/html)→ markitdown 提取文本 → 按段落每段一条。"""
    try:
        result = _get_markitdown().convert_stream(
            io.BytesIO(content), file_extension=f".{ext}"
        )
    except Exception as exc:  # noqa: BLE001 解析失败统一上报
        raise ParseError(f"{ext} 解析失败:{exc}") from exc
    text = (result.text_content or "").strip()
    if not text:
        return []
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    return [{"text": p} for p in paras] if paras else [{"text": text}]


def normalize_to_records(content: bytes, fmt: str) -> list[dict]:
    """把源文件字节按格式规范化为记录列表(每条 → jsonl 一行)。

    - jsonl:逐行 JSON
    - json :顶层 list → 每元素一条;顶层 object → 单条
    - csv/tsv:表头为字段名,每行一条
    - txt :每非空行 → {"text": 行}
    其余格式抛 UnsupportedFormatError。解析失败抛 ParseError。
    """
    fmt = fmt.lower()
    if fmt not in LANDABLE_FORMATS:
        raise UnsupportedFormatError(fmt)
    if fmt == "xlsx":
        return _xlsx_to_records(content)
    if fmt == "xls":
        return _xls_to_records(content)
    if fmt in DOC_FORMATS:
        return _doc_to_records(content, fmt)
    try:
        text = content.decode("utf-8")
        if fmt == "jsonl":
            return [json.loads(ln) for ln in text.splitlines() if ln.strip()]
        if fmt == "json":
            data = json.loads(text)
            if isinstance(data, list):
                return [d if isinstance(d, dict) else {"value": d} for d in data]
            return [data]
        if fmt in ("csv", "tsv"):
            delimiter = "," if fmt == "csv" else "\t"
            reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
            return [dict(row) for row in reader]
        # txt
        return [{"text": ln} for ln in text.splitlines() if ln.strip()]
    except (json.JSONDecodeError, UnicodeDecodeError, csv.Error) as exc:
        raise ParseError(str(exc)) from exc


def records_to_jsonl_bytes(records: list[dict]) -> bytes:
    """把记录列表序列化为 jsonl 字节(每行一个 JSON 对象,UTF-8)。

    与 land_records 的本地落地一致:ensure_ascii=False 保留中文,非 JSON 原生
    类型(datetime/Decimal 等)经 default=str 兜底;嵌套对象(JSONB 列)按结构保留。
    空记录 → 空字节。
    """
    if not records:
        return b""
    return (
        "\n".join(
            json.dumps(rec, ensure_ascii=False, default=str) for rec in records
        )
        + "\n"
    ).encode("utf-8")


class ParquetCodecError(LandingError):
    """records ↔ parquet 编解码失败(供 land_records 捕获兜底回退 jsonl)。"""


def records_to_parquet_bytes(records: list[dict]) -> bytes:
    """把记录列表写成 parquet 字节(pyarrow 推断 schema,保留列类型)。

    空记录 / 同列异构类型等无法推断的情况抛 ParquetCodecError,由调用方兜底。
    Decimal/date/datetime 等原生类型由 pyarrow 直接保留;不做 default=str 降级。
    """
    if not records:
        raise ParquetCodecError("空记录无法推断 parquet schema")
    import io

    import pyarrow as pa
    import pyarrow.parquet as pq

    try:
        table = pa.Table.from_pylist(records)
        buf = io.BytesIO()
        pq.write_table(table, buf)
        return buf.getvalue()
    except ParquetCodecError:
        raise
    except Exception as exc:  # 任何 parquet 推断/写失败 → 兜底回退 jsonl(D2 红线)
        raise ParquetCodecError(f"parquet 编码失败:{exc}") from exc


def parquet_bytes_to_records(content: bytes, limit: int = 0) -> list[dict]:
    """读 parquet 字节还原为 dict 列表(limit>0 取前 N)。供预览/行数/物化复用。"""
    import io

    import pyarrow.parquet as pq

    table = pq.read_table(io.BytesIO(content))
    if limit > 0:
        table = table.slice(0, limit)
    return table.to_pylist()


async def land_records(
    session: AsyncSession,
    records: list[dict],
    *,
    dataset_name: str,
    data_type: str | None = None,
    semantic_type: str | None = None,
    source_kind: str | None = None,
    source_format: str | None = None,
    description: str | None = None,
    note: str | None = None,
    produced_by_job_id: str | None = None,
    creator: str = "admin",
    strict_semantic: bool = False,
    storage_format: str = "jsonl",
) -> tuple[Dataset, DatasetVersion]:
    """统一落地出口:把规范化记录写 jsonl → 建 Dataset(v1) + DatasetVersion。

    所有连接器(上传 / 采集 / ...)最终都汇到这里。`produced_by_job_id` 记录
    产出者(上传为空;采集传任务 id),即血缘上游。非 JSON 原生类型(datetime/
    Decimal 等)经 `default=str` 兜底为字符串。

    语义类型(与 data_type 正交,见 docs/plan/14):
    - 显式传 `semantic_type` → 按其标准 schema 归一别名 + 校验(strict 模式
      不合规抛 SemanticValidationError);记录会被归一后落地。
    - 未传 → 按 data_type 默认映射只打**版本/数据集级标签**,**不改记录**(零回归)。
    校验在写盘/建行之前完成,失败不留脏对象。
    """
    explicit = coerce_semantic_type(semantic_type)
    if explicit is not None:
        records, _report = apply_semantic_spec(
            records, explicit, strict=strict_semantic
        )
        effective_semantic: str | None = explicit.value
    else:
        inferred = infer_semantic_from_data_type(data_type)
        effective_semantic = inferred.value if inferred else None

    dataset = Dataset(
        id=_new_dataset_id(),
        name=dataset_name or "未命名数据集",
        description=description,
        data_type=data_type,
        semantic_type=effective_semantic,
        source_kind=source_kind,
        source_format=source_format,
        owner=creator,
        creator=creator,
    )
    session.add(dataset)

    # 产物统一上平台 MinIO(storage_uri=s3://):供 preview/加工/DuckDB 直查、
    # download 预签名给训练平台。复用 records_to_jsonl_bytes(与原本地写盘同编码)。
    # 平台未配置 → ExternalStoreError(回滚 pending dataset,不留脏对象)。
    from app.services.external_store import (  # 延迟 import 避免与 external_store 循环
        ExternalStoreError,
        upload_jsonl_to_uploads,
        upload_parquet_to_uploads,
    )

    effective_format = "jsonl"
    storage_uri: str
    size: int
    if storage_format == "parquet":
        try:
            parquet_bytes = records_to_parquet_bytes(records)
            storage_uri = await upload_parquet_to_uploads(dataset.id, 1, parquet_bytes)
            effective_format = "parquet"
            size = len(parquet_bytes)
        except ParquetCodecError:
            # 兜底:无法推断 parquet schema(空/嵌套/异构)→ 退回 jsonl,采集照常成功
            jsonl_bytes = records_to_jsonl_bytes(records)
            try:
                storage_uri = await upload_jsonl_to_uploads(dataset.id, 1, jsonl_bytes)
            except ExternalStoreError:
                await session.rollback()
                raise
            size = len(jsonl_bytes)
        except ExternalStoreError:
            await session.rollback()
            raise
    else:
        jsonl_bytes = records_to_jsonl_bytes(records)
        try:
            storage_uri = await upload_jsonl_to_uploads(dataset.id, 1, jsonl_bytes)
        except ExternalStoreError:
            await session.rollback()
            raise
        size = len(jsonl_bytes)

    # 切片 B:落地后由归一后 records 算结构化质量统计 + schema 快照(单次遍历,
    # ingest 规模行数无忧)。land_records 不评估策略 → quality_verdict 保持默认
    # "skipped",路由层根据任务级 policy 决定 passed/failed。
    from app.services.ingest_quality import (  # 延迟 import 避免无谓启动期加载
        compute_quality_stats,
        schema_snapshot,
    )

    quality_stats = compute_quality_stats(records)
    snapshot = schema_snapshot(quality_stats)

    version = DatasetVersion(
        id=_new_version_id(),
        dataset_id=dataset.id,
        version_no=1,
        storage_uri=storage_uri,
        format=effective_format,
        rows=len(records),
        size=size,
        origin="managed",
        semantic_type=effective_semantic,
        produced_by_job_id=produced_by_job_id,
        note=note,
        quality_stats=quality_stats,
        schema_snapshot=snapshot,
    )
    session.add(version)
    await session.commit()
    await session.refresh(dataset)
    await session.refresh(version)
    return dataset, version


async def land_upload(
    session: AsyncSession,
    *,
    content: bytes,
    filename: str,
    source_format: str,
    dataset_name: str | None = None,
    data_type: str | None = None,
    semantic_type: str | None = None,
    description: str | None = None,
    creator: str = "admin",
    strict_semantic: bool = False,
) -> tuple[Dataset, DatasetVersion]:
    """本地上传连接器:规范化 → 落地。解析失败抛 LandingError,不留脏对象。"""
    records = normalize_to_records(content, source_format)
    return await land_records(
        session,
        records,
        dataset_name=dataset_name or Path(filename).stem or "未命名数据集",
        data_type=data_type,
        semantic_type=semantic_type,
        source_kind="local_upload",
        source_format=source_format.lower(),
        description=description,
        note=f"本地上传落地:{filename}",
        creator=creator,
        strict_semantic=strict_semantic,
    )


async def land_upload_raw(
    session: AsyncSession,
    *,
    content: bytes,
    filename: str,
    source_format: str,
    dataset_name: str | None = None,
    data_type: str | None = None,
    semantic_type: str | None = None,
    description: str | None = None,
    creator: str = "admin",
) -> tuple[Dataset, DatasetVersion]:
    """二进制本地上传:原样存储,不解析。版本 rows=None,format=源扩展名。

    二进制无 dict 行 → 不经 apply_semantic_spec(见 docs/plan/14 §3.6);仅按
    显式 semantic_type 或 data_type 默认映射打**版本/数据集级标签**(结构就绪)。
    """
    explicit = coerce_semantic_type(semantic_type)
    if explicit is not None:
        effective_semantic: str | None = explicit.value
    else:
        inferred = infer_semantic_from_data_type(data_type)
        effective_semantic = inferred.value if inferred else None

    dataset = Dataset(
        id=_new_dataset_id(),
        name=dataset_name or Path(filename).stem or "未命名数据集",
        description=description,
        data_type=data_type,
        semantic_type=effective_semantic,
        source_kind="local_upload",
        source_format=source_format.lower(),
        owner=creator,
        creator=creator,
    )
    session.add(dataset)

    # 二进制原样上平台 MinIO(storage_uri=s3://),不解析;key=<id>/v1/<filename>。
    # 上传失败 → 回滚 pending dataset + 抛 LandingError,绝不留孤立 Dataset 行。
    from app.services.external_store import (  # 延迟 import 避免与 external_store 循环
        ExternalStoreError,
        platform_config,
        upload_object,
    )

    fname = Path(filename).name or f"data.{source_format}"
    bucket = settings.storage_minio_upload_bucket
    key = f"{dataset.id}/v1/{fname}"
    try:
        await upload_object(
            platform_config(),
            bucket,
            key,
            io.BytesIO(content),
            len(content),
        )
    except ExternalStoreError as exc:
        await session.rollback()
        raise LandingError(f"原样存储失败:{exc}") from exc
    storage_uri = f"s3://{bucket}/{key}"

    version = DatasetVersion(
        id=_new_version_id(),
        dataset_id=dataset.id,
        version_no=1,
        storage_uri=storage_uri,
        format=source_format.lower(),
        rows=None,
        size=len(content),
        origin="managed",
        semantic_type=effective_semantic,
        produced_by_job_id=None,
        note=f"本地上传(原样存):{filename}",
    )
    session.add(version)
    await session.commit()
    await session.refresh(dataset)
    await session.refresh(version)
    return dataset, version
