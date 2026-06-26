# 采集配置向导 + 源数据预览 + 字段选择（切片 A）实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把"新建采集任务"从单弹窗升级为四步向导，第 3 步源数据采样预览，数据库整表模式支持勾选列（落地只采选中列）。

**Architecture:** 后端新增 `POST /ingest-tasks/preview` 无副作用采样接口（DB 走 LIMIT N、文件走 download+parse、parquet 走 DuckDB）；列裁剪在共享工具 `_build_queries` 单点实现，rerun 与生成数据集双路径自动受益；前端新建改 StepsForm，编辑弹窗不动。

**Tech Stack:** FastAPI + asyncpg/asyncmy（DB）+ DuckDB（parquet 预览）+ minio（对象下载）；React + Ant Design Pro v6 + ProComponents StepsForm。

## Global Constraints

- 后端：Python 3.12+；`uv` 本机 exit 127，测试用 `cd backend && ./.venv/Scripts/python.exe -m pytest`；无热重载，改 `.py` 手动重启 uvicorn。
- 后端测试连 `TEST_DATABASE_URL`（远程 PG `.60:55433/adp_test`），纯单元测试不打 DB。
- 前端：Biome only（无 ESLint/Prettier）；提交前 `npm run lint` + `npx antd lint ./src` 必过；TypeScript strict；写 antd 代码前 `npx antd info <Component>`。
- 约定式提交（commitlint 强制）。
- 不动编辑弹窗、不动 rerun/generate-dataset 路由主体。

**Spec：** `docs/superpowers/specs/2026-06-26-ingest-00-wizard-preview-design.md`

---

### Task 1: `_build_queries` 支持 extract.columns 列裁剪

**Files:**
- Modify: `backend/app/services/connectors/base.py:107-125`（`_build_queries`）
- Test: `backend/tests/unit/test_connectors.py:173`（已有 `_build_queries` 测试区）

**Interfaces:**
- Produces: `_build_queries(extract)` 在 `extract.mode=="table"` 且 `extract.columns` 非空时，每张表查询改为 `SELECT <quoted columns> FROM <quoted table>`；columns 空/缺省维持 `SELECT *`。下游 `pg.fetch_records`/`mysql.fetch_records`/`run_pg_ingest` 无需改动即自动受益。

- [ ] **Step 1: 写失败测试（列裁剪 + 缺省回退）**

追加到 `backend/tests/unit/test_connectors.py`（`test_build_queries_table_empty_raises` 之后）：

```python
def test_build_queries_table_columns_projected():
    """勾选列 → SELECT 投影到选中列(单点裁剪,rerun 与生成数据集双路径受益)。"""
    out = _build_queries(
        {"mode": "table", "tables": ["public.users"], "columns": ["id", "name"]}
    )
    assert out == [("public.users", 'SELECT "id", "name" FROM "public"."users"')]


def test_build_queries_table_no_columns_keeps_star():
    """未勾列 → 维持 SELECT *(向后兼容存量任务)。"""
    out = _build_queries({"mode": "table", "tables": ["t1"]})
    assert out == [("t1", 'SELECT * FROM "t1"')]


def test_build_queries_table_empty_columns_keeps_star():
    """columns 显式空数组 → 等价未勾,维持 SELECT *。"""
    out = _build_queries({"mode": "table", "tables": ["t1"], "columns": []})
    assert out == [("t1", 'SELECT * FROM "t1"')]
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_connectors.py -k "table_columns_projected or no_columns_keeps_star or empty_columns_keeps_star" -v`
Expected: FAIL（`test_build_queries_table_columns_projected` 拿到 `SELECT *` 而非投影）。

- [ ] **Step 3: 改 `_build_queries`**

替换 `backend/app/services/connectors/base.py` 中 `_build_queries` 的 table 分支：

```python
    if mode == "table":
        tables = [t.strip() for t in (extract.get("tables") or []) if t.strip()]
        if not tables:
            raise IngestError("采集对象为表,但未选择任何表")
        columns = [c.strip() for c in (extract.get("columns") or []) if c.strip()]
        if columns:
            col_list = ", ".join(_quote_ident(c) for c in columns)
            return [
                (t, f"SELECT {col_list} FROM {_quote_ident(t)}") for t in tables
            ]
        return [(t, f"SELECT * FROM {_quote_ident(t)}") for t in tables]
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_connectors.py -k "_build_queries or build_quer" -v`
Expected: PASS（全部 `_build_queries` 用例，含新增 3 条 + 既有 sql/path/empty 回归）。

