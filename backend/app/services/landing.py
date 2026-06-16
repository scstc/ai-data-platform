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

# 二进制类:原样存储,不规范化(图像 / 音频 / 视频)
BINARY_FORMATS = {
    "png", "jpg", "jpeg", "gif", "bmp", "webp",
    "mp3", "wav", "flac", "m4a", "aac", "ogg",
    "mp4", "avi", "mov", "mkv", "webm",
}

# 数据接入可受理的全部格式(可规范化 + 二进制零拷贝)
INGESTABLE_FORMATS = LANDABLE_FORMATS | BINARY_FORMATS

# 媒体批量接入产物的版本 format:一份 manifest jsonl(每行引用对象存储里的媒体),
# 物化时下载成员并改写为本地路径喂给 dj-process(见 external_store.materialized_version)。
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


async def land_records(
    session: AsyncSession,
    records: list[dict],
    *,
    dataset_name: str,
    data_type: str | None = None,
    description: str | None = None,
    note: str | None = None,
    produced_by_job_id: str | None = None,
    creator: str = "admin",
) -> tuple[Dataset, DatasetVersion]:
    """统一落地出口:把规范化记录写 jsonl → 建 Dataset(v1) + DatasetVersion。

    所有连接器(上传 / 采集 / ...)最终都汇到这里。`produced_by_job_id` 记录
    产出者(上传为空;采集传任务 id),即血缘上游。非 JSON 原生类型(datetime/
    Decimal 等)经 `default=str` 兜底为字符串。
    """
    dataset = Dataset(
        id=_new_dataset_id(),
        name=dataset_name or "未命名数据集",
        description=description,
        data_type=data_type,
        owner=creator,
        creator=creator,
    )
    session.add(dataset)

    out_dir = Path(settings.datasets_dir) / dataset.id / "v1"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "data.jsonl"
    with out_path.open("w", encoding="utf-8") as fp:
        for rec in records:
            fp.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")

    version = DatasetVersion(
        id=_new_version_id(),
        dataset_id=dataset.id,
        version_no=1,
        storage_uri=str(out_path),
        format="jsonl",
        rows=len(records),
        size=out_path.stat().st_size,
        origin="managed",
        produced_by_job_id=produced_by_job_id,
        note=note,
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
    description: str | None = None,
    creator: str = "admin",
) -> tuple[Dataset, DatasetVersion]:
    """本地上传连接器:规范化 → 落地。解析失败抛 LandingError,不留脏对象。"""
    records = normalize_to_records(content, source_format)
    return await land_records(
        session,
        records,
        dataset_name=dataset_name or Path(filename).stem or "未命名数据集",
        data_type=data_type,
        description=description,
        note=f"本地上传落地:{filename}",
        creator=creator,
    )


async def land_upload_raw(
    session: AsyncSession,
    *,
    content: bytes,
    filename: str,
    source_format: str,
    dataset_name: str | None = None,
    data_type: str | None = None,
    description: str | None = None,
    creator: str = "admin",
) -> tuple[Dataset, DatasetVersion]:
    """二进制本地上传:原样存储,不解析。版本 rows=None,format=源扩展名。"""
    dataset = Dataset(
        id=_new_dataset_id(),
        name=dataset_name or Path(filename).stem or "未命名数据集",
        description=description,
        data_type=data_type,
        owner=creator,
        creator=creator,
    )
    session.add(dataset)

    out_dir = Path(settings.datasets_dir) / dataset.id / "v1"
    out_path = out_dir / (Path(filename).name or f"data.{source_format}")
    # 磁盘写失败(空间/权限)→ 清理半成品 + 回滚 pending dataset,抛 LandingError,
    # 绝不留下没有对应文件的孤立 Dataset 行
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(content)
    except OSError as exc:
        out_path.unlink(missing_ok=True)
        await session.rollback()
        raise LandingError(f"原样存储失败:{exc}") from exc

    version = DatasetVersion(
        id=_new_version_id(),
        dataset_id=dataset.id,
        version_no=1,
        storage_uri=str(out_path),
        format=source_format.lower(),
        rows=None,
        size=len(content),
        origin="managed",
        produced_by_job_id=None,
        note=f"本地上传(原样存):{filename}",
    )
    session.add(version)
    await session.commit()
    await session.refresh(dataset)
    await session.refresh(version)
    return dataset, version
