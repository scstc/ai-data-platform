"""采集配置期源数据预览(切片 A):无副作用采样。

供 POST /ingest-tasks/preview 复用:
- preview_db   : 数据库整表/SQL → SELECT * FROM (...) LIMIT N
- preview_file : S3/HDFS 首个匹配文件 → 下载 → 按 jsonl/csv/parquet 解析头部
列类型统一由样本首非空值推断(跨 PG/MySQL 驱动一致,避免类型名 API 差异)。
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any, Awaitable, Callable

PREVIEW_SAMPLE_ROWS = 50
MAX_MATERIALIZE_BYTES = 64 * 1024 * 1024  # 64 MiB 护栏,与 external_store 对齐

# Python 类型 → 展示标签
_TYPE_LABELS = {
    bool: "boolean",
    int: "integer",
    float: "float",
    str: "text",
}


def _infer_columns(rows: list[dict[str, Any]]) -> list[dict[str, str]]:
    """由样本行推断列(名+类型)。列序=首见序;类型=该列首非空值类型。"""
    order: list[str] = []
    seen: set[str] = set()
    for r in rows:
        for k in r:
            if k not in seen:
                seen.add(k)
                order.append(k)
    col_type: dict[str, str] = {}
    for k in order:
        for r in rows:
            v = r.get(k)
            if v is not None:
                col_type[k] = _TYPE_LABELS.get(type(v), "text")
                break
        else:
            col_type[k] = "text"  # 全空列兜底
    return [{"name": k, "type": col_type[k]} for k in order]


def _parse_jsonl_head(text: str, n: int) -> tuple[list[dict], bool]:
    rows: list[dict] = []
    truncated = False
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        if len(rows) >= n:
            truncated = True
            break
        rows.append(json.loads(line))
    return rows, truncated


def _parse_csv_head(text: str, n: int) -> tuple[list[dict], bool]:
    reader = csv.DictReader(io.StringIO(text))
    rows = []
    truncated = False
    try:
        for r in reader:
            if len(rows) >= n:
                truncated = True
                break
            rows.append(dict(r))
    except csv.Error as exc:
        raise ValueError(f"CSV 解析失败:{exc}") from exc
    return rows, truncated


def _parse_parquet_head(path: Path, n: int) -> tuple[list[dict], bool]:
    import duckdb  # noqa: PLC0415

    con = duckdb.connect()
    try:
        rel = con.execute(f"SELECT * FROM read_parquet('{path}') LIMIT {n + 1}")
        cols = [d[0] for d in rel.description]
        fetched = rel.fetchall()
        truncated = len(fetched) > n
        rows = [dict(zip(cols, row, strict=False)) for row in fetched[:n]]
    finally:
        con.close()  # execute/fetchall 抛错也必须释放连接
    return rows, truncated


def _to_result(rows: list[dict], sampled_from: str) -> dict[str, Any]:
    n = PREVIEW_SAMPLE_ROWS
    truncated = len(rows) > n
    return {
        "columns": _infer_columns(rows),
        "rows": rows[:n],
        "truncated": truncated,
        "sampledFrom": sampled_from,
    }


async def preview_db(
    connect_fn: Callable[..., Awaitable[Any]],
    cfg: dict[str, Any],
    extract: dict[str, Any],
) -> dict[str, Any]:
    """数据库采样:对 extract 第一条查询包 LIMIT N 子查询。

    connect_fn: pg._connect / mysql._connect(均返回连接对象,具 fetch 或 cursor)。
    多表/多查询只取第 1 条预览(标注 sampledFrom)。
    """
    from app.services.connectors.base import _build_queries  # noqa: PLC0415

    queries = _build_queries(extract)  # 复用列裁剪后的查询
    suffix, query = queries[0]
    sampled_from = suffix or "<sql>"
    wrapped = f"SELECT * FROM ({query}) AS _preview LIMIT {PREVIEW_SAMPLE_ROWS + 1}"
    conn = await connect_fn(cfg)
    try:
        # asyncpg: 有 .fetch;asyncmy: 走 cursor。两者都尝试,cursor 路径取列名。
        if hasattr(conn, "fetch"):
            records = await conn.fetch(wrapped)
            rows = [dict(r) for r in records]
        else:
            async with conn.cursor() as cur:
                await cur.execute(wrapped)
                cols = [d[0] for d in cur.description]
                raw = await cur.fetchall()
            rows = [dict(zip(cols, r, strict=False)) for r in raw]
    finally:
        closer = getattr(conn, "close", None)
        if closer:
            maybe = closer()
            await maybe if _is_awaitable(maybe) else None
    return _to_result(rows, sampled_from)


def _is_awaitable(obj: Any) -> bool:
    import inspect  # noqa: PLC0415

    return inspect.isawaitable(obj)


async def preview_file(datasource: Any, extract: dict[str, Any]) -> dict[str, Any]:
    """对象存储采样:list 匹配 → 取首个文件 → 大小护栏 → 下载 → 按格式解析。

    datasource.type=s3 走 minio;type=hdfs 走 WebHDFS OPEN。
    """
    dstype = datasource.type
    cfg = datasource.config or {}
    if dstype == "s3":
        return await _preview_s3(cfg, extract)
    if dstype == "hdfs":
        return await _preview_hdfs(cfg, extract)
    raise ValueError(f"暂不支持预览的数据源类型:{dstype}")


async def _preview_s3(cfg: dict[str, Any], extract: dict[str, Any]) -> dict[str, Any]:
    from app.services.connectors.objectstore import (  # noqa: PLC0415
        _bucket_from_config,
        _keys_from_extract,
    )
    from app.services.external_store import (  # noqa: PLC0415
        ExternalStoreError,
        download_to_temp,
        list_objects,
        stat_object,
    )

    bucket = _bucket_from_config(cfg)
    prefix = str(cfg.get("prefix") or "").strip()
    all_objects = await list_objects(cfg, bucket, prefix)
    keys = _keys_from_extract(extract, all_objects)
    if not keys:
        raise ValueError("未匹配到任何对象,请检查 paths/glob")
    key = keys[0]
    try:
        meta = await stat_object(cfg, bucket, key)
    except ExternalStoreError as exc:
        raise ValueError(f"获取对象信息失败:{exc}") from exc
    if int(meta.get("size", 0)) > MAX_MATERIALIZE_BYTES:
        raise ValueError(
            f"对象 {key} 过大({meta.get('size')} 字节),暂不支持预览"
        )
    tmp = await download_to_temp(cfg, bucket, key)
    return _parse_file(tmp, key)


async def _preview_hdfs(cfg: dict[str, Any], extract: dict[str, Any]) -> dict[str, Any]:
    from app.services.connectors.hdfs import (  # noqa: PLC0415
        HdfsConnector,
        _build_webhdfs_url,
    )

    connector = HdfsConnector()
    nn = connector._namenode(cfg)  # 缺 namenode → ConnectorNotReady(已 4xx 兜底)
    extra = connector._extra_params(cfg)
    paths = [p.strip() for p in (extract.get("paths") or []) if p.strip()]
    # glob 在 HDFS 需 LISTSTATUS 解析,v1 仅支持显式 paths 首个
    if not paths:
        raise ValueError("HDFS 预览 v1 仅支持 extract.paths 首个文件")
    url = _build_webhdfs_url(nn, paths[0], "OPEN", **extra)
    resp = await connector._get(url)  # httpx GET,不可达 → ConnectorNotReady
    if resp.status_code != 200:
        raise ValueError(
            f"HDFS OPEN 失败:HTTP {resp.status_code},路径 {paths[0]!r}"
        )
    data = resp.content[: MAX_MATERIALIZE_BYTES + 1]
    if len(data) > MAX_MATERIALIZE_BYTES:
        raise ValueError(f"HDFS 文件 {paths[0]} 过大,暂不支持预览")
    return _parse_bytes(data, paths[0])


def _parse_file(path: Path, key: str) -> dict[str, Any]:
    ext = path.suffix.lower().lstrip(".")
    # N+1:让 _to_result 的 len>N 截断判断成立(与 DB 路径 LIMIT N+1 一致)
    if ext == "parquet":
        rows, _ = _parse_parquet_head(path, PREVIEW_SAMPLE_ROWS + 1)
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
        if ext == "jsonl":
            rows, _ = _parse_jsonl_head(text, PREVIEW_SAMPLE_ROWS + 1)
        elif ext == "csv":
            rows, _ = _parse_csv_head(text, PREVIEW_SAMPLE_ROWS + 1)
        else:
            raise ValueError(f"暂不支持预览的文件格式:{ext}")
    return _to_result(rows, key)


def _parse_bytes(data: bytes, key: str) -> dict[str, Any]:
    import tempfile  # noqa: PLC0415

    ext = key.rsplit(".", 1)[-1].lower() if "." in key else ""
    # N+1:让 _to_result 的 len>N 截断判断成立(与 DB 路径 LIMIT N+1 一致)
    if ext == "parquet":
        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as f:
            f.write(data)
            f.flush()
            rows, _ = _parse_parquet_head(Path(f.name), PREVIEW_SAMPLE_ROWS + 1)
    else:
        text = data.decode("utf-8", errors="replace")
        if ext == "jsonl":
            rows, _ = _parse_jsonl_head(text, PREVIEW_SAMPLE_ROWS + 1)
        elif ext == "csv":
            rows, _ = _parse_csv_head(text, PREVIEW_SAMPLE_ROWS + 1)
        else:
            raise ValueError(f"暂不支持预览的文件格式:{ext}")
    return _to_result(rows, key)