- [ ] **Step 5: 提交**

```bash
cd backend && git add app/services/connectors/base.py tests/unit/test_connectors.py
git commit -m "feat(ingest): _build_queries 整表模式支持 extract.columns 列裁剪"
```

---

### Task 2: IngestExtract schema 增加 columns 字段 + 校验

**Files:**
- Modify: `backend/app/schemas/ingest_task.py:33-61`（`IngestExtract`）
- Test: `backend/tests/unit/test_connectors.py` 或新建 `backend/tests/unit/test_ingest_schemas.py`

**Interfaces:**
- Produces: `IngestExtract.columns: list[str] | None`；校验器拒绝 `columns` 在 sql/path 模式下出现（仅 table 模式有效）。

- [ ] **Step 1: 写失败测试**

新建 `backend/tests/unit/test_ingest_schemas.py`：

```python
"""IngestExtract schema 校验测试(切片 A)。"""

import pytest
from app.schemas.ingest_task import IngestExtract


def test_extract_table_accepts_columns():
    """table 模式接受 columns。"""
    ext = IngestExtract(mode="table", tables=["t1"], columns=["id", "name"])
    assert ext.columns == ["id", "name"]


def test_extract_table_columns_optional():
    """columns 可缺省(存量任务向后兼容)。"""
    ext = IngestExtract(mode="table", tables=["t1"])
    assert ext.columns is None


def test_extract_sql_rejects_columns():
    """sql 模式不接受 columns(列由 SQL 决定)。"""
    with pytest.raises(ValueError, match="columns"):
        IngestExtract(mode="sql", sql="SELECT 1", columns=["id"])


def test_extract_path_rejects_columns():
    """path 模式不接受 columns。"""
    with pytest.raises(ValueError, match="columns"):
        IngestExtract(mode="path", paths=["a.jsonl"], columns=["id"])
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_ingest_schemas.py -v`
Expected: FAIL（`columns` 字段不存在 → 构造报错；reject 用例因无校验不抛错）。

- [ ] **Step 3: 改 schema**

在 `backend/app/schemas/ingest_task.py` 的 `IngestExtract` 类加字段与校验：

```python
    columns: list[str] | None = None

    @model_validator(mode="after")
    def _check_mode_fields(self) -> IngestExtract:
        """校验 mode 与对应字段一致(空值不在此强制,留给连接器运行期诚实失败)。"""
        if self.mode == "table" and self.sql:
            raise ValueError("extract.mode=table 时不应携带 sql")
        if self.mode == "sql" and self.tables:
            raise ValueError("extract.mode=sql 时不应携带 tables")
        if self.mode == "path" and (self.tables or self.sql):
            raise ValueError("extract.mode=path 时不应携带 tables/sql")
        if self.mode != "path" and (self.paths or self.glob):
            raise ValueError("paths/glob 仅在 extract.mode=path 时有效")
        if self.columns and self.mode != "table":
            raise ValueError(
                "extract.columns 仅在 mode=table 时有效"
                "(sql 由语句决定列,path 为半结构化记录)"
            )
        return self
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_ingest_schemas.py -v`
Expected: PASS（4 条全过）。

- [ ] **Step 5: 提交**

```bash
cd backend && git add app/schemas/ingest_task.py tests/unit/test_ingest_schemas.py
git commit -m "feat(ingest): IngestExtract 增加 columns 字段(table 模式列裁剪)"
```

---

### Task 3: 预览采样模块 preview.py（纯函数 + DB/文件采样器）

**Files:**
- Create: `backend/app/services/preview.py`
- Test: `backend/tests/unit/test_preview.py`

**Interfaces:**
- Consumes: `external_store.download_to_temp`/`stat_object`/`list_objects`（文件下载/列目录大小护栏）、`objectstore._keys_from_extract`（S3 key 匹配）、`pg._connect`/`mysql._connect`（DB 建连）、`base._build_queries`（DB 查询编排）。
- Produces:
  - `PREVIEW_SAMPLE_ROWS = 50`（常量）
  - `infer_columns(rows: list[dict]) -> list[dict]`：`[{name, type}]`，type 由首非空值 Python 类型推断
  - `preview_db(connect_fn, cfg, extract) -> dict`：DB 采样（LIMIT N 包子查询），返回 `{columns, rows, truncated, sampledFrom}`
  - `preview_file(datasource, extract) -> dict`：文件采样（list+match→取首文件→stat 护栏→download→按格式 parse），返回同形
  - 内部纯解析：`_parse_jsonl_head(text, n)`、`_parse_csv_head(text, n)`、`_parse_parquet_head(path, n)`

