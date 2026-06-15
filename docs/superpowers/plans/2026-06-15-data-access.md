# 数据接入(Data Access)Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 `数据管理` 菜单下新增「数据接入」页(替换「本地上传」),按文件类型分栏接入数据,每类支持本地上传与从文件管理零拷贝引入两条路径。

**Architecture:** 后端复用现有 landing(规范化落地)与 #18 托管机制:文本类走 `land_upload`(jsonl 规范化),二进制类走新增 `land_upload_raw`(原样存),文件管理对象走新增 `POST /datasets/host-platform`(零拷贝登记,用平台 MinIO 凭证)。前端新建独立页,左侧类型栏驱动列表 `dataType` 过滤与上传 `accept`。

**Tech Stack:** 后端 FastAPI + SQLAlchemy async + PostgreSQL,pytest;前端 Ant Design Pro v6(React 19 / antd 6 / Umi Max),Jest + RTL。

**约定:**
- 后端命令在 `backend/` 目录跑;测试 `uv run pytest`(沿用现有约定,如不确定先看 `backend/CLAUDE.md` / 现有测试如何跑)。
- 前端命令在 `frontend/` 目录跑;`npm run test` / `npm run lint` / `npx antd lint ./src`。
- 提交用 conventional commits(commitlint 强制);AI 提交自动加 `Co-Authored-By`。
- 类型栏的 `key` 即数据集的 `dataType`:`csv-tsv` / `sql` / `image` / `audio` / `video` / `pdf` / `json` / `log`。

---

## 文件结构

**后端**
- Modify `backend/app/services/landing.py` — 新增 `BINARY_FORMATS`、`INGESTABLE_FORMATS`、`land_upload_raw`;`LANDABLE_FORMATS` 加 `log`。
- Modify `backend/app/api/v1/datasets.py` — upload 端点按格式分流;新增 `host_platform` 端点;preview 加二进制守卫 + hosted 平台凭证回退。
- Modify `backend/app/schemas/dataset.py` — 新增 `PlatformHostRequest`。
- Modify `backend/app/services/external_store.py` — `materialized_version` 在 `source_datasource_id` 为空时回退 `platform_config()`。
- Test `backend/tests/test_data_access.py`(新增,覆盖 raw landing / host-platform / preview 回退)。

**前端**
- Create `frontend/src/pages/ingest/access/constants.ts` — 类型栏分类法 + 纯函数。
- Create `frontend/src/pages/ingest/access/FileManagerPicker.tsx` — 平台对象选择器。
- Create `frontend/src/pages/ingest/access/UploadModal.tsx` — 双导入上传弹框。
- Create `frontend/src/pages/ingest/access/index.tsx` — 页面(左栏 + 表格 + SQL 引导)。
- Create `frontend/src/pages/ingest/access/constants.test.ts`、`index.test.tsx`。
- Modify `frontend/config/routes.ts`、`frontend/src/locales/zh-CN/menu.ts`、`frontend/src/locales/en-US/menu.ts`。
- Modify `frontend/src/services/data-platform/api.ts`、`frontend/src/services/data-platform/typings.d.ts`。
- Delete `frontend/src/pages/ingest/upload/`(index.tsx + utils.ts)。

---

## Task 1: 后端 — landing 扩展格式 + raw landing

**Files:**
- Modify: `backend/app/services/landing.py`
- Test: `backend/tests/test_data_access.py`

- [ ] **Step 1: 写失败测试**

新建 `backend/tests/test_data_access.py`:

```python
"""数据接入:raw landing / host-platform / preview 回退测试。"""

import pytest

from app.services import landing


@pytest.mark.asyncio
async def test_land_upload_raw_stores_bytes_rows_null(db_session, tmp_path, monkeypatch):
    # datasets 落到临时目录,避免污染
    monkeypatch.setattr(landing.settings, "datasets_dir", str(tmp_path))
    content = b"\x89PNG\r\n\x1a\n binary bytes"
    dataset, version = await landing.land_upload_raw(
        db_session,
        content=content,
        filename="cat.png",
        source_format="png",
        data_type="image",
    )
    assert version.rows is None
    assert version.format == "png"
    assert version.size == len(content)
    assert version.origin == "managed"
    # 原样存:文件内容字节级一致
    from pathlib import Path

    assert Path(version.storage_uri).read_bytes() == content
    assert dataset.data_type == "image"


def test_format_sets():
    assert "log" in landing.LANDABLE_FORMATS
    assert "mp4" in landing.BINARY_FORMATS
    assert "png" in landing.INGESTABLE_FORMATS
    assert "csv" in landing.INGESTABLE_FORMATS
    # 二进制不在可规范化集合
    assert "mp4" not in landing.LANDABLE_FORMATS
```

> `db_session` fixture:先看 `backend/tests/conftest.py` 是否已有 async session fixture(本仓库有 `conftest.py`)。若名称不同,改用现有名。

- [ ] **Step 2: 跑测试,确认失败**

Run: `cd backend && uv run pytest tests/test_data_access.py -v`
Expected: FAIL —`AttributeError: module 'app.services.landing' has no attribute 'land_upload_raw'` / `BINARY_FORMATS`。

- [ ] **Step 3: 实现**

在 `backend/app/services/landing.py` 顶部格式定义处,把 `LANDABLE_FORMATS` 改为含 `log`,并新增二进制与汇总集合(放在 `LANDABLE_FORMATS` 定义之后):

```python
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
```

在文件末尾(`land_upload` 之后)新增:

```python
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
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / (Path(filename).name or f"data.{source_format}")
    out_path.write_bytes(content)

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
```

- [ ] **Step 4: 跑测试,确认通过**

Run: `cd backend && uv run pytest tests/test_data_access.py -v`
Expected: PASS(2 passed)。

- [ ] **Step 5: 提交**

```bash
git add backend/app/services/landing.py backend/tests/test_data_access.py
git commit -m "feat(landing): 二进制 raw landing + 接入格式白名单扩展(数据接入)"
```

---

## Task 2: 后端 — upload 端点按格式分流

**Files:**
- Modify: `backend/app/api/v1/datasets.py:82-118`(upload 端点)、import 区
- Test: `backend/tests/test_data_access.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_data_access.py`(`client` 为现有 httpx async fixture,名称以 `conftest.py` 为准):

```python
@pytest.mark.asyncio
async def test_upload_binary_lands_raw(client, monkeypatch, tmp_path):
    from app.services import landing as landing_mod

    monkeypatch.setattr(landing_mod.settings, "datasets_dir", str(tmp_path))
    files = {"file": ("clip.mp4", b"\x00\x00\x00\x18ftypmp42rawbytes", "video/mp4")}
    resp = await client.post(
        "/api/v1/datasets/upload", files=files, data={"data_type": "video"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"]["dataType"] == "video"
    # 二进制不解析:版本 rows 为空
    assert body["data"]["versions"][0]["rows"] is None
    assert body["data"]["versions"][0]["format"] == "mp4"
```

- [ ] **Step 2: 跑测试,确认失败**

