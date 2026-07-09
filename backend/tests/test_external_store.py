"""外部 S3 数据托管(#18)集成测试。

真连测试 MinIO(10.60.1.60:9000,桶 datasets,账号 adpadmin)。覆盖:
- external_store:test_connection 真连成功;list_buckets 含 datasets;
  download_to_temp + normalize 一个 jsonl 对象。
- 托管全流程:host-s3 登记一个对象(数据集 origin=hosted、未下载)→ preview 取前 N
  → 对 hosted 版本跑内容审核(useLlm=false)成功且产受管新版本 → DELETE hosted →
  403 → unhost(admin)→ 平台引用消失 → **断言 S3 源对象仍在**(minio client stat)。

环境不满足时整体 skip(minio 未装 / 60 不可达),不硬失败。凭证为 60 的 demo 账号,
仅测试用途;部署红线:全程绝不调用任何 S3 删除。
"""

from __future__ import annotations

import io
import socket
import uuid

import pytest
import pytest_asyncio
from httpx import AsyncClient

# minio 未安装(如 uv sync 前)→ 整模块 skip,不硬失败
minio = pytest.importorskip("minio")
from minio.error import S3Error  # noqa: E402

pytestmark = pytest.mark.asyncio

# 10.60.1.60 的 MinIO demo 账号(仅测试用途)
_MINIO_HOST = "10.60.1.60"
_MINIO_PORT = 9000
_MINIO_ENDPOINT_HTTP = f"http://{_MINIO_HOST}:{_MINIO_PORT}"
_MINIO_ENDPOINT = f"{_MINIO_HOST}:{_MINIO_PORT}"
_ACCESS_KEY = "adpadmin"
_SECRET_KEY = "adpMinio#2026"
_BUCKET = "datasets"

# 数据源 config(s3 表单形态;endpoint 带 http:// 前缀 → secure=False)
_S3_CONFIG = {
    "endpoint": _MINIO_ENDPOINT_HTTP,
    "bucket": _BUCKET,
    "accessKey": _ACCESS_KEY,
    "secretKey": _SECRET_KEY,
}

# 一条可被 normalize_to_records 解析的 jsonl 样本(两行)
_SAMPLE_JSONL = b'{"text": "hello world"}\n{"text": "second line"}\n'


def _minio_reachable() -> bool:
    """探测 60:9000 是否可达(2s 超时);不可达则用例 skip。"""
    sock = socket.socket()
    sock.settimeout(2)
    try:
        sock.connect((_MINIO_HOST, _MINIO_PORT))
        return True
    except OSError:
        return False
    finally:
        sock.close()


# 连不上 60 → 整模块 skip(不硬失败,见 spec §8)
if not _minio_reachable():
    pytest.skip(
        f"MinIO {_MINIO_ENDPOINT} 不可达,跳过外部 S3 托管集成测试",
        allow_module_level=True,
    )


def _raw_client() -> minio.Minio:
    """直连 60 的原生 minio client(测试自管对象的播种与核验,不经平台)。"""
    return minio.Minio(
        _MINIO_ENDPOINT,
        access_key=_ACCESS_KEY,
        secret_key=_SECRET_KEY,
        secure=False,
    )


@pytest_asyncio.fixture
async def seeded_object():
    """播种一个唯一 key 的 jsonl 对象到 datasets 桶,产出 (key) 供用例引用。

    清理由测试自身负责(测试拥有该对象;平台侧绝不删源)——用例结束 remove。
    """
    client = _raw_client()
    key = f"adp-test/host-{uuid.uuid4().hex}.jsonl"
    client.put_object(
        _BUCKET,
        key,
        io.BytesIO(_SAMPLE_JSONL),
        length=len(_SAMPLE_JSONL),
        content_type="application/x-ndjson",
    )
    try:
        yield key
    finally:
        # 测试自管对象清理(非平台行为);忽略已不存在
        try:
            client.remove_object(_BUCKET, key)
        except S3Error:
            pass


@pytest_asyncio.fixture(autouse=True)
async def _admin_session(client: AsyncClient, seed_users: None) -> None:
    """create_datasource / unhost 走 require_admin:统一以 admin 身份请求。"""
    from app.services.auth import sign_token

    client.cookies.set("adp_session", sign_token("admin"))


# ---------------------------------------------------------------------------
# external_store 直测
# ---------------------------------------------------------------------------
async def test_test_connection_real_success() -> None:
    """test_connection 真连 60 成功,延迟为非负毫秒。"""
    from app.services import external_store

    ok, latency_ms, msg = await external_store.test_connection(_S3_CONFIG)
    assert ok is True
    assert latency_ms >= 0
    assert isinstance(msg, str) and msg


async def test_list_buckets_contains_datasets() -> None:
    """list_buckets 真连 60,结果含 datasets 桶。"""
    from app.services import external_store

    buckets = await external_store.list_buckets(_S3_CONFIG)
    assert _BUCKET in buckets


async def test_download_and_normalize(seeded_object: str) -> None:
    """download_to_temp 下载 + normalize_to_records 规范化一个 jsonl 对象。"""
    from app.services import external_store
    from app.services.landing import normalize_to_records

    tmp_path = await external_store.download_to_temp(
        _S3_CONFIG, _BUCKET, seeded_object
    )
    try:
        assert tmp_path.exists()
        content = tmp_path.read_bytes()
        assert content == _SAMPLE_JSONL
        records = normalize_to_records(content, "jsonl")
        assert records == [
            {"text": "hello world"},
            {"text": "second line"},
        ]
    finally:
        tmp_path.unlink(missing_ok=True)
    # 下载是只读:S3 源对象仍在
    assert external_store_object_exists(seeded_object)