- [ ] **Step 1: 写失败测试（纯函数：列推断 + 三格式解析头部）**

新建 `backend/tests/unit/test_preview.py`：

```python
"""预览采样纯函数测试(切片 A)。

DB/文件采样器需真实驱动/MinIO,这里只测纯解析与列推断逻辑。
"""

from app.services.preview import (
    PREVIEW_SAMPLE_ROWS,
    _infer_columns,
    _parse_csv_head,
    _parse_jsonl_head,
)


def test_sample_rows_is_50():
    assert PREVIEW_SAMPLE_ROWS == 50


def test_infer_columns_type_from_first_non_null():
    """列类型由首非空值推断(跨 PG/MySQL 统一,避免驱动类型 API 差异)。"""
    rows = [
        {"id": None, "name": "a", "ok": True},
        {"id": 1, "name": "b", "ok": False},
    ]
    cols = {c["name"]: c["type"] for c in _infer_columns(rows)}
    assert cols["id"] == "integer"   # 取首非空 → int
    assert cols["name"] == "text"
    assert cols["ok"] == "boolean"


def test_infer_columns_preserves_first_seen_order():
    rows = [{"b": 1, "a": 2}]
    names = [c["name"] for c in _infer_columns(rows)]
    assert names == ["b", "a"]


def test_infer_columns_empty():
    assert _infer_columns([]) == []


def test_parse_jsonl_head_limit():
    text = "\n".join('{"i": %d}' % i for i in range(100))
    rows, truncated = _parse_jsonl_head(text, 50)
    assert len(rows) == 50
    assert truncated is True
    assert rows[0] == {"i": 0}


def test_parse_csv_head():
    text = "id,name\n1,a\n2,b\n"
    rows, truncated = _parse_csv_head(text, 50)
    assert rows == [{"id": "1", "name": "a"}, {"id": "2", "name": "b"}]
    assert truncated is False
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_preview.py -v`
Expected: FAIL（模块/函数不存在，ImportError）。

- [ ] **Step 3: 实现 preview.py**

创建 `backend/app/services/preview.py`：

```python
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
    for r in reader:
        if len(rows) >= n:
            truncated = True
            break
        rows.append(dict(r))
    return rows, truncated


def _parse_parquet_head(path: Path, n: int) -> tuple[list[dict], bool]:
    import duckdb  # noqa: PLC0415

    con = duckdb.connect()
    rel = con.execute(f"SELECT * FROM read_parquet('{path}') LIMIT {n + 1}")
    cols = [d[0] for d in rel.description]
    fetched = rel.fetchall()
    truncated = len(fetched) > n
    rows = [dict(zip(cols, row, strict=False)) for row in fetched[:n]]
    con.close()
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
    if ext == "parquet":
        rows, _ = _parse_parquet_head(path, PREVIEW_SAMPLE_ROWS)
    else:
        text = path.read_text(encoding="utf-8", errors="replace")
        if ext == "jsonl":
            rows, _ = _parse_jsonl_head(text, PREVIEW_SAMPLE_ROWS)
        elif ext == "csv":
            rows, _ = _parse_csv_head(text, PREVIEW_SAMPLE_ROWS)
        else:
            raise ValueError(f"暂不支持预览的文件格式:{ext}")
    return _to_result(rows, key)


def _parse_bytes(data: bytes, key: str) -> dict[str, Any]:
    import tempfile  # noqa: PLC0415

    ext = key.rsplit(".", 1)[-1].lower() if "." in key else ""
    if ext == "parquet":
        with tempfile.NamedTemporaryFile(suffix=".parquet", delete=False) as f:
            f.write(data)
            f.flush()
            rows, _ = _parse_parquet_head(Path(f.name), PREVIEW_SAMPLE_ROWS)
    else:
        text = data.decode("utf-8", errors="replace")
        if ext == "jsonl":
            rows, _ = _parse_jsonl_head(text, PREVIEW_SAMPLE_ROWS)
        elif ext == "csv":
            rows, _ = _parse_csv_head(text, PREVIEW_SAMPLE_ROWS)
        else:
            raise ValueError(f"暂不支持预览的文件格式:{ext}")
    return _to_result(rows, key)
```

