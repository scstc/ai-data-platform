"""接入落地契约:把任意来源规范化为 DJ 可读的 jsonl,落地为受管 DatasetVersion。

这是 M0 地基的"输入前提":任何连接器(上传 / S3 / HDFS / DB)最终都调用本服务,
产出 `origin=managed` 的不可变版本。当前实现首个连接器——本地上传。
首次落地无 Job,故 `produced_by_job_id` 为空(模型允许)。
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import secrets
from datetime import UTC, datetime
from pathlib import Path

import openpyxl
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.ids import uuid7_hex
from app.models.dataset import Dataset
from app.models.dataset_version import DatasetVersion
from app.models.dataset_version_table import DatasetVersionTable

# 注意:external_store 反向 import 本模块的 BINARY_FORMATS,故此处用函数内延迟
# import(见 land_records / land_upload_raw),避免模块加载期循环导入。
from app.services.semantic_registry import (
    SemanticType,
    apply_semantic_spec,
    coerce_semantic_type,
    collect_modalities,
    default_schema_variant,
    infer_semantic_from_data_type,
    infer_train_type,
)

# 文档类:用 markitdown 提取文本,按段落落地
DOC_FORMATS = {"pdf", "doc", "docx", "ppt", "pptx", "html"}
# GIS 行业标准:GeoJSON FeatureCollection(OGC RFC 7946);文件本身是 JSON 文本,
# 但 .geojson 后缀比 .json 更不容易被误识别为通用 JSON。
GIS_FORMATS = {"geojson"}
# 可直接落地的源格式(覆盖需求 #3 列出的全部常见格式)
LANDABLE_FORMATS = {
    "jsonl",
    "json",
    "csv",
    "tsv",
    "txt",
    "log",
    "md",
    "markdown",
    "xlsx",
    "xls",
    *DOC_FORMATS,
    *GIS_FORMATS,
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

# data_type(前端多模态上传传 image/audio/video 单数)→ 标准媒体字段名(复数)。
# 单媒体原样/manifest 存储无真实文本 → 子标签为单模态(图片/音频/视频),不含 text。
_DATA_TYPE_TO_MEDIA_FIELD = {"image": "images", "audio": "audios", "video": "videos"}

# manifest 批量媒体接入:data_type → dj 特殊 token(占位符,dj-process 按此定位媒体)。
_MEDIA_TOKEN = {
    "image": "<__dj__image>",
    "audio": "<__dj__audio>",
    "video": "<__dj__video>",
}

# manifest 批量媒体接入:单文件体积上限(与 uploads.py / 前端 200MB 对齐)
_MAX_MEDIA_FILE_BYTES = 200 * 1024 * 1024


def media_kind(fmt: str) -> str | None:
    """媒体扩展名 → 模态名(image/audio/video);非媒体格式 → None。"""
    return _MEDIA_KIND_BY_FORMAT.get(fmt.lower())

# 数据接入可受理的全部格式(可规范化 + 二进制零拷贝)
INGESTABLE_FORMATS = LANDABLE_FORMATS | BINARY_FORMATS

# 媒体批量接入版本 format:manifest jsonl(每行引用对象存储媒体),
# 物化时下载成员并改写本地路径喂给 dj-process(见 external_store.materialized_version)。
MANIFEST_FORMAT = "manifest"

# manifest 版本在成员级 UI/校验(数据预览之外的清洗任务编辑器)里的合成成员名:
# 一个 manifest 版本 = 一份媒体清单,不落 dataset_version_tables,天然只有"一个成员"
# (整版本一套算子),见 datasets.py _attach_tables 与 jobs.py _start_job。
MANIFEST_MEMBER_NAME = "manifest"

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


class OcrUnavailableError(LandingError):
    """OCR 未启用 / OCR service 不可达(治理整改 G10)。"""


# 扫描型 PDF 判定阈值:每页平均非空白字符数低于此值 → 疑似扫描件(需 OCR)
_SCANNED_CHAR_PER_PAGE = 50


def _markdown_to_plain_text(text: str) -> str:
    """markitdown 输出的 markdown → 纯文本(治理整改 G12)。

    去除 #/**/[]()/表格分隔/列表前缀等标记,避免干扰 data-juicer 的语言检测/长度/
    去重算子统计。纯函数(仅标准库 re),可单测。
    """
    import re

    lines: list[str] = []
    for raw in text.splitlines():
        ln = raw
        # 围栏代码块标记行 ``` → 删
        if re.match(r"^\s*```", ln):
            continue
        # 表格分隔行 |---|---| → 删
        if re.match(r"^\s*\|?\s*:?-{2,}.*$", ln) and "|" in ln and set(
            ln.strip()
        ) <= set("|:- "):
            continue
        # 水平分割线 ---/***/___ → 删
        if re.match(r"^\s*([-*_])\1{2,}\s*$", ln):
            continue
        # 图片 ![alt](url) → 空(先于链接,避免吃掉 alt)
        ln = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", ln)
        # 行内链接 [text](url) → text
        ln = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", ln)
        # 标题前缀 #{1,6}
        ln = re.sub(r"^\s*#{1,6}\s+", "", ln)
        # 引用前缀 >
        ln = re.sub(r"^\s*>\s?", "", ln)
        # 列表项前缀(保留缩进)
        ln = re.sub(r"^(\s*)([*+-]|\d+\.)\s+", r"\1", ln)
        # 表格行:|A|B| → A B
        stripped = ln.strip()
        if stripped.startswith("|") or stripped.endswith("|"):
            cells = [c.strip() for c in stripped.strip("|").split("|")]
            ln = " ".join(c for c in cells if c)
        # 强调/行内代码标记 **/__/*/_/`
        ln = re.sub(r"(\*\*|__|\*|_|`)", "", ln)
        lines.append(ln)
    out = "\n".join(lines)
    # 收敛多余空行
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


def _pdf_page_count(content: bytes) -> int:
    """PDF 页数;取不到(损坏/无 pypdf)退化为 1(按总字符判定,绝不中断主流程)。"""
    try:
        import io as _io

        from pypdf import PdfReader

        return len(PdfReader(_io.BytesIO(content)).pages)
    except Exception:  # noqa: BLE001 取页数失败不应中断接入
        return 1


def _detect_pdf_type(text: str, page_count: int = 1) -> bool:
    """判断 PDF 是否扫描型(需 OCR)。纯函数:每页平均非空白字符 < 阈值 → True。"""
    chars = len("".join(text.split()))
    return (chars / max(page_count, 1)) < _SCANNED_CHAR_PER_PAGE


def _ocr_pdf(content: bytes) -> str:
    """调用独立 OCR service 识别扫描型 PDF(治理整改 G10)。

    引擎本体(Unlimited-OCR/paddleocr,~1GB 模型 + GPU)是独立 service,平台仅作
    HTTP 客户端。未启用 → OcrUnavailableError;调用失败/超时 → ParseError
    (点名原因,绝不静默返回 '' —— 守 D2 红线 + Rule 12 Fail loud)。
    """
    if not settings.ocr_enabled or not settings.ocr_endpoint:
        raise OcrUnavailableError("OCR 未启用(设 OCR_ENABLED + OCR_ENDPOINT)")
    import httpx

    try:
        resp = httpx.post(
            settings.ocr_endpoint,
            files={"file": ("doc.pdf", content, "application/pdf")},
            data={"lang": settings.ocr_lang},
            timeout=settings.ocr_timeout,
        )
        resp.raise_for_status()
        return resp.json().get("text", "")
    except httpx.HTTPError as exc:
        raise ParseError(f"OCR service 调用失败:{exc}") from exc


def _new_dataset_id() -> str:
    """形如 ``dset-`` + UUIDv7 十六进制(时间前缀,天然按创建时间字典序)。"""
    return f"dset-{uuid7_hex()}"


def _new_version_id() -> str:
    """形如 ``dsv-`` + 6 位 hex。"""
    return f"dsv-{secrets.token_hex(3)}"


def _new_member_id() -> str:
    """形如 ``dvt-`` + 6 位 hex(版本-表成员主键)。"""
    return f"dvt-{secrets.token_hex(3)}"


def _safe_table_name(filename: str) -> str:
    """由文件名派生成员表名:去路径与扩展名,非法字符替 ``_``,空则 ``data``。"""
    stem = Path(filename).stem or "data"
    cleaned = "".join(ch if (ch.isalnum() or ch in "._-") else "_" for ch in stem)
    return cleaned.strip("_") or "data"


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


def _convert_legacy_doc_to_text(content: bytes) -> str:
    """老 .doc(二进制)→ 纯文本:antiword 优先(快、原生 Word);soffice headless 兜底。

    antiword 0.37 对部分现代生成的 .doc 报 `text stream too small to handle`,此时
    退到 LibreOffice headless 转 .docx 后用 mammoth 解(.docx → markdown)。
    antiword / soffice 都不可用时抛 ParseError(让上层正常报错,绝不静默成功)。
    """
    import os
    import shutil
    import subprocess
    import tempfile

    # 1) antiword 直读(快速路径)
    if shutil.which("antiword"):
        try:
            with tempfile.NamedTemporaryFile(suffix=".doc", delete=False) as f:
                f.write(content)
                tmp = f.name
            try:
                out = subprocess.run(
                    ["antiword", tmp],
                    capture_output=True,
                    timeout=30,
                    check=False,
                )
                if out.returncode == 0 and out.stdout:
                    return out.stdout.decode("utf-8", errors="replace")
            finally:
                os.unlink(tmp)
        except Exception:  # noqa: BLE001
            pass

    # 2) soffice headless: .doc → .docx,再 mammoth
    if shutil.which("soffice") or shutil.which("libreoffice"):
        soffice = shutil.which("soffice") or shutil.which("libreoffice")
        try:
            with tempfile.TemporaryDirectory() as td:
                doc = os.path.join(td, "in.doc")
                with open(doc, "wb") as f:
                    f.write(content)
                env = os.environ.copy()
                env["HOME"] = td  # soffice 启动需要可写 HOME
                subprocess.run(
                    [
                        soffice,
                        "--headless",
                        "--norestore",
                        "--nologo",
                        "--nodefault",
                        "--nofirststartwizard",
                        "--convert-to",
                        "docx",
                        "--outdir",
                        td,
                        doc,
                    ],
                    capture_output=True,
                    timeout=120,
                    check=False,
                    env=env,
                )
                docx = os.path.join(td, "in.docx")
                if os.path.exists(docx):
                    import mammoth

                    with open(docx, "rb") as f:
                        result = mammoth.extract_raw_text(f)
                    if result.value:
                        return result.value
        except Exception:  # noqa: BLE001
            pass

    raise ParseError("无法解析 .doc:请安装 antiword 或 libreoffice-core")


def _doc_to_records(content: bytes, ext: str) -> list[dict]:
    """文档(pdf/doc/docx/ppt/pptx/html)→ markitdown 提取 → 去格式 → 按段落每段一条。

    .doc 走双桥(antiword → soffice→mammoth);其他用 markitdown。

    pdf 扫描型识别(G10):提取空或字符密度过低 → 疑似扫描件,OCR 启用则调 OCR,
    否则 Fail-loud 抛错(不静默落 0 行,守 D2 红线)。
    markdown 去格式(G12):markitdown 输出含 #/**/[]() 等标记,去格式为纯文本,
    避免干扰 data-juicer 算子统计。
    """
    if ext == "doc":
        # .doc 双桥产出已是纯文本,不经 markitdown / 去格式
        text = _convert_legacy_doc_to_text(content).strip()
        if not text:
            raise ParseError(".doc 提取为空(可能是空文档或转换失败)")
        paras = [p.strip() for p in text.split("\n\n") if p.strip()]
        return [{"text": p} for p in paras] if paras else [{"text": text}]
    try:
        result = _get_markitdown().convert_stream(
            io.BytesIO(content), file_extension=f".{ext}"
        )
    except Exception as exc:  # noqa: BLE001 解析失败统一上报
        raise ParseError(f"{ext} 解析失败:{exc}") from exc
    text = (result.text_content or "").strip()

    # PDF 扫描型分流(G10):空 / 字符密度过低 → OCR(启用时)或 Fail-loud
    if ext == "pdf":
        page_count = _pdf_page_count(content)
        if not text or _detect_pdf_type(text, page_count):
            if settings.ocr_enabled:
                ocr_text = _ocr_pdf(content).strip()
                # 取更长结果(呼应设计 §2.1.2:OCR 与直提取对比取优)
                if len(ocr_text) > len(text):
                    text = ocr_text
            if not text:
                raise ParseError(
                    "PDF 提取为空,疑似扫描型 PDF(需 OCR);"
                    "请开启 OCR(OCR_ENABLED)或改用文本型 PDF"
                )
    elif not text:
        raise ParseError(f"{ext} 提取为空(文档无可提取文本)")

    # G12:去 markdown 格式标记 → 纯文本
    text = _markdown_to_plain_text(text)
    if not text:
        raise ParseError(f"{ext} 去格式后为空")
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    return [{"text": p} for p in paras] if paras else [{"text": text}]


def _geojson_to_records(content: bytes) -> list[dict]:
    """GeoJSON FeatureCollection → 一行一条 record(每 Feature 一条)。

    - 顶层是 FeatureCollection:每个 Feature 展平为一条记录;properties 字段平铺,
      从 geometry.coordinates(GeoJSON 是 [lon, lat])抽出 lat/lon 并附 geometry_type。
    - 顶层是单个 Feature:返回 1 条记录
    - 顶层是普通 JSON object/array:退到通用 JSON 解析(绝不抛错)
    - 非 dict 类型或解析失败:回退 [] + ParseError
    """
    try:
        # 用 utf-8-sig 兼容 Windows/Excel 工具链保存的 UTF-8 BOM 文件,
        # 对无 BOM 的标准文件完全等价于 utf-8。
        data = json.loads(content.decode("utf-8-sig"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ParseError(str(exc)) from exc

    def _flatten_feature(feat: dict) -> dict:
        out: dict = {}
        props = feat.get("properties") or {}
        if isinstance(props, dict):
            out.update(props)
        geom = feat.get("geometry") or {}
        if isinstance(geom, dict):
            out["geometry_type"] = geom.get("type")
            coords = geom.get("coordinates")
            if isinstance(coords, (list, tuple)) and len(coords) >= 2:
                try:
                    out["lon"] = float(coords[0])
                    out["lat"] = float(coords[1])
                except (TypeError, ValueError):
                    pass
        if "type" in feat and "feature_type" not in out:
            out["feature_type"] = feat["type"]
        return out

    if isinstance(data, dict) and data.get("type") == "FeatureCollection":
        features = data.get("features") or []
        return [_flatten_feature(f) for f in features if isinstance(f, dict)]
    if isinstance(data, dict) and data.get("type") == "Feature":
        return [_flatten_feature(data)]
    # 非标准 GeoJSON 形状:退到通用 JSON 解析,让上层仍能拿到 1 条记录
    if isinstance(data, list):
        return [d if isinstance(d, dict) else {"value": d} for d in data]
    if isinstance(data, dict):
        return [data]
    return []


def normalize_to_records(content: bytes, fmt: str) -> list[dict]:
    """把源文件字节按格式规范化为记录列表(每条 → jsonl 一行)。

    - jsonl:逐行 JSON
    - json :顶层 list → 每元素一条;顶层 object → 单条
    - csv/tsv:表头为字段名,每行一条
    - txt/log/md/markdown:每非空行 → {"text": 行}
    - geojson:FeatureCollection 每 Feature 一行,自动抽取 lon/lat
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
    if fmt in GIS_FORMATS:
        return _geojson_to_records(content)
    try:
        # utf-8-sig 自动剥离 BOM 头,兼容 Windows/Excel 工具链导出。
        text = content.decode("utf-8-sig")
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
        # txt / log / md / markdown:逐行落地为 {"text": 行}
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


