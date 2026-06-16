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


@pytest.mark.asyncio
async def test_binary_dataset_blocked_from_processing(
    client, seed_users, monkeypatch, tmp_path
):
    """二进制数据集版本提交加工/试跑 → 提前 400(而非跑起来才失败)。"""
    from app.services import landing as landing_mod
    from app.services.auth import sign_token

    monkeypatch.setattr(landing_mod.settings, "datasets_dir", str(tmp_path))
    # 加工创建端点 require_admin(jobs.py),以 admin 身份请求才能走到二进制门控
    client.cookies.set("adp_session", sign_token("admin"))
    files = {"file": ("clip.mp4", b"\x00\x00\x00\x18ftypmp42rawbytes", "video/mp4")}
    resp = await client.post(
        "/api/v1/datasets/upload", files=files, data={"data_type": "video"}
    )
    version_id = resp.json()["data"]["versions"][0]["id"]

    # 建加工任务:二进制版本 → 提前 400(含「二进制」),不真正起任务
    resp = await client.post(
        "/api/v1/jobs",
        json={
            "name": "二进制加工",
            "datasetVersionId": version_id,
            "operators": [{"name": "text_length_filter"}],
        },
    )
    assert resp.status_code == 400
    assert "二进制" in resp.json()["message"]

    # 样例试跑同样提前 400
    resp = await client.post(
        "/api/v1/jobs/preview",
        json={
            "datasetVersionId": version_id,
            "operators": [{"name": "text_length_filter"}],
            "sampleSize": 5,
        },
    )
    assert resp.status_code == 400
    assert "二进制" in resp.json()["message"]


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


# ---------------------------------------------------------------------------
# 媒体批量接入:一批文件 → 平台 MinIO → 一个 manifest 数据集
# ---------------------------------------------------------------------------
def _mem_store_patch(monkeypatch, module) -> dict:
    """给某 module(datasets / external_store)打补丁:用内存字典模拟平台 MinIO。"""
    import tempfile
    from pathlib import Path as _P

    store: dict[tuple[str, str], bytes] = {}
    monkeypatch.setattr(
        module,
        "platform_config",
        lambda: {"endpoint": "x", "accessKey": "a", "secretKey": "b"},
    )

    async def fake_upload(cfg, bucket, key, data, length, content_type="x"):
        store[(bucket, key)] = data.read()

    async def fake_download(cfg, bucket, key):
        fd, name = tempfile.mkstemp()
        import os as _os

        _os.close(fd)
        p = _P(name)
        p.write_bytes(store[(bucket, key)])
        return p

    async def fake_presign(cfg, bucket, key, expires_seconds=600):
        return f"http://minio/{bucket}/{key}?sig=test"

    async def fake_remove(cfg, bucket, key):
        store.pop((bucket, key), None)

    if hasattr(module, "upload_object"):
        monkeypatch.setattr(module, "upload_object", fake_upload)
    monkeypatch.setattr(module, "download_to_temp", fake_download)
    if hasattr(module, "presigned_get_url"):
        monkeypatch.setattr(module, "presigned_get_url", fake_presign)
    if hasattr(module, "remove_object"):
        monkeypatch.setattr(module, "remove_object", fake_remove)
    return store