> **注：** `_preview_hdfs` 复用 `HdfsConnector` 实例的 `_namenode`/`_extra_params`/`_get`（`_get` 用 httpx，不可达抛 `ConnectorNotReady` → 路由兜底 4xx）+ 模块级 `_build_webhdfs_url`，与现有 hdfs 连接器请求方式一致（已核 `hdfs.py:168-201`）。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_preview.py -v`
Expected: PASS（纯函数测试全过：列推断 + jsonl/csv 解析 + 常量）。

- [ ] **Step 5: 提交**

```bash
cd backend && git add app/services/preview.py tests/unit/test_preview.py
git commit -m "feat(ingest): 源数据预览采样模块 preview.py(DB/文件采样 + 纯解析)"
```

---

### Task 4: POST /ingest-tasks/preview 路由

**Files:**
- Modify: `backend/app/api/v1/ingest_tasks.py`（新增路由，在 `generate_dataset` 路由之前插入）
- Test: `backend/tests/unit/test_preview_route.py`

**Interfaces:**
- Consumes: `preview.preview_db`/`preview_file`、`DataSource` 模型、`pg._connect`/`mysql._connect`、`ConnectorNotReady`/`IngestError`。
- Produces: `POST /api/v1/ingest-tasks/preview`，body `{datasourceId, extract}`，响应 `{data:{columns,rows,truncated,sampledFrom}, success:true}`；失败 4xx + message。

- [ ] **Step 1: 写失败测试（派发 + 诚实失败，monkeypatch 采样器免真实驱动）**

新建 `backend/tests/unit/test_preview_route.py`：

```python
"""preview 路由派发测试(切片 A):用 monkeypatch 替换采样器,免真实 DB/MinIO。"""

import pytest
from app.api.v1.ingest_tasks import router
from app.services.connectors.base import ConnectorNotReady, IngestError
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient


@pytest.fixture
def app():
    f = FastAPI()
    f.include_router(router)
    return f


async def _post(app, body):
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        return await c.post("/api/v1/ingest-tasks/preview", json=body)


async def test_preview_unsupported_type_400(app, monkeypatch, session_deps):
    """非 DB/文件类型 → 400(不伪造)。"""
    # 注入一个 api 数据源
    from app.models.datasource import DataSource

    # 见 conftest 的 session_deps / 数据源工厂;若无,用现有 test_ingest_tasks 的夹具
    resp = await _post(app, {"datasourceId": "ds_api_x", "extract": {"mode": "path"}})
    assert resp.status_code in (400, 404)


async def test_preview_db_missing_extract_400(app, monkeypatch):
    """未配采集对象 → 400。"""

    async def boom(*a, **k):
        raise IngestError("未配置采集对象")

    monkeypatch.setattr(
        "app.services.preview.preview_db", lambda *a, **k: _raise(boom())
    )
    # 数据库类型但 extract 为空 → _build_queries 抛 IngestError → 400
    resp = await _post(
        app,
        {"datasourceId": "ds_pg_x", "extract": {"mode": "table", "tables": []}},
    )
    assert resp.status_code == 400


def _raise(exc):
    raise exc