Run: `cd backend && uv run pytest tests/test_data_access.py::test_upload_binary_lands_raw -v`
Expected: FAIL — 当前 mp4 走 `land_upload` → `normalize_to_records` 抛 `UnsupportedFormatError` → 400(不是 200)。

- [ ] **Step 3: 实现**

`datasets.py` import 区(第 39-44 行)改为带上 `BINARY_FORMATS` 与 `land_upload_raw`:

```python
from app.services.landing import (
    BINARY_FORMATS,
    LANDABLE_FORMATS,
    LandingError,
    UnsupportedFormatError,
    land_upload,
    land_upload_raw,
)
```

`upload_as_dataset`(第 95-118 行)的 landing 分流改为:

```python
    if fmt in BINARY_FORMATS:
        dataset, version = await land_upload_raw(
            session,
            content=content,
            filename=filename,
            source_format=fmt,
            dataset_name=name,
            data_type=data_type,
            description=description,
        )
    else:
        try:
            dataset, version = await land_upload(
                session,
                content=content,
                filename=filename,
                source_format=fmt,
                dataset_name=name,
                data_type=data_type,
                description=description,
            )
        except UnsupportedFormatError:
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "message": f"格式 .{fmt} 暂不支持落地;当前支持 "
                    "jsonl/json/csv/tsv/txt/log/xlsx/xls/html/pdf/doc/docx/ppt/pptx "
                    "及常见图像/音频/视频",
                },
            )
        except LandingError as exc:
            return JSONResponse(
                status_code=400,
                content={"success": False, "message": f"解析失败:{exc}"},
            )
```

- [ ] **Step 4: 跑测试,确认通过**

Run: `cd backend && uv run pytest tests/test_data_access.py -v`
Expected: PASS。再跑 `uv run pytest tests/ -q` 确认未回归既有上传测试。

- [ ] **Step 5: 提交**

```bash
git add backend/app/api/v1/datasets.py backend/tests/test_data_access.py
git commit -m "feat(datasets): upload 按格式分流(二进制走 raw landing)"
```

---

## Task 3: 后端 — `POST /datasets/host-platform`(文件管理零拷贝)

**Files:**
- Modify: `backend/app/schemas/dataset.py`(新增 `PlatformHostRequest`)
- Modify: `backend/app/api/v1/datasets.py`(import + 新端点)
- Test: `backend/tests/test_data_access.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_data_access.py`:

```python
@pytest.mark.asyncio
async def test_host_platform_zero_copy(client, monkeypatch):
    """登记平台对象为受管数据集:只 stat 不下载。"""
    from app.api.v1 import datasets as datasets_mod

    monkeypatch.setattr(
        datasets_mod, "platform_config", lambda: {"endpoint": "x", "accessKey": "a", "secretKey": "b"}
    )

    async def fake_stat(cfg, bucket, key):
        return {"size": 4242}

    called = {"download": False}

    async def fake_download(*a, **k):
        called["download"] = True
        raise AssertionError("不应下载")

    monkeypatch.setattr(datasets_mod, "stat_object", fake_stat)
    # 保证零拷贝:download 路径若被触发即失败
    monkeypatch.setattr("app.services.external_store.download_to_temp", fake_download)

    resp = await client.post(
        "/api/v1/datasets/host-platform",
        json={"bucket": "raw", "keys": ["a/b.csv"], "dataType": "csv-tsv"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert called["download"] is False
    v = body["data"][0]["versions"][0]
    assert v["origin"] == "hosted"
    assert v["storageUri"] == "s3://raw/a/b.csv"
    assert v["size"] == 4242


@pytest.mark.asyncio
async def test_host_platform_unconfigured_returns_503(client, monkeypatch):
    from app.api.v1 import datasets as datasets_mod
    from app.services.external_store import ExternalStoreError

    def boom():
        raise ExternalStoreError("平台存储(MinIO)未配置")

    monkeypatch.setattr(datasets_mod, "platform_config", boom)
    resp = await client.post(
        "/api/v1/datasets/host-platform",
        json={"bucket": "raw", "keys": ["a/b.csv"]},
    )
    assert resp.status_code == 503
```

> 注:出参 `storageUri`/`size` 的 camel 别名以现有 `DatasetVersionRead` 实际序列化为准;若别名不同,按实际字段名断言。

- [ ] **Step 2: 跑测试,确认失败**

Run: `cd backend && uv run pytest tests/test_data_access.py -k host_platform -v`
Expected: FAIL —404(路由不存在)。

- [ ] **Step 3: 实现**

`schemas/dataset.py` 在 `HostS3Request` 之后新增:

```python
class PlatformHostRequest(CamelModel):
    """文件管理零拷贝接入入参:把平台 MinIO 对象登记为受管数据集版本(不下载)。"""

    bucket: str
    keys: list[str]
    name: str | None = None
    data_type: str | None = None
    category_id: str | None = None
```

`datasets.py` import 区:`external_store` import 加 `platform_config`;`landing` import 加 `INGESTABLE_FORMATS`;`schemas.dataset` import 加 `PlatformHostRequest`:

```python
from app.services.external_store import (
    ExternalStoreError,
    head_records,
    parse_s3_uri,
    platform_config,
    stat_object,
)
```
```python
from app.services.landing import (
    BINARY_FORMATS,
    INGESTABLE_FORMATS,
    LANDABLE_FORMATS,
    LandingError,
    UnsupportedFormatError,
    land_upload,
    land_upload_raw,
)
```
```python
from app.schemas.dataset import (
    DatasetDetailRead,
    DatasetRead,
    DatasetUpdate,
    DatasetVersionRead,
    HostS3Request,
    PlatformHostRequest,
)
```

在 `host_s3` 端点之后新增端点:

```python
@router.post("/datasets/host-platform")
async def host_platform(body: PlatformHostRequest, session: SessionDep) -> JSONResponse:
    """文件管理零拷贝接入:把平台 MinIO 若干对象登记为受管数据集版本,**不下载**。

    用 platform_config() 取平台存储凭证(而非数据源);source_datasource_id 留空,
    预览/物化时由 platform_config 回退定位(见 preview / materialized_version)。
    """
    if not body.keys:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "请至少选择一个对象"},
        )
    try:
        cfg = platform_config()
    except ExternalStoreError as exc:
        return JSONResponse(
            status_code=503, content={"success": False, "message": str(exc)}
        )

    pairs: list[tuple[Dataset, DatasetVersion]] = []
    multiple = len(body.keys) > 1
    for key in body.keys:
        fmt = _file_ext(key)
        if fmt not in INGESTABLE_FORMATS:
            return JSONResponse(
                status_code=400,
                content={
                    "success": False,
                    "message": f"对象 {key} 的格式 .{fmt} 暂不支持接入",
                },
            )
        try:
            meta = await stat_object(cfg, body.bucket, key)
        except ExternalStoreError as exc:
            return JSONResponse(
                status_code=400,
                content={"success": False, "message": f"读取平台对象失败:{exc}"},
            )

        base_name = body.name or Path(key).stem or "未命名接入数据集"
        ds_name = f"{base_name}/{Path(key).name}" if multiple else base_name
        dataset = Dataset(
            id=_new_dataset_id(),
            name=ds_name,
            data_type=body.data_type,
            category_id=body.category_id,
            owner="admin",
            creator="admin",
        )
        session.add(dataset)
        version = DatasetVersion(
            id=_new_version_id(),
            dataset_id=dataset.id,
            version_no=1,
            storage_uri=f"s3://{body.bucket}/{key}",
            format=fmt,
            rows=None,
            size=meta.get("size"),
            origin="hosted",
            source_datasource_id=None,
            note=f"文件管理接入(零拷贝):s3://{body.bucket}/{key}",
        )
        session.add(version)
        pairs.append((dataset, version))

    await session.commit()
    cat_name = None
    if body.category_id:
        names = await build_category_name_map(session, [body.category_id])
        cat_name = names.get(body.category_id)
    created: list[DatasetDetailRead] = []
    for dataset, version in pairs:
        await session.refresh(dataset)
        await session.refresh(version)
        detail = _to_detail(dataset, [version])
        if dataset.category_id:
            detail.category_name = cat_name
        created.append(detail)
    return JSONResponse(
        content={
            "data": [d.model_dump(by_alias=True, mode="json") for d in created],
            "success": True,
        }
    )
```