async def create_dataset(
    session: AsyncSession,
    *,
    name: str,
    data_type: str | None = None,
    semantic_type: str | None = None,
    source_kind: str | None = None,
    source_format: str | None = None,
    description: str | None = None,
    creator: str = "admin",
) -> Dataset:
    """建一个**空**数据集(不建任何版本)。数据集优先流程的入口。

    semantic_type 显式传则归一为枚举值;否则按 data_type 推断默认。
    train_type/schema_variant 是**版本级**元数据(见 docs spec §4.3),不落在
    Dataset 上——在首个表成员落地时(add_table_member)写定。
    """
    explicit = coerce_semantic_type(semantic_type)
    if explicit is not None:
        effective_semantic: str | None = explicit.value
    else:
        inferred = infer_semantic_from_data_type(data_type)
        effective_semantic = inferred.value if inferred else None

    dataset = Dataset(
        id=_new_dataset_id(),
        name=name or "未命名数据集",
        description=description,
        data_type=data_type,
        semantic_type=effective_semantic,
        source_kind=source_kind,
        source_format=source_format,
        owner=creator,
        creator=creator,
    )
    session.add(dataset)
    await session.commit()
    await session.refresh(dataset)
    return dataset


async def _target_draft_version(
    session: AsyncSession, dataset_id: str
) -> DatasetVersion:
    """定位可写 draft 版本(版本不可变约束的调和,见 spec §3):

    - 无版本 → 建 v1 draft。
    - 最新版本是 draft → 复用(在其内增/覆盖成员)。
    - 最新版本已 published → 建 v+1 draft,并**克隆**上一版成员(续接语义)。
    """
    latest = (
        await session.execute(
            select(DatasetVersion)
            .where(DatasetVersion.dataset_id == dataset_id)
            .order_by(DatasetVersion.version_no.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    if latest is not None and latest.publish_status == "draft":
        return latest

    next_no = (latest.version_no + 1) if latest is not None else 1
    ver = DatasetVersion(
        id=_new_version_id(),
        dataset_id=dataset_id,
        version_no=next_no,
        storage_uri=f"pending://{dataset_id}/v{next_no}/",
        format="jsonl",
        rows=0,
        size=0,
        origin="managed",
        publish_status="draft",
    )
    session.add(ver)
    await session.flush()
    # published → 新 draft:克隆上一版成员(指向同一旧文件,不复制数据)
    if latest is not None:
        prev = (
            await session.execute(
                select(DatasetVersionTable).where(
                    DatasetVersionTable.dataset_version_id == latest.id
                )
            )
        ).scalars().all()
        for pm in prev:
            session.add(
                DatasetVersionTable(
                    id=_new_member_id(),
                    dataset_version_id=ver.id,
                    table_name=pm.table_name,
                    storage_uri=pm.storage_uri,
                    format=pm.format,
                    rows=pm.rows,
                    size=pm.size,
                    schema_snapshot=pm.schema_snapshot,
                    schema_variant=pm.schema_variant,
                )
            )
    await session.commit()
    await session.refresh(ver)
    return ver


async def _recompute_version_rollup(
    session: AsyncSession, version: DatasetVersion
) -> None:
    """按成员重算版本级 rollup 字段。

    rows/size = 各成员之和;format = 唯一成员格式,混合则 "multi"。
    storage_uri **保持单文件指针语义**(spec §3 裁决):指向首个成员文件,
    使 download/export/materialize 及既有断言(test_landing_parquet 的
    endswith("data.parquet"))在单表数据集上继续成立;多文件枚举走 _members_of。
    成员按 table_name 排序取首个,保证确定性。
    """
    members = (
        await session.execute(
            select(DatasetVersionTable)
            .where(DatasetVersionTable.dataset_version_id == version.id)
            .order_by(DatasetVersionTable.table_name)
        )
    ).scalars().all()
    # rows:全部成员 rows 均为 None(纯二进制/原样存,行数未知)→ 版本 rows 保持 None;
    # 否则求和(把未知项当 0)。size 同理但二进制有真实字节数,直接求和。
    if members and all(m.rows is None for m in members):
        version.rows = None
    else:
        version.rows = sum((m.rows or 0) for m in members)
    version.size = sum((m.size or 0) for m in members)
    formats = {m.format for m in members}
    version.format = formats.pop() if len(formats) == 1 else "multi"
    if members:
        version.storage_uri = members[0].storage_uri
    await session.commit()


async def add_table_member(
    session: AsyncSession,
    dataset_id: str,
    records: list[dict],
    *,
    table_name: str,
    storage_format: str = "parquet",
    semantic_type: str | None = None,
    source_format: str | None = None,
    produced_by_job_id: str | None = None,
    strict_semantic: bool = False,
    train_type: str | None = None,
    schema_variant: str | None = None,
    note: str | None = None,
) -> tuple[DatasetVersion, DatasetVersionTable]:
    """把一张表的记录落成当前 draft 版本的一个成员(同名覆盖)。

    数据集优先流程的落地出口:定位/新建 draft 版本(_target_draft_version),
    写成员文件(parquet 失败回退 jsonl),upsert 成员行,刷新版本 rollup。
    首个成员定调版本级 train_type/schema_variant/semantic_type/modalities。
    """
    from app.services.external_store import (  # 延迟 import 避免循环
        ExternalStoreError,
        upload_jsonl_member,
        upload_parquet_member,
    )
    from app.services.ingest_quality import compute_quality_stats, schema_snapshot

    # 语义归一(与 land_records 同逻辑):显式传则归一+校验,否则按 data_type 推断
    explicit = coerce_semantic_type(semantic_type)
    version_modalities: list[str] | None = None
    if explicit is not None:
        records, report = apply_semantic_spec(
            records, explicit, strict=strict_semantic
        )
        effective_semantic: str | None = explicit.value
        if effective_semantic == SemanticType.MULTIMODAL.value:
            version_modalities = report.modalities or None
    else:
        effective_semantic = None

    version = await _target_draft_version(session, dataset_id)

    # 版本 semantic 缺省继承数据集级(create_dataset 已按 data_type 推断写定),
    # 保证新流程(建集→落成员)与旧 land_records 的版本级 semantic 行为一致。
    if effective_semantic is None:
        ds = await session.get(Dataset, dataset_id)
        if ds is not None:
            effective_semantic = ds.semantic_type

    # 写成员文件:parquet 优先,无法编码(空/嵌套/异构)回退 jsonl
    fmt = "parquet"
    if storage_format == "parquet":
        try:
            blob = records_to_parquet_bytes(records)
            uri = await upload_parquet_member(
                dataset_id, version.version_no, table_name, blob
            )
            size = len(blob)
        except ParquetCodecError:
            blob = records_to_jsonl_bytes(records)
            try:
                uri = await upload_jsonl_member(
                    dataset_id, version.version_no, table_name, blob
                )
            except ExternalStoreError:
                await session.rollback()
                raise
            fmt = "jsonl"
            size = len(blob)
        except ExternalStoreError:
            await session.rollback()
            raise
    else:
        blob = records_to_jsonl_bytes(records)
        try:
            uri = await upload_jsonl_member(
                dataset_id, version.version_no, table_name, blob
            )
        except ExternalStoreError:
            await session.rollback()
            raise
        fmt = "jsonl"
        size = len(blob)

    stats = compute_quality_stats(records)
    snap = schema_snapshot(stats)
    eff_train = train_type or infer_train_type(effective_semantic)
    eff_variant = schema_variant or default_schema_variant(eff_train)

    # upsert 成员(同名覆盖,靠 uq_dvt_version_table 保证版本内唯一)
    existing = (
        await session.execute(
            select(DatasetVersionTable).where(
                DatasetVersionTable.dataset_version_id == version.id,
                DatasetVersionTable.table_name == table_name,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.storage_uri = uri
        existing.format = fmt
        existing.rows = len(records)
        existing.size = size
        existing.schema_snapshot = snap
        existing.schema_variant = eff_variant
        member = existing
    else:
        member = DatasetVersionTable(
            id=_new_member_id(),
            dataset_version_id=version.id,
            table_name=table_name,
            storage_uri=uri,
            format=fmt,
            rows=len(records),
            size=size,
            schema_snapshot=snap,
            schema_variant=eff_variant,
        )
        session.add(member)

    # 首个成员定调版本级元数据(版本不可变:仅在尚未写定时填)。
    # source_kind/source_format 归数据集级(create_dataset 时写),不落版本。
    if version.train_type is None:
        version.train_type = eff_train
        version.schema_variant = eff_variant
    if version.semantic_type is None and effective_semantic is not None:
        version.semantic_type = effective_semantic
    if version_modalities is not None and version.modalities is None:
        version.modalities = version_modalities
    if produced_by_job_id and version.produced_by_job_id is None:
        version.produced_by_job_id = produced_by_job_id
    if note:
        version.note = note
    if version.quality_stats is None:
        version.quality_stats = stats
        version.schema_snapshot = snap
    await session.commit()
    await session.refresh(member)
    await _recompute_version_rollup(session, version)
    await session.refresh(version)
    return version, member


async def add_raw_batch(
    session: AsyncSession,
    dataset_id: str,
    *,
    file_count: int,
    total_size: int,
    bucket: str,
    prefix: str,
    note: str | None = None,
) -> DatasetVersion:
    """把一批"只存不解析"的原始文件登记到当前 draft 版本,不生成表成员。

    与 add_table_member 的区别:raw 模式没有可提取的结构化字段,若仍造一个
    DatasetVersionTable,_members_of 会优先返回这张伪表(只有 file_name/format/
    size 几列),掩盖掉每个原件的真实文件名/格式——所以这里不写表成员,让
    _members_of 走它已有的 originals/ 枚举兜底,如实展示每个原始文件。
    """
    version = await _target_draft_version(session, dataset_id)
    version.storage_uri = f"s3://{bucket}/{prefix}"
    version.format = "raw"
    version.rows = file_count
    version.size = total_size
    if note is not None:
        version.note = note
    await session.commit()
    await session.refresh(version)
    return version


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
    train_type: str | None = None,
    schema_variant: str | None = None,
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
    version_modalities: list[str] | None = None
    if explicit is not None:
        records, report = apply_semantic_spec(
            records, explicit, strict=strict_semantic
        )
        effective_semantic: str | None = explicit.value
        # 多模态:从校验报告取模态集合(images/audios/videos/text)落版本快照
        if effective_semantic == SemanticType.MULTIMODAL.value:
            version_modalities = report.modalities or None
    else:
        inferred = infer_semantic_from_data_type(data_type)
        effective_semantic = inferred.value if inferred else None
        # data_type 推断为 multimodal(如 image/audio/video):不改记录,纯扫描取模态集合
        if effective_semantic == SemanticType.MULTIMODAL.value:
            version_modalities = collect_modalities(records) or None

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

    # 训练用途元数据(G1):显式传入优先,否则按 semantic_type 推断默认。
    # 版本不可变,一次写定。
    effective_train_type = train_type or infer_train_type(effective_semantic)
    effective_schema_variant = schema_variant or default_schema_variant(
        effective_train_type
    )

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
        modalities=version_modalities,
        produced_by_job_id=produced_by_job_id,
        note=note,
        quality_stats=quality_stats,
        schema_snapshot=snapshot,
        train_type=effective_train_type,
        schema_variant=effective_schema_variant,
    )
    session.add(version)
    await session.commit()
    await session.refresh(dataset)
    await session.refresh(version)
    # 数据集优先改造:为该版本补一行表成员(table_name="data"),镜像版本级
    # storage_uri/format/rows/schema,使读路径(_members_of/materialized_version)
    # 统一走成员模型。单表数据集 = 恰好一个 "data" 成员;版本级 storage_uri 仍
    # 指向该文件(单文件指针语义不变,见 spec §3)。
    session.add(
        DatasetVersionTable(
            id=_new_member_id(),
            dataset_version_id=version.id,
            table_name="data",
            storage_uri=storage_uri,
            format=effective_format,
            rows=len(records),
            size=size,
            schema_snapshot=snapshot,
            schema_variant=effective_schema_variant,
        )
    )
    await session.commit()
    await session.refresh(version)
    return dataset, version


def _stamp_lineage(
    records: list[dict], *, source_file: str, doc_id: str, ingest_batch: str
) -> list[dict]:
    """为每条记录注入逐条血缘字段(治理整改 G14)。

    source_file(原始文件名)/ doc_id(文件内容 sha256)/ ingest_batch(落地时间戳)。
    不覆盖记录已有的同名字段(连接器/上游已注入时尊重其值),非 dict 行原样跳过。
    兑现血缘溯源:跨文件合并到一个数据集后仍可回溯单条样本来自哪个原始文件。
    """
    for rec in records:
        if not isinstance(rec, dict):
            continue
        rec.setdefault("source_file", source_file)
        rec.setdefault("doc_id", doc_id)
        rec.setdefault("ingest_batch", ingest_batch)
    return records


async def land_upload(
    session: AsyncSession,
    *,
    content: bytes,
    filename: str,
    source_format: str,
    dataset_id: str | None = None,
    dataset_name: str | None = None,
    data_type: str | None = None,
    semantic_type: str | None = None,
    description: str | None = None,
    creator: str = "admin",
    strict_semantic: bool = False,
) -> tuple[Dataset, DatasetVersion]:
    """本地上传连接器:规范化 → 注入逐条血缘 → 落地。

    `dataset_id` 传入(数据集优先流程)→ 作为表成员落进该数据集的 draft 版本
    (table_name 由文件名派生);不传 → 旧行为(新建 Dataset+v1,向后兼容)。
    解析失败抛 LandingError,不留脏对象。
    """
    records = normalize_to_records(content, source_format)
    _stamp_lineage(
        records,
        source_file=filename,
        doc_id=f"sha256:{hashlib.sha256(content).hexdigest()}",
        ingest_batch=datetime.now(UTC).isoformat(),
    )
    if dataset_id is not None:
        version, _member = await add_table_member(
            session,
            dataset_id,
            records,
            table_name=_safe_table_name(filename),
            storage_format="jsonl",
            semantic_type=semantic_type,
            source_format=source_format.lower(),
            strict_semantic=strict_semantic,
            note=f"本地上传落地:{filename}",
        )
        dataset = await session.get(Dataset, dataset_id)
        return dataset, version
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


async def _land_raw_member(
    session: AsyncSession,
    dataset_id: str,
    *,
    content: bytes,
    filename: str,
    source_format: str,
) -> tuple[Dataset, DatasetVersion]:
    """二进制原样存为目标数据集 draft 版本的一个 raw 表成员(不解析)。

    key=``<dataset_id>/v<n>/<table>.<ext>``;成员 format=源扩展名,rows=None。
    平台未配置/上传失败 → 回滚并抛 LandingError。
    """
    from app.services.external_store import (
        ExternalStoreError,
        platform_config,
        upload_object,
    )

    version = await _target_draft_version(session, dataset_id)
    table_name = _safe_table_name(filename)
    ext = source_format.lower()
    bucket = settings.storage_minio_upload_bucket
    key = f"{dataset_id}/v{version.version_no}/{table_name}.{ext}"
    try:
        await upload_object(
            platform_config(), bucket, key, io.BytesIO(content), len(content)
        )
    except ExternalStoreError as exc:
        await session.rollback()
        raise LandingError(f"原样存储失败:{exc}") from exc
    uri = f"s3://{bucket}/{key}"

    existing = (
        await session.execute(
            select(DatasetVersionTable).where(
                DatasetVersionTable.dataset_version_id == version.id,
                DatasetVersionTable.table_name == table_name,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.storage_uri = uri
        existing.format = ext
        existing.rows = None
        existing.size = len(content)
        member = existing
    else:
        member = DatasetVersionTable(
            id=_new_member_id(),
            dataset_version_id=version.id,
            table_name=table_name,
            storage_uri=uri,
            format=ext,
            rows=None,
            size=len(content),
        )
        session.add(member)
    if version.note is None:
        version.note = f"本地上传(原样存):{filename}"
    await session.commit()
    await _recompute_version_rollup(session, version)
    await session.refresh(version)
    dataset = await session.get(Dataset, dataset_id)
    return dataset, version


async def land_media_manifest(
    session: AsyncSession,
    dataset_id: str,
    *,
    files: list[tuple[str, bytes]],
    data_type: str,
) -> DatasetVersion:
    """一批媒体字节(单模态)→ manifest jsonl 版本(一文件一行,DJ 可读契约)。

    `POST /datasets/upload-media` 与数据湖抽取(音/图/视频快照)共用的落地核心:
    复用该数据集最新 draft 版本(有则续写 manifest 追加行,无则新建);manifest 是
    整版本形态(非表成员)。`files` 为 ``(filename, content)``,仅支持单模态
    (image/audio/video,一个数据集一种模态,见 `_DATA_TYPE_TO_MEDIA_FIELD`)。

    Raises:
        LandingError: data_type 不支持 / 空文件列表 / 超出接入上限 / 单文件或总体积超限
        ExternalStoreError: 平台存储未配置 / 对象写入失败(写入失败时已尽力回收
            本次已写对象,不留孤儿)——与 LandingError 分开抛,便于调用方映射
            400(校验类)与 503(存储类)两种状态码
    """
    from app.services.external_store import (
        MAX_MANIFEST_MEMBERS,
        MAX_MATERIALIZE_BYTES,
        ExternalStoreError,
        download_to_temp,
        platform_config,
        remove_prefix,
        upload_object,
    )

    field = _DATA_TYPE_TO_MEDIA_FIELD.get(data_type)
    token = _MEDIA_TOKEN.get(data_type)
    if field is None or token is None:
        raise LandingError("媒体批量接入仅支持 image / audio / video 类型")
    if not files:
        raise LandingError("请至少选择一个文件")
    if len(files) > MAX_MANIFEST_MEMBERS:
        raise LandingError(f"一次最多接入 {MAX_MANIFEST_MEMBERS} 个文件")

    cfg = platform_config()  # ExternalStoreError(未配置)原样上抛,不转 LandingError

    bucket = settings.storage_minio_upload_bucket
    existing_draft = (
        await session.execute(
            select(DatasetVersion)
            .where(
                DatasetVersion.dataset_id == dataset_id,
                DatasetVersion.publish_status == "draft",
            )
            .order_by(DatasetVersion.version_no.desc())
            .limit(1)
        )
    ).scalar_one_or_none()

    if existing_draft is not None:
        version = existing_draft
        version_no = version.version_no
        existing_manifest_rows: list[dict] = []
        if version.storage_uri:
            try:
                _uri_parts = version.storage_uri.removeprefix("s3://").split("/", 1)
                _m_bucket, _m_key = _uri_parts[0], _uri_parts[1]
                _tmp = await download_to_temp(cfg, _m_bucket, _m_key)
                existing_manifest_rows = [
                    json.loads(line)
                    for line in _tmp.read_text(encoding="utf-8").splitlines()
                    if line.strip()
                ]
                _tmp.unlink(missing_ok=True)
            except Exception:  # noqa: BLE001 旧清单读取失败按空清单处理,不阻断本次接入
                existing_manifest_rows = []
        start_idx = len(existing_manifest_rows)
    else:
        max_no = (
            await session.execute(
                select(func.max(DatasetVersion.version_no)).where(
                    DatasetVersion.dataset_id == dataset_id
                )
            )
        ).scalar()
        version_no = (max_no or 0) + 1
        existing_manifest_rows = []
        start_idx = 0
        version = None

    ver_prefix = f"{dataset_id}/v{version_no}/"

    async def _gc() -> None:
        try:
            await remove_prefix(platform_config(), bucket, ver_prefix)
        except ExternalStoreError:
            pass  # 尽力回收,失败不掩盖原始错误

    new_rows: list[dict] = []
    total_size = 0
    for idx, (filename, content) in enumerate(files):
        if len(content) > _MAX_MEDIA_FILE_BYTES:
            await _gc()
            raise LandingError(f"文件 {filename} 超过单文件 200MB 上限")
        total_size += len(content)
        if total_size > MAX_MATERIALIZE_BYTES:
            await _gc()
            raise LandingError("本批文件总体积超过上限,无法加工")
        fmt = Path(filename).suffix.lstrip(".").lower()
        base = Path(filename).name
        local_name = f"{start_idx + idx:06d}-{base}"
        member_key = f"{ver_prefix}{local_name}"
        try:
            await upload_object(
                cfg,
                bucket,
                member_key,
                io.BytesIO(content),
                len(content),
                content_type="application/octet-stream",
            )
        except ExternalStoreError:
            await _gc()
            raise
        new_rows.append(
            {
                # images/audios/videos 存相对 manifest 所在目录的文件名(与
                # manifest.jsonl 必然同前缀,见 land_media_manifest/add_dataset_members
                # 都用 ver_prefix 落成员);__member.key 才是完整对象 key,供
                # 预签名/删除等直接寻址用。见 external_store._materialized_manifest
                # 的解析侧。
                field: [local_name],
                "text": token,
                "__member": {
                    "bucket": bucket,
                    "key": member_key,
                    "name": base,
                    "size": len(content),
                    "format": fmt,
                },
            }
        )

    manifest_rows = existing_manifest_rows + new_rows
    manifest_bytes = (
        "\n".join(json.dumps(r, ensure_ascii=False) for r in manifest_rows) + "\n"
    ).encode("utf-8")
    manifest_key = f"{ver_prefix}manifest.jsonl"
    try:
        await upload_object(
            cfg,
            bucket,
            manifest_key,
            io.BytesIO(manifest_bytes),
            len(manifest_bytes),
            content_type="application/x-ndjson",
        )
    except ExternalStoreError:
        await _gc()
        raise

    media_modalities = [field]
    if version is None:
        version = DatasetVersion(
            id=_new_version_id(),
            dataset_id=dataset_id,
            version_no=version_no,
            storage_uri=f"s3://{bucket}/{manifest_key}",
            format=MANIFEST_FORMAT,
            rows=len(manifest_rows),
            size=total_size,
            origin="managed",
            source_datasource_id=None,
            modalities=media_modalities,
            publish_status="draft",
            note=f"媒体批量接入:{len(files)} 个文件",
        )
        session.add(version)
    else:
        version.storage_uri = f"s3://{bucket}/{manifest_key}"
        version.rows = len(manifest_rows)
        version.size = (version.size or 0) + total_size
        if media_modalities:
            version.modalities = media_modalities
    await session.commit()
    await session.refresh(version)
    return version


async def land_upload_raw(
    session: AsyncSession,
    *,
    content: bytes,
    filename: str,
    source_format: str,
    dataset_id: str | None = None,
    dataset_name: str | None = None,
    data_type: str | None = None,
    semantic_type: str | None = None,
    description: str | None = None,
    creator: str = "admin",
) -> tuple[Dataset, DatasetVersion]:
    """二进制本地上传:原样存储,不解析。版本 rows=None,format=源扩展名。

    `dataset_id` 传入(数据集优先流程)→ 原字节作为 raw 表成员落进该数据集的
    draft 版本(table_name 由文件名派生);不传 → 旧行为(新建 Dataset+v1)。
    二进制无 dict 行 → 不经 apply_semantic_spec(见 docs/plan/14 §3.6);仅按
    显式 semantic_type 或 data_type 默认映射打**版本/数据集级标签**(结构就绪)。
    """
    if dataset_id is not None:
        return await _land_raw_member(
            session,
            dataset_id,
            content=content,
            filename=filename,
            source_format=source_format,
        )
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

    # 单媒体原样存:data_type(image/audio/video)→ 单模态字段(无真实文本 → 单模态子标签)
    dt_key = (data_type or "").lower()
    raw_modalities: list[str] | None = (
        [_DATA_TYPE_TO_MEDIA_FIELD[dt_key]]
        if dt_key in _DATA_TYPE_TO_MEDIA_FIELD
        else None
    )

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
        modalities=raw_modalities,
        produced_by_job_id=None,
        note=f"本地上传(原样存):{filename}",
    )
    session.add(version)
    await session.commit()
    await session.refresh(dataset)
    await session.refresh(version)
    return dataset, version
