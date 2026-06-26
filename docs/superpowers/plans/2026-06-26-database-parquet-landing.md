# 数据库直连数据以 Parquet 落地 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 数据库直连(PG 族 + GoldenDB)采集的数据集以 Parquet 落地、预览、物化、加工,保留列类型;非数据库来源不变。

**Architecture:** 在现有 `landing.py`(写)/`external_store.py`(对象存储 + 物化)/连接器/`engine.py`(dj 加工)/`datasets.py`(预览)五处加 parquet 分支。核心新增 `records_to_parquet_bytes` + `parquet_bytes_to_records`(pyarrow),并贯穿"尝试 parquet,失败回退 jsonl"的兜底。预览复用已存在的 DuckDB 路径(`_duck_query` 已支持 `read_parquet`)。

**Tech Stack:** Python 3.11、FastAPI、SQLAlchemy async、pyarrow 24.0.0、duckdb 1.5.4、minio、pytest。

## Global Constraints

- 后端无热重载:改 `.py` 后手动重启 uvicorn;本地测试用 venv `pytest`,**勿用 `uv run`**(本机 `uv` exit 127)。
- 测试库:`TEST_DATABASE_URL` 指向 `10.60.1.60:55433/adp_test`(`.119` 已下线),连接慢约 3s,窄范围跑。
- 绝不调用任何 S3 删除(remove_object/remove_bucket)落到外部 hosted 源;平台 `uploads` 桶允许写。
- `DatasetVersion.format` 是 free string,**本特性不需要数据库迁移**。
- 零回归红线:本地上传(jsonl/原样)、媒体 manifest、API 推送路径行为不变。
- 兜底红线:parquet 推断/写失败 → 回退 jsonl 落地并记 `format="jsonl"`,采集照常成功,绝不报错中断。

---

### Task 1: parquet 编解码工具(records ↔ parquet bytes)

**Files:**
- Modify: `backend/app/services/landing.py`(在 `records_to_jsonl_bytes` 之后新增)
- Test: `backend/tests/unit/test_parquet_codec.py`(新建)

**Interfaces:**
- Produces:
  - `records_to_parquet_bytes(records: list[dict]) -> bytes` — pyarrow 推断 schema 写 parquet;空记录或推断失败抛 `ParquetCodecError`。
  - `parquet_bytes_to_records(content: bytes, limit: int = 0) -> list[dict]` — 读 parquet 还原为 dict 列表(`limit>0` 取前 N);供预览/行数/物化复用。
  - `ParquetCodecError(LandingError)` — parquet 编解码失败(供 land_records 捕获兜底)。

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_parquet_codec.py
import datetime
from decimal import Decimal

import pytest

from app.services.landing import (
    ParquetCodecError,
    parquet_bytes_to_records,
    records_to_parquet_bytes,
)


def test_roundtrip_preserves_types():
    records = [
        {"id": 1, "amount": Decimal("100.50"), "d": datetime.date(2026, 1, 1)},
        {"id": 2, "amount": Decimal("200.00"), "d": datetime.date(2026, 1, 2)},
    ]
    blob = records_to_parquet_bytes(records)
    assert isinstance(blob, bytes) and len(blob) > 0
    back = parquet_bytes_to_records(blob)
    assert back[0]["id"] == 1            # 仍是 int,非 "1"
    assert back[1]["d"] == datetime.date(2026, 1, 2)


def test_empty_records_raise():
    with pytest.raises(ParquetCodecError):
        records_to_parquet_bytes([])


def test_heterogeneous_column_raises():
    # 同列类型冲突(int vs dict)pyarrow 无法推断 → 抛 ParquetCodecError
    with pytest.raises(ParquetCodecError):
        records_to_parquet_bytes([{"x": 1}, {"x": {"nested": True}}])