```

> **注：** `session_deps`/数据源工厂若 conftest 未提供，执行时参考 `backend/tests/test_ingest_tasks.py` 现有的数据源创建方式（grep `DataSource(` 定位夹具），保持一致。路由测试以"派发到采样器 + 采样器抛错→4xx"为主，真实采样在 Task 3 纯函数已覆盖。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_preview_route.py -v`
Expected: FAIL（路由不存在 → 404 而非预期行为）。

- [ ] **Step 3: 实现路由**

在 `backend/app/api/v1/ingest_tasks.py` 的 `generate_dataset` 路由（`@router.post("/ingest-tasks/{task_id}/generate-dataset")`）之前插入：

```python
@router.post("/ingest-tasks/preview")
async def preview_ingest_source(
    payload: dict,
    session: SessionDep,
) -> Response:
    """源数据预览(切片 A):无副作用采样,不建任务/不落地/不建 job。

    - database(PG族/GoldenDB):SELECT * FROM (查询) LIMIT 50;多表取首张
    - s3/hdfs:首个匹配文件按 jsonl/csv/parquet 解析头部(parquet 走 DuckDB)
    - api / 不支持类型 / 未配采集对象 → 4xx 诚实失败
    """
    datasource_id = payload.get("datasourceId") or payload.get("datasource_id")
    extract = payload.get("extract") or {}
    datasource = await session.get(DataSource, datasource_id or "")
    if datasource is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "数据源不存在"},
        )

    from app.services.preview import preview_db, preview_file  # noqa: PLC0415

    try:
        if datasource.type == "database":
            db_kind = (datasource.db_kind or "").lower()
            if db_kind in _PG_KINDS:
                from app.services.connectors.pg import _connect  # noqa: PLC0415
            elif db_kind == "goldendb":
                from app.services.connectors.mysql import _connect  # noqa: PLC0415
            else:
                return JSONResponse(
                    status_code=400,
                    content={
                        "success": False,
                        "message": f"数据库品牌「{datasource.db_kind}」暂不支持预览",
                    },
                )
            data = await preview_db(_connect, datasource.config or {}, extract)
        elif datasource.type in ("s3", "hdfs"):
            data = await preview_file(datasource, extract)
        else:
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "message": f"数据源类型「{datasource.type}」暂不支持预览",
                },
            )
    except ConnectorNotReady as exc:
        return JSONResponse(
            status_code=400, content={"success": False, "message": str(exc)}
        )
    except (IngestError, ValueError) as exc:
        return JSONResponse(
            status_code=400, content={"success": False, "message": str(exc)}
        )

    return JSONResponse(content={"data": data, "success": True})
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_preview_route.py -v`
Expected: PASS。

- [ ] **Step 5: 跑全量回归**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/ -q`
Expected: PASS（无回归）。

- [ ] **Step 6: 提交**

```bash
cd backend && git add app/api/v1/ingest_tasks.py tests/unit/test_preview_route.py
git commit -m "feat(ingest): 新增 POST /ingest-tasks/preview 无副作用采样路由"
```

---

### Task 5: 前端 typings + api.ts 预览接口

**Files:**
- Modify: `frontend/src/services/data-platform/typings.d.ts:97`（`IngestExtract`）+ 新增 preview 响应类型
- Modify: `frontend/src/services/data-platform/api.ts`（新增 `previewIngestSource`）

**Interfaces:**
- Produces: `DataPlatform.IngestExtract.columns?: string[]`；`DataPlatform.IngestSourcePreview` 类型；`previewIngestSource({datasourceId, extract})` 函数。

- [ ] **Step 1: typings 加 columns + preview 类型**

在 `typings.d.ts:97` 的 `IngestExtract` 加字段（先 `npx antd info` 无关，这是类型；改完跑 `npm run tsc`）。在 `extract` 定义内追加：

```typescript
    columns?: string[]; // table 模式勾选的列(裁剪落地);仅整表模式
```

并在文件内 `IngestTaskCreate` 附近新增：

```typescript
  type IngestPreviewColumn = { name: string; type: string };
  type IngestSourcePreview = {
    columns: IngestPreviewColumn[];
    rows: Record<string, any>[];
    truncated: boolean;
    sampledFrom: string;
  };
```

- [ ] **Step 2: api.ts 加 previewIngestSource**

在 `api.ts` 的 `listDatasourceTables`（:193）之后追加：

```typescript
/** 源数据预览（采集配置期采样，无副作用）POST /api/v1/ingest-tasks/preview */
export async function previewIngestSource(
  body: { datasourceId: string; extract: DataPlatform.IngestExtract },
  options?: { [key: string]: any },
) {
  return request<{
    data: DataPlatform.IngestSourcePreview;
    success: boolean;
  }>('/api/v1/ingest-tasks/preview', {
    method: 'POST',
    data: body,
    ...(options || {}),
  });
}
```

- [ ] **Step 3: 类型检查 + lint**

Run: `cd frontend && npm run tsc`
Expected: 无错误。

Run: `cd frontend && npx biome check src/services/data-platform/api.ts src/services/data-platform/typings.d.ts --write`
Expected: 无错误（Biome 自动格式化）。

- [ ] **Step 4: 提交**

```bash
cd frontend && git add src/services/data-platform/api.ts src/services/data-platform/typings.d.ts
git commit -m "feat(ingest): typings/api 增加 extract.columns 与 previewIngestSource"
```

---

### Task 6: SourcePreview 组件（预览表格 + 整表勾列）

**Files:**
- Create: `frontend/src/pages/ingest/tasks/components/SourcePreview.tsx`
- Test: `frontend/src/pages/ingest/tasks/components/SourcePreview.test.tsx`

**Interfaces:**
- Consumes: `previewIngestSource`、props `{ datasourceId, extract, mode, onColumnsChange }`。
- Produces: 渲染样本表格；`mode==='table'` 时列头 checkbox 勾选（默认全选），`onColumnsChange(选中列名[])` 回传；预览失败显示错误 + 重试。

- [ ] **Step 1: 写失败测试**

新建 `SourcePreview.test.tsx`：

```tsx
import { render, screen, waitFor, fireEvent } from '@testing-library/react';
import { SourcePreview } from './SourcePreview';

jest.mock('@/services/data-platform/api', () => ({
  previewIngestSource: jest.fn(),
}));

const { previewIngestSource } = require('@/services/data-platform/api');

it('整表模式渲染样本并默认全选列,取消勾选回传', async () => {
  (previewIngestSource as jest.Mock).mockResolvedValue({
    data: {
      columns: [
        { name: 'id', type: 'integer' },
        { name: 'name', type: 'text' },
      ],
      rows: [{ id: 1, name: 'a' }],
      truncated: false,
      sampledFrom: 'public.users',
    },
    success: true,
  });
  const onColumnsChange = jest.fn();
  render(
    <SourcePreview
      datasourceId="ds1"
      extract={{ mode: 'table', tables: ['public.users'] }}
      mode="table"
      onColumnsChange={onColumnsChange}
    />,
  );
  await waitFor(() => expect(screen.getByText('id')).toBeInTheDocument());
  // 默认全选 → 回传 ['id','name']
  expect(onColumnsChange).toHaveBeenLastCalledWith(['id', 'name']);
  // 取消 name 勾选
  fireEvent.click(screen.getByLabelText('name'));
  expect(onColumnsChange).toHaveBeenLastCalledWith(['id']);
});

it('非 table 模式只读预览(无勾选 UI)', async () => {
  (previewIngestSource as jest.Mock).mockResolvedValue({
    data: {
      columns: [{ name: 'count', type: 'integer' }],
      rows: [{ count: 5 }],
      truncated: false,
      sampledFrom: '<sql>',
    },
    success: true,
  });
  render(
    <SourcePreview
      datasourceId="ds1"
      extract={{ mode: 'sql', sql: 'SELECT 5 AS count' }}
      mode="sql"
    />,
  );
  await waitFor(() => expect(screen.getByText('count')).toBeInTheDocument());
  expect(screen.queryByLabelText('count')).toBeNull(); // 只读,无 checkbox
});
```

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx jest SourcePreview.test`
Expected: FAIL（组件不存在）。

- [ ] **Step 3: 写组件**

> 先 `npx antd info Table` + `npx antd info Alert` 确认 v6 API（勾选用 Table `rowSelection` 不适用列头，列头 checkbox 用自定义 `title` render + 受控 `columnSelected` 状态）。

创建 `SourcePreview.tsx`：

```tsx
import { useEffect, useState } from 'react';
import { Alert, Button, Table, Tag, message } from 'antd';
import { previewIngestSource } from '@/services/data-platform/api';
import type { DataPlatform } from '@/services/data-platform/typings';

interface Props {
  datasourceId: string;
  extract: DataPlatform.IngestExtract;
  /** table=可勾列;其他=只读预览 */
  mode?: string;
  onColumnsChange?: (cols: string[]) => void;
}

export function SourcePreview({
  datasourceId,
  extract,
  mode = 'table',
  onColumnsChange,
}: Props) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [preview, setPreview] =
    useState<DataPlatform.IngestSourcePreview | null>(null);
  const [selected, setSelected] = useState<string[]>([]);

  const fetchPreview = async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await previewIngestSource({ datasourceId, extract });
      setPreview(res.data);
      const all = (res.data.columns || []).map((c) => c.name);
      setSelected(all);
      onColumnsChange?.(all);
    } catch (e: any) {
      const msg = e?.data?.message ?? e?.message ?? '预览失败';
      setError(msg);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    fetchPreview();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [datasourceId, JSON.stringify(extract)]);

  const toggle = (name: string) => {
    const next = selected.includes(name)
      ? selected.filter((c) => c !== name)
      : [...selected, name];
    // 保持预览列顺序
    const ordered = (preview?.columns || [])
      .map((c) => c.name)
      .filter((c) => next.includes(c));
    setSelected(ordered);
    onColumnsChange?.(ordered);
  };

  if (error) {
    return (
      <Alert
        type="error"
        message="预览失败"
        description={error}
        action={
          <Button size="small" onClick={fetchPreview}>
            重试
          </Button>
        }
      />
    );
  }

  const selectable = mode === 'table';
  const columns = (preview?.columns || []).map((c) => ({
    title: selectable ? (
      <label>
        <input
          type="checkbox"
          checked={selected.includes(c.name)}
          aria-label={c.name}
          onChange={() => toggle(c.name)}
        />{' '}
        {c.name} <Tag>{c.type}</Tag>
      </label>
    ) : (
      <span>
        {c.name} <Tag>{c.type}</Tag>
      </span>
    ),
    dataIndex: c.name,
  }));

  return (
    <div>
      {preview?.truncated && (
        <Alert
          type="info"
          showIcon
          message={`仅展示前 50 行(取样自 ${preview.sampledFrom})`}
          style={{ marginBottom: 8 }}
        />
      )}
      <Table
        size="small"
        loading={loading}
        rowKey={(_, i) => String(i)}
        columns={columns}
        dataSource={preview?.rows || []}
        scroll={{ x: 'max-content' }}
        pagination={false}
      />
    </div>
  );
}
```

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npx jest SourcePreview.test`
Expected: PASS。

- [ ] **Step 5: lint**

Run: `cd frontend && npx biome check src/pages/ingest/tasks/components/SourcePreview.tsx --write && npx antd lint src/pages/ingest/tasks/components/SourcePreview.tsx`
Expected: 无错误。

- [ ] **Step 6: 提交**

```bash
cd frontend && git add src/pages/ingest/tasks/components/SourcePreview.tsx src/pages/ingest/tasks/components/SourcePreview.test.tsx
git commit -m "feat(ingest): SourcePreview 组件(样本预览 + 整表勾列)"
```

---

### Task 7: 新建任务改 StepsForm 四步向导

**Files:**
- Modify: `frontend/src/pages/ingest/tasks/index.tsx:508-527`（"新建任务" `ModalForm` → `StepsForm`；编辑 `ModalForm:531` 不动）
- Modify: `frontend/src/pages/ingest/tasks/index.test.tsx`（向导流转测试）

**Interfaces:**
- Consumes: Task 5 的 `previewIngestSource`、Task 6 的 `SourcePreview`、现有 `taskFormFields`（步骤 1-2/4 的字段块）。
- Produces: 新建走 4 步（基本信息 → 采集对象 → 预览与字段 → 落地确认），第 3 步渲染 SourcePreview 并把选中列写入 `extract.columns`，提交 `createIngestTask`。

- [ ] **Step 1: 写失败测试（向导四步流转）**

在 `tasks/index.test.tsx` 追加（参考现有测试的 mock/渲染模式）：

```tsx
it('新建任务走四步向导,table 模式预览勾列写入 extract.columns', async () => {
  // mock previewIngestSource 返回列
  // 渲染列表 → 点「新建任务」→ 断言 StepsForm 出现 4 步
  // 填步骤1/2 → 进步骤3 → 断言 SourcePreview 渲染 → 取消某列 → 提交
  // 断言 createIngestTask 收到 extract.columns 不含被取消列
});
```

> **注：** `index.test.tsx` 现有 mock 结构需先 Read 确认（执行时 `Read frontend/src/pages/ingest/tasks/index.test.tsx`），沿用其 listIngestTasks/createIngestTask mock 方式。测试断言核心 intent："勾列 → extract.columns 只含选中列"。

- [ ] **Step 2: 跑测试确认失败**

Run: `cd frontend && npx jest tasks/index.test`
Expected: FAIL（仍是 ModalForm，无 StepsForm）。

- [ ] **Step 3: 改造为 StepsForm**

> 先 `npx antd info StepsForm`（ProComponents）确认 v6 步骤表单 API。

在 `index.tsx:508` 把新建 `ModalForm` 替换为 `StepsForm`（保留 `trigger`/`onFinish`）。核心结构：

```tsx
import { StepsForm } from '@ant-design/pro-components';
import { SourcePreview } from './components/SourcePreview';

// toolBarRender 内:
<StepsForm<DataPlatform.IngestTaskCreate>
  key="create"
  title="新建采集任务"
  trigger={<Button type="primary">新建任务</Button>}
  modalProps={{ destroyOnHidden: true }}
  initialValues={{ schedule: { mode: 'once' } }}
  onFinish={async (values) => {
    try {
      await createIngestTask(values);
      message.success('采集任务创建成功');
      actionRef.current?.reload();
      return true;
    } catch {
      message.error('创建失败，请重试');
      return false;
    }
  }}
>
  <StepsForm.StepForm name="base" title="基本信息">
    {/* 任务名、分类、数据源 —— 取 taskFormFields 的前 3 个字段 */}
  </StepsForm.StepForm>
  <StepsForm.StepForm name="object" title="采集对象">
    {/* 调度 + 采集对象(按数据源类型) —— taskFormFields 的 schedule/extract 块 */}
  </StepsForm.StepForm>
  <StepsForm.StepForm name="preview" title="预览与字段">
    <ProFormDependency name={[['datasourceId'], ['extract']]}>
      {({ datasourceId, extract }) =>
        datasourceId && extract ? (
          <SourcePreview
            datasourceId={datasourceId}
            extract={extract}
            mode={extract?.mode}
            onColumnsChange={(cols) => {
              // 写入表单 extract.columns(整表模式才有效)
              formRef.current?.setFieldValue(['extract', 'columns'], cols);
            }}
          />
        ) : (
          <Typography.Text type="secondary">
            请先在前两步选择数据源与采集对象
          </Typography.Text>
        )
      }
    </ProFormDependency>
  </StepsForm.StepForm>
  <StepsForm.StepForm name="confirm" title="落地确认">
    {/* 过滤算子(FilterOperatorPicker) + 调度 + 配置概要只读 */}
  </StepsForm.StepForm>
</StepsForm>
```

> **要点：**
> - 把现有 `taskFormFields`（`index.tsx:200-330`）拆到对应 StepForm：基本信息=任务名/分类/数据源；采集对象=schedule+按类型 extract；落地确认=过滤算子。
> - 第 3 步用 `formRef`（`StepsForm` 的 `formRef`）把勾选列写回 `extract.columns`；仅整表模式 SourcePreview 显示勾选 UI。
> - 编辑入口（`ModalForm:531`）**保持不动**。

- [ ] **Step 4: 跑测试确认通过**

Run: `cd frontend && npx jest tasks/index.test`
Expected: PASS。

- [ ] **Step 5: 全量 lint**

Run: `cd frontend && npm run lint`
Expected: 无错误（Biome + tsc + antd lint）。

- [ ] **Step 6: 提交**

```bash
cd frontend && git add src/pages/ingest/tasks/index.tsx src/pages/ingest/tasks/index.test.tsx
git commit -m "feat(ingest): 新建采集任务改四步向导(预览+字段选择),编辑弹窗不变"
```

---

## 完成验证（全切片）

- [ ] 后端：`cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/ -q` 全绿。
- [ ] 前端：`cd frontend && npm run lint` 全绿。
- [ ] 手测：`/adp-start` 起服务 → 新建采集任务走四步 → 第 3 步看到样本 → 整表模式勾列 → 运行 → 详情产物只含选中列对应数据。
- [ ] 回归：编辑采集任务仍走旧弹窗；生成数据集对勾列任务也只采选中列（经 `_build_queries` 自动受益）。

## Self-Review 结论

- **Spec 覆盖：** 向导四步（Task 7）、preview 接口契约 DB/jsonl-csv/parquet（Task 3-4）、整表勾列落地单点改 `_build_queries`（Task 1）双路径受益（spec ③）、编辑不动（Task 7 注）、测试编码 intent（各 Task Step 1）——均覆盖。
- **类型一致：** `extract.columns` 后端（Task 2 `list[str]|None`）↔ 前端（Task 5 `string[]`）↔ 落地（Task 1 读 `extract.get("columns")`）一致；preview 响应 `columns/rows/truncated/sampledFrom` 前后端一致。
- **已知待执行时确认项（非占位）：** Task 4 路由测试的数据源夹具（沿用 `test_ingest_tasks.py` 现有方式，grep `DataSource(` 定位）；Task 7 测试的现有 mock 结构（先 Read `index.test.tsx`）。其余签名（`_build_webhdfs_url`/`HdfsConnector._namenode`/`download_to_temp`/`stat_object`/`_keys_from_extract`）均已核对。