- [ ] **Step 4: 跑测试,确认通过**

Run: `cd backend && uv run pytest tests/test_data_access.py -k host_platform -v`
Expected: PASS(2 passed)。

- [ ] **Step 5: 提交**

```bash
git add backend/app/schemas/dataset.py backend/app/api/v1/datasets.py backend/tests/test_data_access.py
git commit -m "feat(datasets): POST /datasets/host-platform 文件管理零拷贝接入"
```

---

## Task 4: 后端 — preview 二进制守卫 + hosted 平台凭证回退

**Files:**
- Modify: `backend/app/api/v1/datasets.py`(preview 端点,约 369-447 行)
- Test: `backend/tests/test_data_access.py`

- [ ] **Step 1: 写失败测试**

追加到 `tests/test_data_access.py`:

```python
@pytest.mark.asyncio
async def test_preview_hosted_platform_fallback(client, monkeypatch):
    """平台零拷贝(source_datasource_id 为空)的预览用 platform_config 取数,不报缺数据源。"""
    from app.api.v1 import datasets as datasets_mod

    monkeypatch.setattr(
        datasets_mod, "platform_config", lambda: {"endpoint": "x", "accessKey": "a", "secretKey": "b"}
    )

    async def fake_stat(cfg, bucket, key):
        return {"size": 10}

    async def fake_head(cfg, bucket, key, fmt, limit):
        return [{"a": 1}, {"a": 2}]

    monkeypatch.setattr(datasets_mod, "stat_object", fake_stat)
    monkeypatch.setattr(datasets_mod, "head_records", fake_head)

    created = await client.post(
        "/api/v1/datasets/host-platform",
        json={"bucket": "raw", "keys": ["a/b.csv"], "dataType": "csv-tsv"},
    )
    version_id = created.json()["data"][0]["versions"][0]["id"]

    resp = await client.get(f"/api/v1/datasets/versions/{version_id}/preview")
    assert resp.status_code == 200
    assert resp.json()["data"] == [{"a": 1}, {"a": 2}]


@pytest.mark.asyncio
async def test_preview_binary_not_previewable(client, monkeypatch, tmp_path):
    from app.services import landing as landing_mod

    monkeypatch.setattr(landing_mod.settings, "datasets_dir", str(tmp_path))
    files = {"file": ("p.png", b"\x89PNG bytes", "image/png")}
    up = await client.post(
        "/api/v1/datasets/upload", files=files, data={"data_type": "image"}
    )
    version_id = up.json()["data"]["versions"][0]["id"]
    resp = await client.get(f"/api/v1/datasets/versions/{version_id}/preview")
    assert resp.status_code == 200
    assert resp.json()["data"] == []
    assert "不支持预览" in resp.json().get("message", "")
```

> preview 路由路径以现有为准(此处假设 `/api/v1/datasets/versions/{version_id}/preview`;实现前用 `grep -n "preview" backend/app/api/v1/datasets.py` 确认真实路径,测试里对齐)。

- [ ] **Step 2: 跑测试,确认失败**

Run: `cd backend && uv run pytest tests/test_data_access.py -k preview -v`
Expected: FAIL — fallback 用例报"托管版本缺少数据源引用"(400);binary 用例当前会进 managed 分支 `json.loads` 二进制 → 500/解析错误。

- [ ] **Step 3: 实现**

`datasets.py` import 区的 `landing` import 已含 `BINARY_FORMATS`(Task 2/3)。在 preview 端点取到 `version` 且非 None 之后,**最前面**加二进制守卫:

```python
    if version.format in BINARY_FORMATS:
        return JSONResponse(
            content={
                "data": [],
                "columns": [],
                "total": version.rows or 0,
                "success": True,
                "message": "二进制文件不支持预览,请下载查看",
            }
        )
```

把 hosted 分支(原第 387-417 行)改为按是否有 datasource 选择 config:

```python
    # hosted:按需从 S3 取前 offset+limit 条再切片(预览成本由取前 N 缓解)
    if version.origin == "hosted":
        if version.source_datasource_id:
            ds = await session.get(DataSource, version.source_datasource_id)
            if ds is None:
                return JSONResponse(
                    status_code=400,
                    content={"success": False, "message": "托管版本对应的数据源已不存在"},
                )
            cfg = ds.config
        else:
            # 平台对象零拷贝接入:用平台 MinIO 凭证回退
            try:
                cfg = platform_config()
            except ExternalStoreError as exc:
                return JSONResponse(
                    status_code=503,
                    content={"success": False, "message": str(exc)},
                )
        try:
            bucket, key = parse_s3_uri(version.storage_uri)
            head = await head_records(
                cfg, bucket, key, version.format, offset + limit
            )
        except ExternalStoreError as exc:
            return JSONResponse(
                status_code=400,
                content={"success": False, "message": f"读取 S3 对象失败:{exc}"},
            )
        rows = head[offset : offset + limit]
        return JSONResponse(
            content={
                "data": rows,
                "columns": _columns_of(rows),
                "total": version.rows or 0,
                "success": True,
            }
        )
```

- [ ] **Step 4: 跑测试,确认通过**

Run: `cd backend && uv run pytest tests/test_data_access.py -v && uv run pytest tests/ -q`
Expected: 全 PASS,无回归。

- [ ] **Step 5: 提交**

```bash
git add backend/app/api/v1/datasets.py backend/tests/test_data_access.py
git commit -m "feat(datasets): 预览二进制守卫 + hosted 平台凭证回退"
```

---

## Task 5: 后端 — `materialized_version` 平台凭证回退(加工路径)

**Files:**
- Modify: `backend/app/services/external_store.py:280-292`
- Test: `backend/tests/test_data_access.py`

- [ ] **Step 1: 写失败测试**

追加:

```python
@pytest.mark.asyncio
async def test_materialized_version_platform_fallback(db_session, monkeypatch):
    """origin=hosted 且无 source_datasource_id → 用 platform_config 下载,不报缺凭证。"""
    from pathlib import Path

    from app.models.dataset_version import DatasetVersion
    from app.services import external_store as es

    monkeypatch.setattr(
        es, "platform_config", lambda: {"endpoint": "x", "accessKey": "a", "secretKey": "b"}
    )

    seen = {}

    async def fake_download(cfg, bucket, key):
        seen["cfg"] = cfg
        p = Path((await _atmp()))
        p.write_bytes(b'{"x":1}\n')
        return p

    # 简化:直接给个临时 jsonl 文件
    import tempfile

    async def _atmp():
        fd, name = tempfile.mkstemp(suffix=".jsonl")
        import os

        os.close(fd)
        return name

    monkeypatch.setattr(es, "download_to_temp", fake_download)

    v = DatasetVersion(
        id="dsv-test01",
        dataset_id="dset-test01",
        version_no=1,
        storage_uri="s3://raw/a.jsonl",
        format="jsonl",
        origin="hosted",
        source_datasource_id=None,
    )
    async with es.materialized_version(v, db_session) as path:
        assert path.exists()
    assert seen["cfg"]["endpoint"] == "x"
```

- [ ] **Step 2: 跑测试,确认失败**

Run: `cd backend && uv run pytest tests/test_data_access.py -k materialized -v`
Expected: FAIL —`ExternalStoreError: 托管版本缺少 source_datasource_id`。

- [ ] **Step 3: 实现**

`external_store.py` `materialized_version`(第 285-292 行)的取凭证逻辑改为:

```python
    if version.source_datasource_id:
        ds = await session.get(DataSource, version.source_datasource_id)
        if ds is None:
            raise ExternalStoreError("托管版本对应的数据源已不存在,无法访问 S3")
        cfg = ds.config
    else:
        # 平台对象零拷贝接入:用平台 MinIO 凭证(未配置 → ExternalStoreError)
        cfg = platform_config()

    bucket, key = parse_s3_uri(version.storage_uri)
    raw_path = await download_to_temp(cfg, bucket, key)
```

> `platform_config` 与 `materialized_version` 同在本模块,直接调用即可。

- [ ] **Step 4: 跑测试,确认通过**

Run: `cd backend && uv run pytest tests/test_data_access.py -k materialized -v`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add backend/app/services/external_store.py backend/tests/test_data_access.py
git commit -m "feat(external-store): materialized_version 平台凭证回退(零拷贝接入可加工)"
```

---

## Task 6: 前端 — 类型栏分类法 constants

**Files:**
- Create: `frontend/src/pages/ingest/access/constants.ts`
- Test: `frontend/src/pages/ingest/access/constants.test.ts`

- [ ] **Step 1: 写失败测试**

```ts
import {
  ACCESS_TYPES,
  acceptOf,
  formatFileSize,
  getExtension,
  isExtAllowed,
} from './constants';

describe('access constants', () => {
  it('有 8 个类型栏,key 唯一', () => {
    expect(ACCESS_TYPES).toHaveLength(8);
    const keys = ACCESS_TYPES.map((t) => t.key);
    expect(new Set(keys).size).toBe(8);
    expect(keys).toContain('csv-tsv');
    expect(keys).toContain('sql');
  });

  it('sql 栏无扩展名(纯引导)', () => {
    const sql = ACCESS_TYPES.find((t) => t.key === 'sql')!;
    expect(sql.extensions).toEqual([]);
  });

  it('acceptOf 拼带点逗号串', () => {
    const csv = ACCESS_TYPES.find((t) => t.key === 'csv-tsv')!;
    expect(acceptOf(csv)).toBe('.csv,.tsv');
  });

  it('isExtAllowed 大小写不敏感、按栏判定', () => {
    const img = ACCESS_TYPES.find((t) => t.key === 'image')!;
    expect(isExtAllowed('A.PNG', img)).toBe(true);
    expect(isExtAllowed('a.csv', img)).toBe(false);
  });

  it('getExtension / formatFileSize', () => {
    expect(getExtension('a.b.CSV')).toBe('csv');
    expect(getExtension('noext')).toBe('');
    expect(formatFileSize(0)).toBe('0 B');
    expect(formatFileSize(1536)).toBe('1.5 KB');
  });
});
```

- [ ] **Step 2: 跑测试,确认失败**

Run: `cd frontend && npm run test -- constants.test`
Expected: FAIL — 模块不存在。

- [ ] **Step 3: 实现**

`frontend/src/pages/ingest/access/constants.ts`:

```ts
/** 数据接入类型栏定义(key 即数据集 dataType)。 */
export interface AccessType {
  /** 栏 key,等于落库的 dataType */
  key: string;
  /** 菜单标签 */
  label: string;
  /** 是否二进制(本地上传走 raw、预览置灰) */
  binary: boolean;
  /** 允许的扩展名(不含点,小写);sql 栏为空数组(纯引导) */
  extensions: string[];
}

/** 左侧 8 个类型栏(顺序即展示顺序,贴合 BCC 截图) */
export const ACCESS_TYPES: AccessType[] = [
  { key: 'csv-tsv', label: 'CSV/TSV接入', binary: false, extensions: ['csv', 'tsv'] },
  { key: 'sql', label: 'SQL接入', binary: false, extensions: [] },
  { key: 'image', label: '图像接入', binary: true, extensions: ['png', 'jpg', 'jpeg', 'gif', 'bmp', 'webp'] },
  { key: 'audio', label: '音频接入', binary: true, extensions: ['mp3', 'wav', 'flac', 'm4a', 'aac', 'ogg'] },
  { key: 'video', label: '视频接入', binary: true, extensions: ['mp4', 'avi', 'mov', 'mkv', 'webm'] },
  { key: 'pdf', label: 'PDF接入', binary: false, extensions: ['pdf'] },
  { key: 'json', label: 'JSON接入', binary: false, extensions: ['json', 'jsonl'] },
  { key: 'log', label: '日志接入', binary: false, extensions: ['log', 'txt'] },
];

/** Upload accept 属性值(带点号,逗号分隔) */
export const acceptOf = (t: AccessType): string =>
  t.extensions.map((ext) => `.${ext}`).join(',');

/** 从文件名取小写扩展名(无扩展名返回空串) */
export const getExtension = (filename: string): string => {
  const dotIndex = filename.lastIndexOf('.');
  if (dotIndex < 0 || dotIndex === filename.length - 1) return '';
  return filename.slice(dotIndex + 1).toLowerCase();
};

/** 文件扩展名是否在该类型栏白名单内 */
export const isExtAllowed = (filename: string, t: AccessType): boolean =>
  t.extensions.includes(getExtension(filename));