def test_limit_reads_prefix():
    records = [{"i": i} for i in range(10)]
    blob = records_to_parquet_bytes(records)
    assert len(parquet_bytes_to_records(blob, limit=3)) == 3
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_parquet_codec.py -v`
Expected: FAIL — `ImportError: cannot import name 'records_to_parquet_bytes'`.

- [ ] **Step 3: Write minimal implementation**

在 `backend/app/services/landing.py` 的 `records_to_jsonl_bytes` 定义之后新增:

```python
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
    except (pa.ArrowInvalid, pa.ArrowTypeError, pa.ArrowNotImplementedError) as exc:
        raise ParquetCodecError(f"parquet schema 推断失败:{exc}") from exc
    buf = io.BytesIO()
    pq.write_table(table, buf)
    return buf.getvalue()


def parquet_bytes_to_records(content: bytes, limit: int = 0) -> list[dict]:
    """读 parquet 字节还原为 dict 列表(limit>0 取前 N)。供预览/行数/物化复用。"""
    import io

    import pyarrow.parquet as pq

    table = pq.read_table(io.BytesIO(content))
    if limit > 0:
        table = table.slice(0, limit)
    return table.to_pylist()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/unit/test_parquet_codec.py -v`
Expected: PASS(4 passed)。

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/landing.py backend/tests/unit/test_parquet_codec.py
git commit -m "feat(landing): records ↔ parquet 编解码工具(pyarrow,保留列类型)"
```

---

### Task 2: parquet 上传到平台 MinIO

**Files:**
- Modify: `backend/app/services/external_store.py`(在 `upload_jsonl_to_uploads` 之后新增)
- Test: `backend/tests/test_external_store.py`(追加)

**Interfaces:**
- Consumes: `records_to_parquet_bytes`(Task 1,仅测试用)。
- Produces: `upload_parquet_to_uploads(dataset_id: str, version_no: int, parquet_bytes: bytes) -> str` — 上传到 `uploads/<dataset_id>/v<n>/data.parquet`,返回 `s3://<bucket>/<key>`。平台未配置抛 `ExternalStoreError`。

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_external_store.py(追加,沿用本文件既有 monkeypatch 平台配置/上传的风格)
import pytest

from app.services import external_store


@pytest.mark.asyncio
async def test_upload_parquet_to_uploads_key_and_uri(monkeypatch):
    captured = {}

    async def fake_upload_object(cfg, bucket, key, data, length, content_type="application/octet-stream"):
        captured["bucket"] = bucket
        captured["key"] = key
        captured["content_type"] = content_type

    monkeypatch.setattr(
        external_store, "platform_config", lambda: {"endpoint": "e", "accessKey": "a", "secretKey": "s"}
    )
    monkeypatch.setattr(external_store.settings, "storage_minio_upload_bucket", "uploads")
    monkeypatch.setattr(external_store, "upload_object", fake_upload_object)

    uri = await external_store.upload_parquet_to_uploads("dset-abc123", 2, b"PAR1data")
    assert uri == "s3://uploads/dset-abc123/v2/data.parquet"
    assert captured["key"] == "dset-abc123/v2/data.parquet"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_external_store.py::test_upload_parquet_to_uploads_key_and_uri -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'upload_parquet_to_uploads'`.

- [ ] **Step 3: Write minimal implementation**

在 `backend/app/services/external_store.py` 的 `upload_jsonl_to_uploads` 之后新增:

```python
async def upload_parquet_to_uploads(
    dataset_id: str, version_no: int, parquet_bytes: bytes
) -> str:
    """把 parquet 字节上传到平台 MinIO uploads 桶,键 = ``<dataset_id>/v<n>/data.parquet``。

    与 upload_jsonl_to_uploads 同前缀约定(不同版本落不同文件夹)。
    返回 storage_uri(``s3://<bucket>/<key>``)。平台未配置 → ExternalStoreError。
    """
    cfg = platform_config()
    bucket = settings.storage_minio_upload_bucket
    key = f"{dataset_id}/v{version_no}/data.parquet"
    await upload_object(cfg, bucket, key, io.BytesIO(parquet_bytes), len(parquet_bytes))
    return f"s3://{bucket}/{key}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_external_store.py::test_upload_parquet_to_uploads_key_and_uri -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/external_store.py backend/tests/test_external_store.py
