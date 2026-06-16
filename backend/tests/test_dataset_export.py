"""消费导出契约(端到端闭环终点):GET /dataset-versions/{id}/download。

为什么重要:平台原本只有逐对象 member-url(预览语义),managed 本地结构化版本
(jsonl/parquet)**没有任何下载出口** —— 数据进得来、出不去,闭环在消费终端断开。
本端点是发布门的消费侧对偶,把「published ⟹ 可被算法工程师取走训练集」落成硬约束:

- 门控:仅 publish_status=published 可下载;draft / unpublished → 409(草稿区不外流)。
- managed 本地版本 → 直接流式下发文件(FileResponse,强制下载)。
- s3 背书版本(hosted/manifest)→ 302 跳转预签名 GET URL(浏览器直连对象存储)。

子进程/对象存储层打桩,只验证门控与分发分支,不连真实 MinIO。
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.dataset import Dataset
from app.models.dataset_version import DatasetVersion

pytestmark = pytest.mark.asyncio

DATASET_ID = "dset-ex1"


@pytest_asyncio.fixture(autouse=True)
async def _seed_dataset(session_factory: async_sessionmaker) -> None:
    async with session_factory() as session:
        session.add(Dataset(id=DATASET_ID, name="导出测试集"))
        await session.commit()


async def _add_version(
    session_factory: async_sessionmaker,
    *,
    version_id: str,
    storage_uri: str,
    version_no: int = 1,
    publish_status: str = "draft",
    origin: str = "managed",
    fmt: str = "jsonl",
    source_datasource_id: str | None = None,
) -> None:
    async with session_factory() as session:
        session.add(
            DatasetVersion(
                id=version_id,
                dataset_id=DATASET_ID,
                version_no=version_no,
                storage_uri=storage_uri,
                format=fmt,
                rows=1,
                origin=origin,
                source_datasource_id=source_datasource_id,
                scan_verdict="passed",
                publish_status=publish_status,
            )
        )
        await session.commit()


# --- 门控:仅 published 可下载 --------------------------------------------
async def test_download_blocked_when_draft(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    await _add_version(
        session_factory, version_id="dsv-draft", storage_uri="/tmp/x.jsonl"
    )
    resp = await client.get("/api/v1/dataset-versions/dsv-draft/download")
    assert resp.status_code == 409
    assert resp.json()["success"] is False
    assert "已发布" in resp.json()["message"]


async def test_download_blocked_when_unpublished(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    await _add_version(
        session_factory,
        version_id="dsv-unpub",
        storage_uri="/tmp/x.jsonl",
        publish_status="unpublished",
    )
    resp = await client.get("/api/v1/dataset-versions/dsv-unpub/download")
    assert resp.status_code == 409


async def test_download_version_not_found(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/dataset-versions/dsv-none/download")
    assert resp.status_code == 404


# --- managed 本地版本:流式下发文件 ---------------------------------------
async def test_download_managed_local_streams_file(
    client: AsyncClient, session_factory: async_sessionmaker, tmp_path
) -> None:
    """已发布 managed 本地版本:直接下发产物文件字节,带 attachment 文件名。"""
    data_file = tmp_path / "data.jsonl"
    body = b'{"text":"hello"}\n{"text":"world"}\n'
    data_file.write_bytes(body)
    await _add_version(
        session_factory,
        version_id="dsv-local",
        storage_uri=str(data_file),
        version_no=2,
        publish_status="published",
    )

    resp = await client.get("/api/v1/dataset-versions/dsv-local/download")
    assert resp.status_code == 200, resp.text
    assert resp.content == body
    # 强制下载:Content-Disposition attachment + 文件名带数据集与版本号
    cd = resp.headers.get("content-disposition", "")
    assert "attachment" in cd
    assert f"{DATASET_ID}_v2.jsonl" in cd


async def test_download_managed_local_missing_file(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    """已发布但产物文件缺失 → 410(诚实报缺,不冒 500)。"""
    await _add_version(
        session_factory,
        version_id="dsv-gone",
        storage_uri="/tmp/definitely-missing-adp-export.jsonl",
        publish_status="published",
    )
    resp = await client.get("/api/v1/dataset-versions/dsv-gone/download")
    assert resp.status_code == 410


# --- s3 背书版本:302 跳转预签名 URL --------------------------------------
async def test_download_s3_redirects_to_presigned(
    client: AsyncClient,
    session_factory: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """已发布 hosted/manifest 版本:签发预签名 GET 并 302/307 跳转,不本地流式。"""
    from app.api.v1 import datasets as ds

    fake_url = "http://minio.local/uploads/x/manifest.jsonl?sig=abc"

    async def _fake_presigned(cfg, bucket, key):
        assert (bucket, key) == ("uploads", "dset-ex1/v3/manifest.jsonl")
        return fake_url

    monkeypatch.setattr(ds, "platform_config", lambda: {"endpoint": "x"})
    monkeypatch.setattr(ds, "presigned_get_url", _fake_presigned)

    await _add_version(
        session_factory,
        version_id="dsv-s3",
        storage_uri="s3://uploads/dset-ex1/v3/manifest.jsonl",
        version_no=3,
        publish_status="published",
        origin="hosted",
        fmt="manifest",
    )

    resp = await client.get(
        "/api/v1/dataset-versions/dsv-s3/download", follow_redirects=False
    )
    assert resp.status_code in (302, 307)
    assert resp.headers["location"] == fake_url