/** 字节数格式化为人类友好大小(B / KB / MB / GB) */
export const formatFileSize = (bytes: number): string => {
  if (!Number.isFinite(bytes) || bytes < 0) return '-';
  if (bytes < 1024) return `${bytes} B`;
  const units = ['KB', 'MB', 'GB', 'TB'];
  let value = bytes / 1024;
  let unitIndex = 0;
  while (value >= 1024 && unitIndex < units.length - 1) {
    value /= 1024;
    unitIndex += 1;
  }
  const fixed = value >= 100 || Number.isInteger(value) ? 0 : 1;
  return `${value.toFixed(fixed)} ${units[unitIndex]}`;
};
```

- [ ] **Step 4: 跑测试,确认通过**

Run: `cd frontend && npm run test -- constants.test`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add frontend/src/pages/ingest/access/constants.ts frontend/src/pages/ingest/access/constants.test.ts
git commit -m "feat(access): 数据接入类型栏分类法 + 纯函数"
```

---

## Task 7: 前端 — 服务层 `hostPlatformFiles` + 类型

**Files:**
- Modify: `frontend/src/services/data-platform/api.ts`
- Modify: `frontend/src/services/data-platform/typings.d.ts`

- [ ] **Step 1: 加类型**

`typings.d.ts` 在 `HostS3Params` 附近新增(`grep -n "HostS3Params" typings.d.ts` 定位):

```ts
  type PlatformHostParams = {
    bucket: string;
    keys: string[];
    name?: string;
    dataType?: string;
    categoryId?: string;
  };
```

- [ ] **Step 2: 加服务函数**

`api.ts` 在 `hostS3` 之后新增:

```ts
/** 文件管理零拷贝接入为受管数据集 POST /api/v1/datasets/host-platform */
export async function hostPlatformFiles(
  body: DataPlatform.PlatformHostParams,
  options?: { [key: string]: any },
) {
  return request<{ data: DataPlatform.Dataset[]; success: boolean }>(
    '/api/v1/datasets/host-platform',
    {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      data: body,
      ...(options || {}),
    },
  );
}
```

- [ ] **Step 3: 类型检查**

Run: `cd frontend && npm run tsc`
Expected: 无新增类型错误。

- [ ] **Step 4: 提交**

```bash
git add frontend/src/services/data-platform/api.ts frontend/src/services/data-platform/typings.d.ts
git commit -m "feat(services): hostPlatformFiles 服务 + PlatformHostParams 类型"
```

---

## Task 8: 前端 — 路由与菜单(替换本地上传)

**Files:**
- Modify: `frontend/config/routes.ts`
- Modify: `frontend/src/locales/zh-CN/menu.ts:56`
- Modify: `frontend/src/locales/en-US/menu.ts`

- [ ] **Step 1: 改路由**

`config/routes.ts`:把 `/ingest/upload` 路由项替换为 access,并在 `/ingest` 组前补旧路径重定向。

`/ingest` 组内,删除:
```ts
      {
        path: '/ingest/upload',
        name: 'upload',
        component: './ingest/upload',
      },
```
替换为:
```ts
      {
        path: '/ingest/access',
        name: 'access',
        component: './ingest/access',
      },
```
并在顶层 `/files` 重定向项之后新增旧路径兼容:
```ts
  {
    // 本地上传已升级为「数据接入」(/ingest/access);旧路径兼容
    path: '/ingest/upload',
    redirect: '/ingest/access',
  },
```

- [ ] **Step 2: 改菜单文案**

`src/locales/zh-CN/menu.ts` 第 56 行 `'menu.ingest.upload': '本地上传',` 改为:
```ts
  'menu.ingest.access': '数据接入',
```
`src/locales/en-US/menu.ts` 找到 `menu.ingest.upload` 同样改为 `'menu.ingest.access': 'Data Access',`(键不存在则在 ingest 段新增)。

- [ ] **Step 3: 验证**

Run: `cd frontend && npm run tsc`
Expected: 无类型错误(此时 `./ingest/access` 尚未建会在 build 阶段才报;tsc 不报路由字符串)。先放行,Task 11 建页后整体起服务验证。

- [ ] **Step 4: 提交**

```bash
git add frontend/config/routes.ts frontend/src/locales/zh-CN/menu.ts frontend/src/locales/en-US/menu.ts
git commit -m "feat(routes): 本地上传→数据接入路由与菜单替换(旧路径重定向)"
```

---

## Task 9: 前端 — FileManagerPicker(平台对象选择器)

**Files:**
- Create: `frontend/src/pages/ingest/access/FileManagerPicker.tsx`

复用文件管理页的桶/目录浏览(`listPlatformBuckets`/`listFiles`),改为单/多选文件,按当前类型栏扩展名过滤可选项。

- [ ] **Step 1: 实现**

```tsx
import { FolderOutlined } from '@ant-design/icons';
import { Breadcrumb, Empty, Select, Space, Spin, Table } from 'antd';
import { useCallback, useEffect, useState } from 'react';
import { listFiles, listPlatformBuckets } from '@/services/data-platform';
import type { AccessType } from './constants';
import { formatFileSize, isExtAllowed } from './constants';

export interface PlatformSelection {
  bucket: string;
  keys: string[];
}

interface Props {
  /** 当前类型栏:决定可选文件的扩展名过滤 */
  accessType: AccessType;
  /** 选择变化回调(bucket + 勾选的对象 key 全路径) */
  onChange: (sel: PlatformSelection) => void;
}

type Row =
  | { kind: 'folder'; name: string }
  | { kind: 'file'; key: string; name: string; size?: number };

/** 文件管理对象选择器:浏览平台 MinIO,按类型栏过滤勾选文件。 */
const FileManagerPicker: React.FC<Props> = ({ accessType, onChange }) => {
  const [buckets, setBuckets] = useState<string[]>([]);
  const [bucket, setBucket] = useState<string>();
  const [prefix, setPrefix] = useState<string>('');
  const [rows, setRows] = useState<Row[]>([]);
  const [loading, setLoading] = useState(false);
  const [checkedKeys, setCheckedKeys] = useState<string[]>([]);

  useEffect(() => {
    listPlatformBuckets()
      .then((res) => {
        const list = res.data ?? [];
        setBuckets(list);
        setBucket((cur) => cur ?? list[0]);
      })
      .catch(() => undefined);
  }, []);

  const load = useCallback(async () => {
    if (!bucket) return;
    setLoading(true);
    try {
      const res = await listFiles({ bucket, prefix });
      const folders: Row[] = (res.data.folders ?? []).map((name) => ({
        kind: 'folder',
        name,
      }));
      const files: Row[] = (res.data.files ?? []).map((e) => ({
        kind: 'file',
        key: e.key,
        name: e.name,
        size: e.size,
      }));
      setRows([...folders, ...files]);
    } finally {
      setLoading(false);
    }
  }, [bucket, prefix]);

  useEffect(() => {
    load();
  }, [load]);

  // 切桶/换目录后,被勾选的对象可能已不可见;但保留跨目录选择,交由用户管理
  const emit = (keys: string[]) => {
    setCheckedKeys(keys);
    onChange({ bucket: bucket as string, keys });
  };

  const goPrefix = (next: string) => setPrefix(next);

  const segments = prefix.split('/').filter(Boolean);
  const breadcrumbItems = [
    { key: '__root__', title: <a onClick={() => goPrefix('')}>根目录</a> },
    ...segments.map((seg, i) => {
      const next = `${segments.slice(0, i + 1).join('/')}/`;
      const isLast = i === segments.length - 1;
      return { key: next, title: isLast ? seg : <a onClick={() => goPrefix(next)}>{seg}</a> };
    }),
  ];

  const columns = [
    {
      title: '名称',
      dataIndex: 'name',
      render: (_: unknown, row: Row) =>
        row.kind === 'folder' ? (
          <a onClick={() => goPrefix(`${prefix}${row.name}/`)}>
            <FolderOutlined style={{ marginRight: 6 }} />
            {row.name}
          </a>
        ) : (
          <span>{row.name}</span>
        ),
    },
    {
      title: '大小',
      width: 120,
      render: (_: unknown, row: Row) =>
        row.kind === 'folder' ? '-' : formatFileSize(row.size ?? 0),
    },
  ];

  return (
    <div>
      <Space style={{ marginBottom: 12 }} wrap>
        <span>存储桶:</span>
        <Select
          style={{ width: 220 }}
          placeholder="选择存储桶"
          value={bucket}
          options={buckets.map((b) => ({ label: b, value: b }))}
          onChange={(v) => {
            setBucket(v);
            setPrefix('');
          }}
        />
        <Breadcrumb items={breadcrumbItems} />
      </Space>
      <Spin spinning={loading}>
        {rows.length === 0 ? (
          <Empty description="该目录为空" image={Empty.PRESENTED_IMAGE_SIMPLE} />
        ) : (
          <Table<Row>
            size="small"
            rowKey={(r) => (r.kind === 'folder' ? `d:${r.name}` : `f:${r.key}`)}
            pagination={false}
            scroll={{ y: 300 }}
            dataSource={rows}
            columns={columns}
            rowSelection={{
              selectedRowKeys: rows
                .filter((r) => r.kind === 'file' && checkedKeys.includes(r.key))
                .map((r) => (r as { key: string }).key)
                .map((k) => `f:${k}`),
              getCheckboxProps: (row) => ({
                // 文件夹不可勾;文件按类型栏扩展名过滤
                disabled:
                  row.kind === 'folder' ||
                  !isExtAllowed((row as { name: string }).name, accessType),
              }),
              onChange: (_keys, selectedRows) => {
                const keys = selectedRows
                  .filter((r) => r.kind === 'file')
                  .map((r) => (r as { key: string }).key);
                emit(keys);
              },
            }}
          />
        )}
      </Spin>
    </div>
  );
};

export default FileManagerPicker;
```