git commit -m "feat(external-store): upload_parquet_to_uploads(data.parquet)"
```

---

### Task 3: `land_records` 支持 parquet + 失败回退 jsonl

**Files:**
- Modify: `backend/app/services/landing.py:229-311`(`land_records` 函数)
- Test: `backend/tests/test_landing_parquet.py`(新建)

**Interfaces:**
- Consumes: `records_to_parquet_bytes`(Task 1)、`upload_parquet_to_uploads`(Task 2)。
- Produces: `land_records(..., storage_format: str = "jsonl")` — `"parquet"` 时写 parquet(`format="parquet"`);推断/写失败回退 jsonl(`format="jsonl"`)。默认 `"jsonl"` 行为不变。

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_landing_parquet.py
from decimal import Decimal

import pytest

from app.services import landing


@pytest.mark.asyncio
async def test_land_records_parquet_sets_format(monkeypatch, db_session):
    # db_session: 沿用 conftest 既有 async session fixture
    async def fake_upload_parquet(dataset_id, version_no, blob):
        return f"s3://uploads/{dataset_id}/v{version_no}/data.parquet"

    monkeypatch.setattr(
        "app.services.external_store.upload_parquet_to_uploads", fake_upload_parquet
    )
    ds, ver = await landing.land_records(
        db_session,
        [{"id": 1, "amt": Decimal("9.9")}],
        dataset_name="t",
        source_kind="database",
        storage_format="parquet",
    )
    assert ver.format == "parquet"
    assert ver.storage_uri.endswith("data.parquet")


@pytest.mark.asyncio
async def test_land_records_parquet_falls_back_to_jsonl(monkeypatch, db_session):
    # 嵌套异构 → records_to_parquet_bytes 抛 ParquetCodecError → 回退 jsonl
    async def fake_upload_jsonl(dataset_id, version_no, blob):
        return f"s3://uploads/{dataset_id}/v{version_no}/data.jsonl"

    monkeypatch.setattr(
        "app.services.external_store.upload_jsonl_to_uploads", fake_upload_jsonl
    )
    ds, ver = await landing.land_records(
        db_session,
        [{"x": 1}, {"x": {"nested": True}}],
        dataset_name="t",
        source_kind="database",
        storage_format="parquet",
    )
    assert ver.format == "jsonl"
    assert ver.storage_uri.endswith("data.jsonl")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_landing_parquet.py -v`
Expected: FAIL — `TypeError: land_records() got an unexpected keyword argument 'storage_format'`.

- [ ] **Step 3: Write minimal implementation**

在 `land_records` 签名(`backend/app/services/landing.py:229`)追加参数:

```python
    creator: str = "admin",
    strict_semantic: bool = False,
    storage_format: str = "jsonl",
) -> tuple[Dataset, DatasetVersion]:
```

把当前写盘块(`landing.py:287-306`,从 `jsonl_bytes = records_to_jsonl_bytes(records)` 到构造 `version`)替换为:

```python
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
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_landing_parquet.py -v`
Expected: PASS(2 passed)。

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/landing.py backend/tests/test_landing_parquet.py
git commit -m "feat(landing): land_records 支持 parquet 落地 + 失败回退 jsonl"
```

---

### Task 4: 连接器 + 生成数据集端点改用 parquet

**Files:**
- Modify: `backend/app/services/connectors/pg.py:125`(`run_pg_ingest` 的 `land_records(...)` 调用)
- Modify: `backend/app/services/connectors/mysql.py:199`(`run_ingest` 的 `land_records(...)` 调用)
- Modify: `backend/app/api/v1/ingest_tasks.py:493-518`(`generate-dataset` 落盘块)
- Test: `backend/tests/test_ingest_tasks.py`(追加端到端断言)

**Interfaces:**
- Consumes: `land_records(storage_format="parquet")`(Task 3)、`records_to_parquet_bytes`/`upload_parquet_to_uploads`(Task 1/2)。
- Produces: 数据库采集 / 生成数据集产出的版本 `format="parquet"`(或兜底 jsonl)。

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_ingest_tasks.py(追加;沿用本文件既有的 PG 采集 e2e fixture 与桩)
@pytest.mark.asyncio
async def test_rerun_pg_lands_parquet(monkeypatch, client, pg_datasource_with_table):
    # pg_datasource_with_table: 既有 fixture,建库+表+采集任务,extract 选定表
    ds_id, task_id = pg_datasource_with_table
    resp = await client.post(f"/api/v1/ingest-tasks/{task_id}/rerun")
    assert resp.status_code == 200
    # 取该任务产出版本,断言 format=parquet
    detail = (await client.get(f"/api/v1/ingest-tasks/{task_id}")).json()
    assert detail["data"]["output"], "应产出数据集"
    version_id = detail["data"]["output"][0]["versionId"]
    prev = await client.get(f"/api/v1/dataset-versions/{version_id}/preview")
    assert prev.status_code == 200  # 预览不报错(Task 6 保证 parquet 可预览)
```

