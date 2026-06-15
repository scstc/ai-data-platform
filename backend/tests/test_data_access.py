"""数据接入:raw landing / host-platform / preview 回退 / 物化回退测试。

本仓库 conftest 提供 `client`(httpx AsyncClient,已覆盖 get_session)与
`session_factory`,但没有名为 `db_session` 的 fixture——故本文件用 session_factory
本地构造一个 `db_session`(函数级 AsyncSession),供 service 层直测用例使用。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from app.services import landing


@pytest_asyncio.fixture
async def db_session(session_factory) -> AsyncGenerator[AsyncSession, None]:
    """函数级 AsyncSession(基于测试 engine 的 session_factory)。"""
    async with session_factory() as session:
        yield session


# ---------------------------------------------------------------------------
# Task 1: raw landing + 接入格式白名单扩展
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Task 2: upload 端点按格式分流(二进制走 raw landing)
# ---------------------------------------------------------------------------
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


# ---------------------------------------------------------------------------
# Task 3: POST /datasets/host-platform(文件管理零拷贝)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_host_platform_zero_copy(client, monkeypatch):
    """登记平台对象为受管数据集:只 stat 不下载。"""
    from app.api.v1 import datasets as datasets_mod

    monkeypatch.setattr(
        datasets_mod,
        "platform_config",
        lambda: {"endpoint": "x", "accessKey": "a", "secretKey": "b"},
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


# ---------------------------------------------------------------------------
# Task 4: preview 二进制守卫 + hosted 平台凭证回退
# 真实预览路由(见 datasets.py / test_external_store.py):
#   GET /api/v1/dataset-versions/{version_id}/preview
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_preview_hosted_platform_fallback(client, monkeypatch):
    """平台零拷贝(source_datasource_id 为空)的预览用 platform_config 取数,不报缺数据源。"""
    from app.api.v1 import datasets as datasets_mod

    monkeypatch.setattr(
        datasets_mod,
        "platform_config",
        lambda: {"endpoint": "x", "accessKey": "a", "secretKey": "b"},
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

    resp = await client.get(f"/api/v1/dataset-versions/{version_id}/preview")
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
    resp = await client.get(f"/api/v1/dataset-versions/{version_id}/preview")
    assert resp.status_code == 200
    assert resp.json()["data"] == []
    assert "不支持预览" in resp.json().get("message", "")


# ---------------------------------------------------------------------------
# Task 5: materialized_version 平台凭证回退(加工路径)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_materialized_version_platform_fallback(db_session, monkeypatch):
    """origin=hosted 且无 source_datasource_id → 用 platform_config 下载,不报缺凭证。"""
    import os
    import tempfile
    from pathlib import Path

    from app.models.dataset_version import DatasetVersion
    from app.services import external_store as es

    monkeypatch.setattr(
        es,
        "platform_config",
        lambda: {"endpoint": "x", "accessKey": "a", "secretKey": "b"},
    )

    seen = {}

    async def fake_download(cfg, bucket, key):
        seen["cfg"] = cfg
        fd, name = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        p = Path(name)
        p.write_bytes(b'{"x":1}\n')
        return p

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