> 用 `npx antd info Table` 核对 `rowSelection`/`getCheckboxProps` 在 antd v6 的签名后再写。

- [ ] **Step 2: 类型检查**

Run: `cd frontend && npm run tsc`
Expected: 无类型错误。

- [ ] **Step 3: 提交**

```bash
git add frontend/src/pages/ingest/access/FileManagerPicker.tsx
git commit -m "feat(access): 文件管理对象选择器(按类型栏过滤)"
```

---

## Task 10: 前端 — UploadModal(双导入弹框)

**Files:**
- Create: `frontend/src/pages/ingest/access/UploadModal.tsx`

- [ ] **Step 1: 实现**

```tsx
import { UploadOutlined } from '@ant-design/icons';
import type { UploadProps } from 'antd';
import {
  Alert,
  Button,
  Modal,
  Radio,
  Select,
  Space,
  Upload,
  message,
} from 'antd';
import { useEffect, useState } from 'react';
import {
  hostPlatformFiles,
  listCategories,
  uploadDataset,
} from '@/services/data-platform';
import type { AccessType } from './constants';
import { acceptOf, isExtAllowed } from './constants';
import FileManagerPicker, { type PlatformSelection } from './FileManagerPicker';

interface Props {
  open: boolean;
  accessType: AccessType;
  onClose: () => void;
  /** 成功入库后通知宿主刷新列表 */
  onDone: () => void;
}

const MAX_FILE_SIZE = 200 * 1024 * 1024;

/** 数据接入上传弹框:本地上传 或 文件管理零拷贝引入。 */
const UploadModal: React.FC<Props> = ({ open, accessType, onClose, onDone }) => {
  const [messageApi, contextHolder] = message.useMessage();
  const [categoryId, setCategoryId] = useState<string>();
  const [categoryOptions, setCategoryOptions] = useState<
    { label: string; value: string }[]
  >([]);
  const [mode, setMode] = useState<'local' | 'platform'>('local');
  const [sel, setSel] = useState<PlatformSelection>({ bucket: '', keys: [] });
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    if (!open) return;
    setMode('local');
    setSel({ bucket: '', keys: [] });
    listCategories()
      .then((res) =>
        setCategoryOptions(res.data.map((c) => ({ label: c.name, value: c.id }))),
      )
      .catch(() => undefined);
  }, [open]);

  const beforeUpload: NonNullable<UploadProps['beforeUpload']> = (file) => {
    if (!isExtAllowed(file.name, accessType)) {
      messageApi.error(
        `不支持的文件格式:${file.name},「${accessType.label}」仅支持 ${accessType.extensions.join('、')}`,
      );
      return Upload.LIST_IGNORE;
    }
    if (file.size > MAX_FILE_SIZE) {
      messageApi.error(`文件 ${file.name} 超过 200MB 大小限制`);
      return Upload.LIST_IGNORE;
    }
    return true;
  };

  const customRequest: NonNullable<UploadProps['customRequest']> = async (opts) => {
    const { file, onSuccess, onError } = opts;
    const formData = new FormData();
    formData.append('file', file as File);
    formData.append('data_type', accessType.key);
    if (categoryId) formData.append('categoryId', categoryId);
    try {
      const res = await uploadDataset(formData);
      onSuccess?.(res);
      messageApi.success(`${(file as File).name} 已接入为数据集「${res.data.name}」`);
      onDone();
    } catch (err) {
      onError?.(err as Error);
      messageApi.error(`${(file as File).name} 接入失败(文件可能损坏或无法解析)`);
    }
  };

  const handlePlatformOk = async () => {
    if (!sel.bucket || sel.keys.length === 0) {
      messageApi.error('请先在文件管理中勾选至少一个文件');
      return;
    }
    setSubmitting(true);
    try {
      const res = await hostPlatformFiles({
        bucket: sel.bucket,
        keys: sel.keys,
        dataType: accessType.key,
        categoryId,
      });
      messageApi.success(`已零拷贝接入 ${res.data.length} 个文件`);
      onDone();
      onClose();
    } catch {
      messageApi.error('文件管理接入失败,请重试');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <Modal
      title={`上传文件 · ${accessType.label}`}
      open={open}
      destroyOnHidden
      onCancel={onClose}
      width={760}
      footer={
        mode === 'platform'
          ? [
              <Button key="cancel" onClick={onClose}>
                取消
              </Button>,
              <Button
                key="ok"
                type="primary"
                loading={submitting}
                onClick={handlePlatformOk}
              >
                接入
              </Button>,
            ]
          : [
              <Button key="close" onClick={onClose}>
                关闭
              </Button>,
            ]
      }
    >
      {contextHolder}
      <Space direction="vertical" style={{ width: '100%' }} size="middle">
        <Space>
          <span>选择分类:</span>
          <Select
            allowClear
            showSearch
            optionFilterProp="label"
            placeholder="请选择分类(可选)"
            style={{ width: 320 }}
            options={categoryOptions}
            value={categoryId}
            onChange={setCategoryId}
          />
        </Space>
        <Space>
          <span>导入方式:</span>
          <Radio.Group
            value={mode}
            onChange={(e) => setMode(e.target.value)}
            options={[
              { label: '本地文件上传', value: 'local' },
              { label: '文件管理文件上传', value: 'platform' },
            ]}
            optionType="button"
          />
        </Space>

        {mode === 'local' ? (
          <Upload
            multiple
            accept={acceptOf(accessType)}
            beforeUpload={beforeUpload}
            customRequest={customRequest}
            showUploadList
          >
            <Button icon={<UploadOutlined />}>选择文件</Button>
          </Upload>
        ) : (
          <>
            <Alert
              type="info"
              showIcon
              message="从文件管理选择的文件为零拷贝引用,不复制副本;删除走「取消托管」,不会删源对象。"
            />
            <FileManagerPicker accessType={accessType} onChange={setSel} />
          </>
        )}
      </Space>
    </Modal>
  );
};

export default UploadModal;
```