@pytest.mark.asyncio
async def test_upload_media_creates_one_manifest_dataset(client, monkeypatch):
    """一批图 → 一个 manifest 数据集(format=manifest/origin=managed/rows=N);
    成员列表 / 预览 / 预签名 URL 均可用;manifest 内容符合 data-juicer 契约。"""
    import json

    from app.api.v1 import datasets as dmod

    store = _mem_store_patch(monkeypatch, dmod)

    files = [
        ("files", ("a.png", b"PNGDATA1", "image/png")),
        ("files", ("b.jpg", b"JPGDATA22", "image/jpeg")),
    ]
    resp = await client.post(
        "/api/v1/datasets/upload-media",
        files=files,
        data={"data_type": "image", "name": "我的图集"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["name"] == "我的图集"
    assert data["dataType"] == "image"
    assert len(data["versions"]) == 1
    v = data["versions"][0]
    assert v["format"] == "manifest"
    assert v["origin"] == "managed"
    assert v["rows"] == 2
    version_id = v["id"]

    # manifest 内容符合 DJ 契约:images 数组 + <__dj__image> token + 平台旁路 __member
    dataset_id = data["id"]
    manifest = store[("uploads", f"{dataset_id}/manifest.jsonl")].decode()
    rows = [json.loads(ln) for ln in manifest.splitlines() if ln.strip()]
    assert len(rows) == 2
    assert all(r["text"] == "<__dj__image>" for r in rows)
    assert all(len(r["images"]) == 1 for r in rows)
    assert all(r["__member"]["format"] in ("png", "jpg") for r in rows)

    # 成员列表
    resp = await client.get(f"/api/v1/dataset-versions/{version_id}/members")
    members = resp.json()["data"]
    assert {m["name"] for m in members} == {"a.png", "b.jpg"}

    # 预览返回成员表
    resp = await client.get(f"/api/v1/dataset-versions/{version_id}/preview")
    body = resp.json()
    assert body["total"] == 2
    assert body["columns"] == ["name", "format", "size"]

    # 预签名 URL:合法成员 key → 200;越权 key → 400
    good_key = members[0]["key"]
    resp = await client.get(
        f"/api/v1/dataset-versions/{version_id}/member-url",
        params={"key": good_key},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["url"].startswith("http")
    resp = await client.get(
        f"/api/v1/dataset-versions/{version_id}/member-url",
        params={"key": "other-ds/evil.png"},
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_materialize_manifest_rewrites_and_strips(db_session, monkeypatch):
    """manifest 物化:images 路径改写为本地相对名、成员文件就近落盘、__member 剥除;
    且 manifest 版本不被加工的二进制门控拦截。"""
    import json
    from pathlib import Path as _P

    from app.api.v1.jobs import _binary_block
    from app.models.dataset_version import DatasetVersion
    from app.services import external_store as es

    store = _mem_store_patch(monkeypatch, es)
    store[("uploads", "ds-x/manifest.jsonl")] = (
        json.dumps(
            {
                "images": ["ds-x/000000-a.png"],
                "text": "<__dj__image>",
                "__member": {
                    "bucket": "uploads",
                    "key": "ds-x/000000-a.png",
                    "name": "a.png",
                    "size": 3,
                    "format": "png",
                },
            }
        )
        + "\n"
    ).encode()
    store[("uploads", "ds-x/000000-a.png")] = b"PNG"

    v = DatasetVersion(
        id="dsv-x",
        dataset_id="ds-x",
        version_no=1,
        storage_uri="s3://uploads/ds-x/manifest.jsonl",
        format="manifest",
        origin="managed",
        rows=1,
    )
    # manifest 不属二进制门控(format=manifest),可进加工
    assert _binary_block(v) is None

    async with es.materialized_version(v, db_session) as path:
        row = json.loads(path.read_text(encoding="utf-8").strip())
        local = row["images"][0]
        assert "/" not in local  # 已改写为本地相对文件名
        assert "__member" not in row  # 平台旁路字段已剥除
        assert (_P(path).parent / local).read_bytes() == b"PNG"  # 成员就近落盘


@pytest.mark.asyncio
async def test_persist_manifest_output_uploads_and_rewrites(monkeypatch, tmp_path):
    """加工产物持久化(materialize 的逆):本地媒体回传 MinIO、images 改写为对象 key、
    补回 __member、清单写到版本前缀;产物自包含(不回指输入对象)。"""
    import json

    from app.services import external_store as es

    store = _mem_store_patch(monkeypatch, es)

    # 模拟 dj 产物:images 指向物化临时目录里的本地媒体(绝对路径)
    img = tmp_path / "000000-000000-pic.png"
    img.write_bytes(b"\x89PNG-fake")
    jsonl = tmp_path / "data.jsonl"
    jsonl.write_text(
        json.dumps({"images": [str(img)], "text": "<__dj__image>"}) + "\n",
        encoding="utf-8",
    )

    uri, rows, size = await es.persist_manifest_output(
        jsonl_path=jsonl, dataset_id="dset-x", version_no=2
    )

    assert uri == "s3://uploads/dset-x/v2/manifest.jsonl"
    assert rows == 1
    assert size == len(b"\x89PNG-fake")
    # 媒体已回传到版本前缀(自包含)
    assert ("uploads", "dset-x/v2/000000-000000-pic.png") in store
    # 清单:images 改写为对象 key + __member 指回该对象
    manifest_row = json.loads(
        store[("uploads", "dset-x/v2/manifest.jsonl")].decode().strip()
    )
    assert manifest_row["images"] == ["dset-x/v2/000000-000000-pic.png"]
    assert manifest_row["__member"]["key"] == "dset-x/v2/000000-000000-pic.png"
    assert manifest_row["__member"]["bucket"] == "uploads"


@pytest.mark.asyncio
async def test_upload_media_rejects_over_member_cap(client, monkeypatch):
    """超过成员数上限的批量 → 400(不会创建永远无法物化的数据集)。"""
    from app.api.v1 import datasets as dmod

    _mem_store_patch(monkeypatch, dmod)
    monkeypatch.setattr(dmod, "MAX_MANIFEST_MEMBERS", 2)
    files = [
        ("files", (f"x{i}.png", b"PNG", "image/png")) for i in range(3)
    ]
    resp = await client.post(
        "/api/v1/datasets/upload-media", files=files, data={"data_type": "image"}
    )
    assert resp.status_code == 400
    assert "最多" in resp.json()["message"]


@pytest.mark.asyncio
async def test_upload_media_gc_on_storage_failure(client, monkeypatch):
    """中途上传失败 → 503 且回收已写对象(remove_prefix 命中本数据集前缀),不留孤儿。"""
    from app.api.v1 import datasets as dmod
    from app.services.external_store import ExternalStoreError

    calls = {"n": 0, "gc": []}
    monkeypatch.setattr(
        dmod,
        "platform_config",
        lambda: {"endpoint": "x", "accessKey": "a", "secretKey": "b"},
    )

    async def fail_upload(cfg, bucket, key, data, length, content_type="x"):
        calls["n"] += 1
        if calls["n"] == 2:
            raise ExternalStoreError("boom")

    async def rec_remove_prefix(cfg, bucket, prefix):
        calls["gc"].append((bucket, prefix))
        return 1

    monkeypatch.setattr(dmod, "upload_object", fail_upload)
    monkeypatch.setattr(dmod, "remove_prefix", rec_remove_prefix)

    files = [
        ("files", ("a.png", b"PNG", "image/png")),
        ("files", ("b.png", b"PNG", "image/png")),
    ]
    resp = await client.post(
        "/api/v1/datasets/upload-media", files=files, data={"data_type": "image"}
    )
    assert resp.status_code == 503
    assert calls["gc"], "失败时应回收已写对象"
    assert calls["gc"][0][1].endswith("/")  # 以数据集前缀回收


@pytest.mark.asyncio
async def test_members_corrupt_manifest_returns_4xx(
    client, session_factory, monkeypatch
):
    """清单损坏 → 成员接口 4xx(不冒 500)。"""
    from app.api.v1 import datasets as dmod
    from app.models.dataset import Dataset
    from app.models.dataset_version import DatasetVersion

    store = _mem_store_patch(monkeypatch, dmod)
    store[("uploads", "ds-bad/manifest.jsonl")] = b"{not json]\n"
    async with session_factory() as session:
        session.add(Dataset(id="ds-bad", name="坏清单"))
        session.add(
            DatasetVersion(
                id="dsv-bad",
                dataset_id="ds-bad",
                version_no=1,
                storage_uri="s3://uploads/ds-bad/manifest.jsonl",
                format="manifest",
                origin="managed",
                rows=1,
            )
        )
        await session.commit()

    resp = await client.get("/api/v1/dataset-versions/dsv-bad/members")
    assert resp.status_code == 400
    assert resp.json()["success"] is False


async def _upload_two_images(client, store_owner_module, monkeypatch):
    """建一个含 2 张图的 manifest 数据集,返回 (datasetId, versionId, store)。"""
    store = _mem_store_patch(monkeypatch, store_owner_module)
    files = [
        ("files", ("a.png", b"PNGDATA1", "image/png")),
        ("files", ("b.jpg", b"JPGDATA22", "image/jpeg")),
    ]
    resp = await client.post(
        "/api/v1/datasets/upload-media", files=files, data={"data_type": "image"}
    )
    d = resp.json()["data"]
    return d["id"], d["versions"][0]["id"], store


@pytest.mark.asyncio
async def test_add_members_appends_to_manifest(client, monkeypatch):
    """追加成员:对象写入 + 清单增行 + 版本 rows 更新;成员接口可见。"""
    from app.api.v1 import datasets as dmod

    did, vid, store = await _upload_two_images(client, dmod, monkeypatch)

    resp = await client.post(
        f"/api/v1/datasets/{did}/members",
        files=[("files", ("c.png", b"PNGC", "image/png"))],
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["rows"] == 3

    members = (
        await client.get(f"/api/v1/dataset-versions/{vid}/members")
    ).json()["data"]
    assert {m["name"] for m in members} == {"a.png", "b.jpg", "c.png"}
    # 清单对象确实增到 3 行
    manifest = store[("uploads", f"{did}/manifest.jsonl")].decode()
    assert len([ln for ln in manifest.splitlines() if ln.strip()]) == 3


@pytest.mark.asyncio
async def test_delete_member_removes_from_manifest(client, monkeypatch):
    """删除成员:清单去行 + 对象移除 + 版本 rows 更新;越权 key → 400。"""
    from app.api.v1 import datasets as dmod

    did, vid, store = await _upload_two_images(client, dmod, monkeypatch)
    members = (
        await client.get(f"/api/v1/dataset-versions/{vid}/members")
    ).json()["data"]
    victim = next(m for m in members if m["name"] == "a.png")["key"]

    # 越权 key(不在本数据集前缀)→ 400
    bad = await client.request(
        "DELETE",
        f"/api/v1/datasets/{did}/members",
        params={"key": "other-ds/x.png"},
    )
    assert bad.status_code == 400

    resp = await client.request(
        "DELETE", f"/api/v1/datasets/{did}/members", params={"key": victim}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["rows"] == 1
    # 成员对象已从存储移除
    assert ("uploads", victim) not in store
    members = (
        await client.get(f"/api/v1/dataset-versions/{vid}/members")
    ).json()["data"]
    assert {m["name"] for m in members} == {"b.jpg"}