def external_store_object_exists(key: str) -> bool:
    """用原生 minio client 核验对象是否仍在(stat_object 成功即存在)。"""
    client = _raw_client()
    try:
        client.stat_object(_BUCKET, key)
        return True
    except S3Error:
        return False


# ---------------------------------------------------------------------------
# 托管全流程(经 API)
# ---------------------------------------------------------------------------
async def _create_s3_datasource(client: AsyncClient) -> str:
    """经 API 建一个 s3 数据源(真探活 → connected),返回 ds id。"""
    resp = await client.post(
        "/api/v1/datasources",
        json={
            "name": "MinIO-60-test",
            "type": "s3",
            "config": dict(_S3_CONFIG),
            "description": "外部 S3 托管集成测试",
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["status"] == "connected", data
    return data["id"]


async def test_host_s3_flow(client: AsyncClient, seeded_object: str) -> None:
    """host-s3 登记 → 未下载 → preview → 审核产新版本 → 删 403 → unhost → 源仍在。"""
    ds_id = await _create_s3_datasource(client)

    # 1) 托管登记一个对象:不下载,origin=hosted、storage_uri=s3://...
    resp = await client.post(
        "/api/v1/datasets/host-s3",
        json={
            "datasourceId": ds_id,
            "bucket": _BUCKET,
            "keys": [seeded_object],
            "name": "托管样本",
            "dataType": "text",
        },
    )
    assert resp.status_code == 200, resp.text
    created = resp.json()["data"]
    assert len(created) == 1
    detail = created[0]
    assert detail["hosted"] is True
    dataset_id = detail["id"]
    version = detail["versions"][0]
    assert version["origin"] == "hosted"
    assert version["storageUri"] == f"s3://{_BUCKET}/{seeded_object}"
    assert version["sourceDatasourceId"] == ds_id
    assert version["format"] == "jsonl"
    # 登记不下载:rows 为空(size 来自 stat_object,可有)
    assert version["rows"] is None
    version_id = version["id"]

    # 列表中该数据集带 hosted 徽标
    list_resp = await client.get("/api/v1/datasets", params={"pageSize": 50})
    listed = {d["id"]: d for d in list_resp.json()["data"]}
    assert listed[dataset_id]["hosted"] is True

    # 2) 预览 hosted 版本:按需从 S3 取前 N
    prev = await client.get(
        f"/api/v1/dataset-versions/{version_id}/preview",
        params={"limit": 10},
    )
    assert prev.status_code == 200, prev.text
    prev_body = prev.json()
    assert prev_body["success"] is True
    assert prev_body["data"] == [
        {"text": "hello world"},
        {"text": "second line"},
    ]
    assert "text" in prev_body["columns"]

    # 3) 对 hosted 版本跑内容审核(useLlm=false,无检测器)→ 成功且产受管新版本
    review_resp = await client.post(
        "/api/v1/content-safety/jobs",
        json={
            "datasetVersionId": version_id,
            "name": "hosted 审核",
            "config": {
                "useLlm": False,
                "usePii": False,
                "useFlaggedWords": False,
            },
        },
    )
    assert review_resp.status_code == 200, review_resp.text
    job_body = review_resp.json()["data"]
    assert job_body["state"] == "success", job_body
    output = job_body["output"]
    assert output is not None
    tagged_version_id = output["versionId"]
    assert tagged_version_id != version_id

    # 产出的新版本是受管的(origin=review),与 hosted 输入不同
    detail_resp = await client.get(f"/api/v1/datasets/{dataset_id}")
    versions = detail_resp.json()["data"]["versions"]
    by_id = {v["id"]: v for v in versions}
    assert by_id[version_id]["origin"] == "hosted"
    assert by_id[tagged_version_id]["origin"] == "review"
    assert by_id[tagged_version_id]["sourceDatasourceId"] is None

    # 4) DELETE hosted 数据集 → 403(删源禁止)
    del_resp = await client.delete(f"/api/v1/datasets/{dataset_id}")
    assert del_resp.status_code == 403
    assert del_resp.json()["success"] is False
    # batch-delete 同样被拒
    batch_resp = await client.post(
        "/api/v1/datasets/batch-delete", json={"ids": [dataset_id]}
    )
    assert batch_resp.status_code == 403

    # 5) unhost(admin)→ 平台引用消失
    unhost_resp = await client.post(f"/api/v1/datasets/{dataset_id}/unhost")
    assert unhost_resp.status_code == 200, unhost_resp.text
    assert unhost_resp.json()["success"] is True
    gone = await client.get(f"/api/v1/datasets/{dataset_id}")
    assert gone.status_code == 404

    # 6) 断言 S3 源对象仍在(取消托管绝不动源)
    assert external_store_object_exists(seeded_object)


# ---------------------------------------------------------------------------
# upload_parquet_to_datasets 单元测试
# ---------------------------------------------------------------------------
async def test_upload_parquet_to_datasets_key_and_uri(monkeypatch):
    from app.services import external_store

    captured = {}

    async def fake_upload_object(cfg, bucket, key, data, length, content_type="application/octet-stream"):
        captured["bucket"] = bucket
        captured["key"] = key
        captured["content_type"] = content_type

    monkeypatch.setattr(
        external_store, "platform_config", lambda: {"endpoint": "e", "accessKey": "a", "secretKey": "s"}
    )
    monkeypatch.setattr(external_store.settings, "storage_minio_datasets_bucket", "adp-datasets")
    monkeypatch.setattr(external_store, "upload_object", fake_upload_object)

    uri = await external_store.upload_parquet_to_datasets("dset-abc123", 2, b"PAR1data")
    assert uri == "s3://adp-datasets/dset-abc123/v2/data.parquet"
    assert captured["key"] == "dset-abc123/v2/data.parquet"