> 写前用 `npx antd info Radio` / `npx antd info Upload` / `npx antd info Modal` 核对 v6 API(尤其 `Modal` 的 `destroyOnHidden` 在本仓库已用、`Radio.Group` 的 `optionType`)。

- [ ] **Step 2: 类型检查**

Run: `cd frontend && npm run tsc`
Expected: 无类型错误。

- [ ] **Step 3: 提交**

```bash
git add frontend/src/pages/ingest/access/UploadModal.tsx
git commit -m "feat(access): 双导入上传弹框(本地 + 文件管理零拷贝)"
```

---

## Task 11: 前端 — 数据接入页(左栏 + 表格 + SQL 引导)

**Files:**
- Create: `frontend/src/pages/ingest/access/index.tsx`
- Test: `frontend/src/pages/ingest/access/index.test.tsx`

- [ ] **Step 1: 写失败测试**

参照 `src/pages/ingest/datasources/index.test.tsx` 的 mock 模式:

```tsx
import { render, screen, waitFor } from '@testing-library/react';
import AccessPage from './index';

jest.mock('@/services/data-platform', () => ({
  listDatasets: jest.fn().mockResolvedValue({ data: [], total: 0, success: true }),
  listCategories: jest.fn().mockResolvedValue({ data: [], success: true }),
  getDatasetDownloadUrl: jest.fn(),
  deleteDataset: jest.fn(),
}));
jest.mock('@umijs/max', () => ({
  useAccess: () => ({ canAdmin: true }),
  Access: ({ children }: { children: React.ReactNode }) => <>{children}</>,
  history: { push: jest.fn() },
}));

describe('数据接入页', () => {
  it('渲染 8 个类型栏', async () => {
    render(<AccessPage />);
    expect(await screen.findByText('CSV/TSV接入')).toBeInTheDocument();
    expect(screen.getByText('SQL接入')).toBeInTheDocument();
    expect(screen.getByText('日志接入')).toBeInTheDocument();
  });

  it('默认栏(CSV/TSV)以 dataType=csv-tsv 拉列表', async () => {
    const { listDatasets } = require('@/services/data-platform');
    render(<AccessPage />);
    await waitFor(() =>
      expect(listDatasets).toHaveBeenCalledWith(
        expect.objectContaining({ dataType: 'csv-tsv' }),
      ),
    );
  });
});
```

> 实际 mock 的服务名以 index.tsx 真正 import 为准(下载/删除函数名先 `grep` 现有 api.ts:`getDatasetDownloadUrl`、`deleteDataset` 等);测试里对齐。

- [ ] **Step 2: 跑测试,确认失败**

Run: `cd frontend && npm run test -- access/index.test`
Expected: FAIL — 模块不存在。

- [ ] **Step 3: 实现**

```tsx
import { AppstoreAddOutlined, UploadOutlined } from '@ant-design/icons';
import type { ActionType, ProColumns } from '@ant-design/pro-components';
import { PageContainer, ProTable } from '@ant-design/pro-components';
import { history, useAccess } from '@umijs/max';
import { Button, Input, Layout, Menu, Result, Select, Space, Tag } from 'antd';
import { useCallback, useEffect, useRef, useState } from 'react';
import CategoryManager from '@/components/CategoryManager';
import {
  deleteDataset,
  getDatasetDownloadUrl,
  listCategories,
  listDatasets,
} from '@/services/data-platform';
import { ACCESS_TYPES, formatFileSize } from './constants';
import UploadModal from './UploadModal';

const { Sider, Content } = Layout;

const AccessPage: React.FC = () => {
  const access = useAccess();
  const actionRef = useRef<ActionType | null>(null);
  const [typeKey, setTypeKey] = useState<string>(ACCESS_TYPES[0].key);
  const [keyword, setKeyword] = useState<string>();
  const [categoryId, setCategoryId] = useState<string>();
  const [categoryOptions, setCategoryOptions] = useState<
    { label: string; value: string }[]
  >([]);
  const [uploadOpen, setUploadOpen] = useState(false);
  const [catOpen, setCatOpen] = useState(false);

  const accessType = ACCESS_TYPES.find((t) => t.key === typeKey)!;

  const loadCategories = useCallback(async () => {
    try {
      const res = await listCategories();
      setCategoryOptions(res.data.map((c) => ({ label: c.name, value: c.id })));
    } catch {
      // 静默
    }
  }, []);

  useEffect(() => {
    loadCategories();
  }, [loadCategories]);

  useEffect(() => {
    actionRef.current?.reload();
  }, [typeKey, keyword, categoryId]);

  const columns: ProColumns<DataPlatform.Dataset>[] = [
    { title: '文件名', dataIndex: 'name', ellipsis: true },
    {
      title: '分类',
      dataIndex: 'categoryName',
      width: 140,
      render: (_, r) => (r.categoryName ? <Tag>{r.categoryName}</Tag> : '-'),
    },
    {
      title: '类型',
      dataIndex: 'dataType',
      width: 120,
      render: (_, r) => (r.dataType ? <Tag color="blue">{r.dataType}</Tag> : '-'),
    },
    { title: '创建人', dataIndex: 'creator', width: 100 },
    { title: '创建时间', dataIndex: 'createdAt', width: 180, valueType: 'dateTime' },
    {
      title: '操作',
      valueType: 'option',
      width: 160,
      render: (_, r) => [
        <a
          key="download"
          onClick={async () => {
            const res = await getDatasetDownloadUrl(r.id);
            window.open(res.data.url, '_blank');
          }}
        >
          下载
        </a>,
        access.canAdmin ? (
          <a
            key="delete"
            style={{ color: 'var(--ant-color-error, #ff4d4f)' }}
            onClick={async () => {
              await deleteDataset(r.id);
              actionRef.current?.reload();
            }}
          >
            删除
          </a>
        ) : null,
      ],
    },
  ];

  const menuItems = ACCESS_TYPES.map((t) => ({ key: t.key, label: t.label }));

  return (
    <PageContainer>
      <Layout style={{ background: 'transparent' }}>
        <Sider width={160} theme="light" style={{ borderRadius: 8 }}>
          <Menu
            mode="inline"
            selectedKeys={[typeKey]}
            items={menuItems}
            onClick={({ key }) => setTypeKey(key)}
            style={{ borderInlineEnd: 'none' }}
          />
        </Sider>
        <Content style={{ paddingInlineStart: 16 }}>
          {accessType.key === 'sql' ? (
            <Result
              status="info"
              title="SQL 接入走「数据源管理」"
              subTitle="数据库接入请在数据源管理中创建 SQL 连接,再用采集任务拉取入库。"
              extra={[
                <Button
                  key="ds"
                  type="primary"
                  onClick={() => history.push('/ingest/datasources')}
                >
                  去数据源管理
                </Button>,
                <Button key="task" onClick={() => history.push('/ingest/tasks')}>
                  去采集任务
                </Button>,
              ]}
            />
          ) : (
            <ProTable<DataPlatform.Dataset>
              headerTitle={accessType.label}
              actionRef={actionRef}
              rowKey="id"
              search={false}
              columns={columns}
              toolBarRender={() => [
                <Space key="filters">
                  <Select
                    allowClear
                    placeholder="选择分类"
                    style={{ width: 180 }}
                    options={categoryOptions}
                    value={categoryId}
                    onChange={setCategoryId}
                  />
                  <Input.Search
                    allowClear
                    placeholder="文件名搜索"
                    style={{ width: 200 }}
                    onSearch={(v) => setKeyword(v || undefined)}
                  />
                </Space>,
                <Button
                  key="upload"
                  type="primary"
                  icon={<UploadOutlined />}
                  onClick={() => setUploadOpen(true)}
                >
                  上传
                </Button>,
                <Button
                  key="cat"
                  icon={<AppstoreAddOutlined />}
                  onClick={() => setCatOpen(true)}
                >
                  分类管理
                </Button>,
              ]}
              request={async (params) => {
                const { current, pageSize } = params;
                const res = await listDatasets({
                  current,
                  pageSize,
                  dataType: accessType.key,
                  name: keyword,
                  categoryId,
                });
                return { data: res.data, total: res.total, success: res.success };
              }}
            />
          )}
        </Content>
      </Layout>

      <UploadModal
        open={uploadOpen}
        accessType={accessType}
        onClose={() => setUploadOpen(false)}
        onDone={() => actionRef.current?.reload()}
      />
      <CategoryManager
        open={catOpen}
        canAdmin={access.canAdmin}
        onClose={() => setCatOpen(false)}
        onChanged={() => {
          loadCategories();
          actionRef.current?.reload();
        }}
      />
    </PageContainer>
  );
};

export default AccessPage;
```

