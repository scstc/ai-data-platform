"""POST /dataset-versions/{id}/export-s3 路由测试(下载/导出至 S3)。

策略同 test_preview_route:FAKE session + monkeypatch 物化/上传,免真实 DB/MinIO。
锁住:发布门(仅 published 放行)、目标数据源类型校验、读源写目标、
以及「绝不回写托管源对象」红线。
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import app.api.v1.datasets as datasets_mod
from app.api.v1.datasets import router
from app.core.db import get_session
from app.models.dataset_version import DatasetVersion
from app.models.datasource import DataSource
from app.schemas.dataset import DatasetMemberRead


class _FakeSession:
    """最小 async session:.get(model, id) 从分模型字典里取 canned 实体或 None。"""

    def __init__(self, by_model: dict[type, dict[str, Any]] | None = None) -> None:
        self._by_model = by_model or {}

    async def get(self, model_cls: type, id_: str) -> Any:  # noqa: ANN401
        return self._by_model.get(model_cls, {}).get(id_)


def _make_version(vid: str, **kw: Any) -> DatasetVersion:
    defaults: dict[str, Any] = {
        "id": vid,
        "dataset_id": "dset-1",
        "version_no": 1,
        "storage_uri": "s3://uploads/dset-1/v1/data.jsonl",
        "format": "jsonl",
        "origin": "managed",
        "publish_status": "published",
    }
    defaults.update(kw)
    return DatasetVersion(**defaults)


def _make_ds(ds_id: str, **kw: Any) -> DataSource:
    defaults: dict[str, Any] = {
        "id": ds_id,
        "name": f"ds-{ds_id}",
        "type": "s3",
        "status": "connected",
        "config": {"endpoint": "http://x:9000", "accessKey": "a", "secretKey": "s"},
        "creator": "admin",
    }
    defaults.update(kw)
    return DataSource(**defaults)


@pytest.fixture
def session() -> _FakeSession:
    return _FakeSession()


@pytest.fixture
def app(session: _FakeSession) -> FastAPI:
    f = FastAPI()
    f.include_router(router, prefix="/api/v1")

    async def _override():
        yield session

    f.dependency_overrides[get_session] = _override
    return f


async def _post(app: FastAPI, vid: str, body: dict) -> Any:  # noqa: ANN401
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        return await c.post(f"/api/v1/dataset-versions/{vid}/export-s3", json=body)


async def test_missing_version_returns_404(app: FastAPI) -> None:
    resp = await _post(app, "dsv-nope", {"datasourceId": "ds-1", "bucket": "b"})
    assert resp.status_code == 404
    assert resp.json()["success"] is False


async def test_draft_version_not_gated(
    app: FastAPI, session: _FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """门控已放开:草稿版本也可导出(不再 409),走到成员上传逻辑并成功。"""
    session._by_model = {
        DatasetVersion: {"dsv-1": _make_version("dsv-1", publish_status="draft")},
        DataSource: {"ds-s3": _make_ds("ds-s3")},
    }
    with tempfile.NamedTemporaryFile(
        "w", suffix=".jsonl", delete=False, encoding="utf-8"
    ) as fp:
        fp.write('{"text":"hi"}\n')
        local_path = fp.name

    async def _fake_members(version: Any, sess: Any) -> list[DatasetMemberRead]:
        return [
            DatasetMemberRead(
                name="data.jsonl", key=local_path, bucket="", format="jsonl", size=14
            )
        ]

    async def _fake_upload(*a: Any, **k: Any) -> None:
        return None

    monkeypatch.setattr(datasets_mod, "_members_of", _fake_members)
    monkeypatch.setattr(datasets_mod, "upload_object", _fake_upload)
    resp = await _post(app, "dsv-1", {"datasourceId": "ds-s3", "bucket": "out"})
    Path(local_path).unlink(missing_ok=True)
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["exported"] == 1


async def test_target_datasource_not_s3_returns_400(
    app: FastAPI, session: _FakeSession
) -> None:
    session._by_model = {
        DatasetVersion: {"dsv-1": _make_version("dsv-1")},
        DataSource: {"ds-db": _make_ds("ds-db", type="database")},
    }
    resp = await _post(app, "dsv-1", {"datasourceId": "ds-db", "bucket": "b"})
    assert resp.status_code == 400
    assert "s3" in resp.json()["message"]


async def test_happy_path_uploads_local_member(
    app: FastAPI, session: _FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """本地成员:读盘字节 → 上传到目标桶/前缀,返回导出数与目标 URI。"""
    session._by_model = {
        DatasetVersion: {"dsv-1": _make_version("dsv-1")},
        DataSource: {"ds-s3": _make_ds("ds-s3")},
    }
    with tempfile.NamedTemporaryFile(
        "w", suffix=".jsonl", delete=False, encoding="utf-8"
    ) as fp:
        fp.write('{"text":"hi"}\n')
        local_path = fp.name

    async def _fake_members(version: Any, sess: Any) -> list[DatasetMemberRead]:
        return [
            DatasetMemberRead(
                name="data.jsonl", key=local_path, bucket="", format="jsonl", size=14
            )
        ]

    captured: dict[str, Any] = {}

    async def _fake_upload(
        cfg: Any, bucket: str, key: str, data: Any, length: int, **kw: Any
    ) -> None:
        captured["bucket"] = bucket
        captured["key"] = key
        captured["length"] = length

    monkeypatch.setattr(datasets_mod, "_members_of", _fake_members)
    monkeypatch.setattr(datasets_mod, "upload_object", _fake_upload)

    resp = await _post(
        app, "dsv-1", {"datasourceId": "ds-s3", "bucket": "out", "prefix": "exp/"}
    )
    Path(local_path).unlink(missing_ok=True)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["exported"] == 1
    assert body["data"]["target"] == "s3://out/exp"
    assert captured["bucket"] == "out"
    assert captured["key"] == "exp/data.jsonl"


async def test_missing_local_member_reports_reason(
    app: FastAPI, session: _FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """本地成员文件不存在(数据在其它部署机)→ 410,诚实回因(含路径),不笼统报空。"""
    session._by_model = {
        DatasetVersion: {"dsv-1": _make_version("dsv-1")},
        DataSource: {"ds-s3": _make_ds("ds-s3")},
    }

    async def _fake_members(version: Any, sess: Any) -> list[DatasetMemberRead]:
        return [
            DatasetMemberRead(
                name="data.jsonl",
                key="/data/datasets/dset-x/v1/data.jsonl",
                bucket="",
                format="jsonl",
                size=10,
            )
        ]

    monkeypatch.setattr(datasets_mod, "_members_of", _fake_members)
    resp = await _post(app, "dsv-1", {"datasourceId": "ds-s3", "bucket": "out"})
    assert resp.status_code == 410
    msg = resp.json()["message"]
    assert "本地文件不存在" in msg
    assert "/data/datasets/dset-x/v1/data.jsonl" in msg


async def test_blocks_writeback_to_hosted_source(
    app: FastAPI, session: _FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """红线:导出目标 == 托管源对象(同源同桶同 key)→ 400,绝不回写源。"""
    session._by_model = {
        DatasetVersion: {
            "dsv-1": _make_version(
                "dsv-1",
                origin="hosted",
                storage_uri="s3://srcb/p/file.jsonl",
                source_datasource_id="ds-s3",
            )
        },
        DataSource: {"ds-s3": _make_ds("ds-s3")},
    }

    async def _fake_members(version: Any, sess: Any) -> list[DatasetMemberRead]:
        return [
            DatasetMemberRead(
                name="file.jsonl",
                key="p/file.jsonl",
                bucket="srcb",
                format="jsonl",
                size=10,
            )
        ]

    async def _fake_cfg(version: Any, sess: Any) -> dict:
        return {"endpoint": "http://x:9000", "accessKey": "a", "secretKey": "s"}

    async def _fake_cached(cfg: Any, bucket: str, key: str) -> bytes:
        return b'{"text":"hi"}\n'

    monkeypatch.setattr(datasets_mod, "_members_of", _fake_members)
    monkeypatch.setattr(datasets_mod, "_version_storage_cfg", _fake_cfg)
    monkeypatch.setattr(datasets_mod, "cached_bytes", _fake_cached)

    resp = await _post(
        app, "dsv-1", {"datasourceId": "ds-s3", "bucket": "srcb", "prefix": "p"}
    )
    assert resp.status_code == 400
    assert "回写源" in resp.json()["message"]