> 若现有测试套没有可直连的 PG 表 fixture,改为对 `run_pg_ingest` 做窄单测:monkeypatch `_connect` 返回桩 `conn.fetch` → 已知 records,断言 `land_records` 收到 `storage_format="parquet"`。

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_ingest_tasks.py::test_rerun_pg_lands_parquet -v`
Expected: FAIL — 版本 `format` 仍为 `jsonl`,或预览/断言不符。

- [ ] **Step 3: Write minimal implementation**

`pg.py:125` 的 `land_records(` 调用追加一行参数:

```python
                ds, ver = await land_records(
                    session,
                    records,
                    dataset_name=name,
                    data_type="sql",
                    semantic_type="structured",
                    source_kind="database",
                    note=f"采集落地:{task.name}(来源 {datasource.name})",
                    produced_by_job_id=job_id,
                    storage_format="parquet",
                )
```

`mysql.py:199` 的 `land_records(` 调用同样追加 `storage_format="parquet",`(保持该调用其余参数不变)。

`ingest_tasks.py` 的 `generate-dataset` 落盘块(`ingest_tasks.py:493-518`,从 `jsonl_bytes = records_to_jsonl_bytes(records)` 到登记 `version`)替换为 parquet 优先 + 回退:

```python
    # 3) parquet → 上传平台 MinIO(uploads/<dataset_id>/v<n>/data.parquet);失败回退 jsonl
    from app.services.landing import ParquetCodecError, records_to_parquet_bytes
    from app.services.external_store import upload_parquet_to_uploads

    fmt = "parquet"
    try:
        blob = records_to_parquet_bytes(records)
        storage_uri = await upload_parquet_to_uploads(dataset.id, next_version, blob)
    except ParquetCodecError:
        fmt = "jsonl"
        blob = records_to_jsonl_bytes(records)
        try:
            storage_uri = await upload_jsonl_to_uploads(dataset.id, next_version, blob)
        except ExternalStoreError as exc:
            await session.rollback()
            return JSONResponse(
                status_code=503, content={"success": False, "message": str(exc)}
            )
    except ExternalStoreError as exc:
        await session.rollback()
        return JSONResponse(
            status_code=503, content={"success": False, "message": str(exc)}
        )

    # 4) 登记 hosted 版本(source_datasource_id 留空 → 回退平台 MinIO)
    version = DatasetVersion(
        id=_new_version_id(),
        dataset_id=dataset.id,
        version_no=next_version,
        storage_uri=storage_uri,
        format=fmt,
        rows=len(records),
        size=len(blob),
        origin="hosted",
        source_datasource_id=None,
        semantic_type="structured",
        note=f"采集生成 {fmt}(来源 {datasource.name},v{next_version})",
    )
```

并把该端点响应里的 `fileKey` 由 `f"{dataset.id}/v{next_version}/data.jsonl"` 改为
`f"{dataset.id}/v{next_version}/data.{fmt}"`(`ingest_tasks.py:539`)。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_ingest_tasks.py -k parquet -v`
Expected: PASS。
回归:`./.venv/Scripts/python.exe -m pytest tests/test_ingest_tasks.py -q` 全绿。

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/connectors/pg.py backend/app/services/connectors/mysql.py backend/app/api/v1/ingest_tasks.py backend/tests/test_ingest_tasks.py
git commit -m "feat(ingest): 数据库采集/生成数据集改用 parquet 落地(带 jsonl 回退)"
```

---

### Task 5: 物化支持 parquet(直接喂 dj)

**Files:**
- Modify: `backend/app/services/external_store.py:473-541`(`materialized_version`)
- Test: `backend/tests/test_external_store.py`(追加)

**Interfaces:**
- Consumes: `cached_bytes`(本模块)。
- Produces: `materialized_version` 对 `format="parquet"` 的版本 yield 一个本地 `.parquet` 路径(dj `ParquetFormatter` 原生读),不转 jsonl。

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_external_store.py(追加)
@pytest.mark.asyncio
async def test_materialized_version_parquet_yields_parquet_path(monkeypatch, tmp_path):
    from app.models.dataset_version import DatasetVersion
    from app.services import external_store as es

    blob = es.__dict__  # 占位避免 lint;实际用 Task 1 的 records_to_parquet_bytes
    from app.services.landing import records_to_parquet_bytes

    parquet_bytes = records_to_parquet_bytes([{"id": 1}])

    async def fake_cached_bytes(cfg, bucket, key):
        return parquet_bytes

    monkeypatch.setattr(es, "cached_bytes", fake_cached_bytes)
    monkeypatch.setattr(
        es, "platform_config", lambda: {"endpoint": "e", "accessKey": "a", "secretKey": "s"}
    )
    v = DatasetVersion(
        id="dsv-x", dataset_id="dset-x", version_no=1,
        storage_uri="s3://uploads/dset-x/v1/data.parquet", format="parquet",
        origin="managed",
    )
    async with es.materialized_version(v, session=None) as p:
        assert str(p).endswith(".parquet")
        assert p.exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_external_store.py::test_materialized_version_parquet_yields_parquet_path -v`
Expected: FAIL — 当前对 parquet 会走 `normalize_to_records`(不支持 parquet)抛 `UnsupportedFormatError`。

- [ ] **Step 3: Write minimal implementation**

在 `materialized_version`(`external_store.py:473`)的 manifest 分支之后、二进制拒绝分支之前,
新增 parquet 分支(放在 `if not str(version.storage_uri).startswith("s3://"):` 本地透传分支之后):

```python
    # parquet:dj ParquetFormatter 原生读 → 取字节落临时 .parquet,直接 yield 该路径
    if version.format == "parquet":
        cfg = await _version_cfg(version, session)
        bucket, key = parse_s3_uri(version.storage_uri)
        content = await cached_bytes(cfg, bucket, key)
        tmp = Path(tempfile.mktemp(prefix="adp-pq-", suffix=".parquet"))
        tmp.write_bytes(content)
        try:
            yield tmp
        finally:
            tmp.unlink(missing_ok=True)
        return
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_external_store.py -k parquet -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/external_store.py backend/tests/test_external_store.py
git commit -m "feat(external-store): materialized_version 支持 parquet 直读喂 dj"
```

---

### Task 6: 预览支持 parquet(复用 DuckDB)

**Files:**
- Modify: `backend/app/api/v1/datasets.py:1667-1706`(`preview_version` 的 s3:// 分支)
- Test: `backend/tests/test_datasets_preview.py`(新建或追加)

**Interfaces:**
- Consumes: 已存在的 `_duck_query`(`datasets.py:1777`)、`s3_settings_for_duckdb`、`platform_config`。
- Produces: `preview_version` 对 `format="parquet"` 的 s3:// 版本经 DuckDB `read_parquet` 返回 `{data, columns, total}`(类型如实)。

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_datasets_preview.py
import pytest


@pytest.mark.asyncio
async def test_preview_parquet_version(client, parquet_version_in_uploads):
    # parquet_version_in_uploads: fixture,往 uploads 桶放一个 data.parquet 并建 version(format=parquet)
    version_id = parquet_version_in_uploads
    resp = await client.get(f"/api/v1/dataset-versions/{version_id}/preview")
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert "id" in body["columns"]
    # 数字列仍是数字(parquet 保真),非字符串
    assert isinstance(body["data"][0]["id"], int)
```

> fixture 若难造,可退化为对 preview 的 parquet 分支做函数级单测:monkeypatch `_duck_query` 返回固定 `(rows, columns, total)`,断言 `preview_version` 走该分支并原样返回。

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_datasets_preview.py -v`
Expected: FAIL — 当前 parquet 走 `head_records` → `normalize_to_records` 抛 `UnsupportedFormatError` → 400。

- [ ] **Step 3: Write minimal implementation**

在 `preview_version` 的 s3:// 分支(`datasets.py:1667`)进入后、调用 `head_records` 之前,
对 parquet 插入 DuckDB 分支:

```python
    if str(version.storage_uri).startswith("s3://"):
        # parquet:走 DuckDB read_parquet(类型保真),复用 SQL 查询同款执行器
        if version.format == "parquet":
            try:
                cfg = await _version_storage_cfg(version, session)
                if cfg is None:
                    raise ExternalStoreError("平台存储(MinIO)未配置")
                s3 = s3_settings_for_duckdb(cfg)
                rows, columns, _total = await asyncio.to_thread(
                    _duck_query, version.storage_uri, "parquet",
                    "SELECT * FROM t", limit, offset, s3,
                )
            except ExternalStoreError as exc:
                return JSONResponse(
                    status_code=400,
                    content={"success": False, "message": f"读取 parquet 失败:{exc}"},
                )
            return JSONResponse(
                content={
                    "data": rows,
                    "columns": columns,
                    "total": version.rows or 0,
                    "success": True,
                }
            )
        # …(原 head_records 分支保持不变)
```

> `_duck_query` 的 `path` 参数对 `s3://` 由 httpfs 直查 MinIO(分支内 `s3 is not None`),与 SQL 端点一致;无需下载。`_version_storage_cfg` 已存在于 `datasets.py`(preview 既用),复用即可。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_datasets_preview.py -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/v1/datasets.py backend/tests/test_datasets_preview.py
git commit -m "feat(datasets): parquet 版本预览走 DuckDB read_parquet(类型保真)"
```

---

### Task 7: dj 加工 parquet 数据集 → 产物输出 parquet

**Files:**
- Modify: `backend/app/services/engine.py:415-479`(产物路径/text_key/行数/format/上传)
- Modify: `backend/app/services/external_store.py`(新增 `upload_parquet_file_to_uploads`)
- Test: `backend/tests/test_engine_parquet.py`(新建)

**Interfaces:**
- Consumes: `materialized_version`(Task 5,parquet 输入产出本地 .parquet)、`parquet_bytes_to_records`(Task 1)、`s3_settings_for_duckdb`。
- Produces: parquet 输入版本的加工产物版本 `format="parquet"`,`storage_uri` 指向 `data.parquet`。

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_engine_parquet.py
import pytest

from app.services.landing import records_to_parquet_bytes, parquet_bytes_to_records


def test_parquet_head_for_text_key(tmp_path):
    # 工具:从 parquet 取前 N 行供 detect_text_key(替代 _read_jsonl_head)
    from app.services.engine import _read_head_records

    p = tmp_path / "data.parquet"
    p.write_bytes(records_to_parquet_bytes([{"title": "hello", "x": 1}]))
    head = _read_head_records(p, 10)
    assert head and "title" in head[0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_engine_parquet.py -v`
Expected: FAIL — `ImportError: cannot import name '_read_head_records'`。

- [ ] **Step 3: Write minimal implementation**

(a) 在 `engine.py` 新增格式无关的取头函数(替代仅 jsonl 的 `_read_jsonl_head`),并在 parquet 输入时用之:

```python
def _read_head_records(path: Path, n: int) -> list[dict]:
    """取数据文件前 N 行 dict(jsonl 逐行 / parquet 读表),供 text_key 探测。"""
    if path.suffix == ".parquet":
        from app.services.landing import parquet_bytes_to_records

        return parquet_bytes_to_records(path.read_bytes(), limit=n)
    return _read_jsonl_head(path, n)
```

(b) `external_store.py` 新增流式上传 parquet 产物:

```python
async def upload_parquet_file_to_uploads(
    dataset_id: str, version_no: int, path: Path
) -> str:
    """把本地 parquet 产物流式上传到平台 MinIO,键 = ``<id>/v<n>/data.parquet``。"""
    cfg = platform_config()
    bucket = settings.storage_minio_upload_bucket
    key = f"{dataset_id}/v{version_no}/data.parquet"
    size = path.stat().st_size
    with path.open("rb") as f:
        await upload_object(cfg, bucket, key, f, size)
    return f"s3://{bucket}/{key}"
```

(c) `engine.py` 加工块(`engine.py:415-479`)按输入版本格式分叉产物格式:

```python
    is_parquet = input_version.format == "parquet"
    out_name = "data.parquet" if is_parquet else "data.jsonl"
    out_path = out_dir / out_name
    ...
    async with materialized_version(input_version, session) as input_path:
        text_key = detect_text_key(_read_head_records(input_path, 50))
        cfg = build_config(
            project_name=job_id,
            input_path=str(input_path),
            output_path=str(out_path),   # 后缀 .parquet → dj Exporter 输出 parquet
            operators=operators,
            text_key=text_key,
        )
        ...
    # 非 manifest 产物落地:
    if is_parquet:
        rows = len(parquet_bytes_to_records(out_path.read_bytes()))
        storage_uri = await upload_parquet_file_to_uploads(dataset_id, new_vno, out_path)
        out_fmt = "parquet"
    else:
        rows = sum(1 for line in out_path.open(encoding="utf-8") if line.strip())
        storage_uri = await upload_file_to_uploads(dataset_id, new_vno, out_path)
        out_fmt = "jsonl"
    version = DatasetVersion(
        id=_new_version_id(),
        dataset_id=dataset_id,
        version_no=new_vno,
        storage_uri=storage_uri,
        stats_uri=str(stats_path) if stats_path.exists() else None,
        format=out_fmt,
        ...
    )
```

> 需在 `engine.py` 顶部 import `parquet_bytes_to_records` 与 `upload_parquet_file_to_uploads`。
> `data_stats.jsonl` 维持 jsonl(dj stats 恒 jsonl,见 exporter `_stats.jsonl`),不随产物格式变。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_engine_parquet.py -v`
Expected: PASS。

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/engine.py backend/app/services/external_store.py backend/tests/test_engine_parquet.py
git commit -m "feat(engine): parquet 数据集加工产物输出 parquet(text_key/行数/上传 parquet-aware)"
```

---

## 收尾验证(全部任务后)

- [ ] 全量后端测试:`cd backend && ./.venv/Scripts/python.exe -m pytest -q`,确认无回归(尤其 `test_ingest_tasks` / `test_external_store` / 上传/manifest 相关)。
- [ ] 手动冒烟:`/adp-start` → 建一个 PG 数据源 → 采集任务选表 → 运行 → 数据集详情预览,确认数字列是数字、`format=parquet`;对该版本跑一个文本算子加工,产物版本 `format=parquet` 且可预览。
- [ ] 回归冒烟:本地上传一个 csv/txt,确认仍 `format=jsonl`、预览正常。

## Self-Review 备注

- 预览端点真实为 `GET /dataset-versions/{version_id}/preview`(`datasets.py:1573`),已用 DuckDB(`_duck_query`,parquet→read_parquet 已注册),Task 6 复用,无需新建查询引擎。
- `materialized_version` 第二参为 `session`;Task 5 测试传 `session=None`(parquet 分支用 `_version_cfg`,managed 版本不取 DataSource,容忍 None)。如 `_version_cfg` 对 None session 不安全,改用 `platform_config()` 直取(managed 版本凭证恒为平台)。
- 数据库迁移:无(format 为 free string)。