> 核对点(实现前 `grep`/`antd info`):
> - `listDatasets` 入参是否支持 `dataType`/`name`/`categoryId`(Task 已确认后端支持;前端 typings 里同名字段确认);
> - `getDatasetDownloadUrl`/`deleteDataset` 真实函数名与签名(若不存在,用现有数据集下载/删除函数名替换并同步测试 mock);
> - `Dataset` 类型是否含 `categoryName` 字段(列表接口已返回 category_name)。

- [ ] **Step 4: 跑测试,确认通过**

Run: `cd frontend && npm run test -- access/index.test`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add frontend/src/pages/ingest/access/index.tsx frontend/src/pages/ingest/access/index.test.tsx
git commit -m "feat(access): 数据接入页(类型栏 + 列表 + SQL 引导)"
```

---

## Task 12: 删除旧本地上传页 + 全量校验

**Files:**
- Delete: `frontend/src/pages/ingest/upload/index.tsx`、`frontend/src/pages/ingest/upload/utils.ts`

- [ ] **Step 1: 确认无残留引用**

Run: `cd frontend && grep -rn "ingest/upload\|pages/ingest/upload\|menu.ingest.upload" src config --include=*.ts --include=*.tsx | grep -v "\.umi"`
Expected: 仅可能出现在 routes.ts 的重定向(`redirect: '/ingest/access'`)——那是路径字符串,非组件引用。无 `./ingest/upload` 组件引用即可删。

- [ ] **Step 2: 删除目录**

```bash
git rm frontend/src/pages/ingest/upload/index.tsx frontend/src/pages/ingest/upload/utils.ts
```

- [ ] **Step 3: 全量 lint + 测试 + 类型**

Run:
```bash
cd frontend && npm run tsc && npm run lint && npx antd lint ./src && npm run test
```
Expected: 全绿。若 `.umi` 缓存报旧 upload chunk,`rm -rf src/.umi` 后重试。

- [ ] **Step 4: 起服务人工验证(真实后端)**

Run(两个终端,或用项目 slash command `/adp-server` + `/adp-web`):
```bash
# 后端
cd backend && <按 backend/CLAUDE.md 启动,端口 18003>
# 前端(连真实后端)
cd frontend && npm run dev
```
人工核对验收标准(见 spec §7):
- 菜单出现「数据接入」,「本地上传」消失;访问 `/ingest/upload` 自动跳 `/ingest/access`。
- 8 个类型栏齐全;CSV/TSV 本地上传成功入库并出现在列表;图像栏上传 png 成功(列表显示,预览置灰)。
- 文件管理零拷贝:在某栏选「文件管理文件上传」勾选对象 → 接入成功,列表新增 hosted 数据集。
- 分类下拉 + 文件名搜索过滤生效;分类管理增改删联动。
- SQL 栏显示引导页 + 两个跳转按钮可达。

- [ ] **Step 5: 提交**

```bash
git add -A
git commit -m "refactor(ingest): 删除旧本地上传页,数据接入全量替换"
```

---

## Self-Review(规划者已核对)

- **Spec 覆盖**:类型栏(Task 6/11)、双导入(Task 10)、零拷贝(Task 3/5)、二进制 raw(Task 1/2)、预览守卫与回退(Task 4)、SQL 引导(Task 11)、菜单替换(Task 8)、删除旧页(Task 12)、测试(各 Task + Task 12 全量)。spec §2–§7 均有对应任务。
- **占位扫描**:无 TBD/TODO;所有代码步骤给出完整代码。少数"实现前用 `grep`/`antd info` 核对"为真实存在的接口对齐动作(下载/删除函数名、antd v6 API、camel 别名),非占位。
- **类型一致**:`AccessType`/`acceptOf`/`isExtAllowed`/`getExtension`/`formatFileSize`(Task 6)在 Task 9/10/11 一致引用;`PlatformSelection`(Task 9)被 Task 10 复用;`hostPlatformFiles`/`PlatformHostParams`(Task 7)被 Task 10 调用;后端 `land_upload_raw`/`BINARY_FORMATS`/`INGESTABLE_FORMATS`/`platform_config`/`PlatformHostRequest` 跨 Task 1–5 命名一致。
