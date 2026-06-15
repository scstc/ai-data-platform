"""文件管理(#19)集成测试:真连平台 MinIO(10.60.1.60:9000)。

覆盖(同 test_external_store 的 socket 探测 + 整模块 skip 约定):
- admin 全流程:新建文件夹 → 上传 → 列出 → download-url → 删对象 → 递归删目录。
- 删除软保护:对被 hosted 版本引用的 key 删 → 409。
- RBAC:非 admin 写 → 403;匿名 → 401;非 admin 读 → 200。

平台 MinIO 凭证(60 demo 账号,仅测试用)经 monkeypatch 注入 settings.storage_minio_*,
测试用例自建 uuid 前缀隔离 + 用后清理,绝不碰既有对象。
"""

from __future__ import annotations

import socket
import uuid

import pytest
import pytest_asyncio
from httpx import AsyncClient

# minio 未安装 → 整模块 skip,不硬失败
minio = pytest.importorskip("minio")
from minio.error import S3Error  # noqa: E402

pytestmark = pytest.mark.asyncio

_MINIO_HOST = "10.60.1.60"
_MINIO_PORT = 9000
_MINIO_ENDPOINT_HTTP = f"http://{_MINIO_HOST}:{_MINIO_PORT}"
_MINIO_ENDPOINT = f"{_MINIO_HOST}:{_MINIO_PORT}"
_ACCESS_KEY = "adpadmin"
_SECRET_KEY = "adpMinio#2026"
_BUCKET = "datasets"


def _minio_reachable() -> bool:
    """探测 60:9000 是否可达(2s 超时);不可达则整模块 skip。"""
    sock = socket.socket()
    sock.settimeout(2)
    try:
        sock.connect((_MINIO_HOST, _MINIO_PORT))
        return True
    except OSError:
        return False
    finally:
        sock.close()


if not _minio_reachable():
    pytest.skip(
        f"MinIO {_MINIO_ENDPOINT} 不可达,跳过文件管理集成测试",
        allow_module_level=True,
    )


def _raw_client() -> minio.Minio:
    """直连 60 的原生 minio client(测试自管对象清理,不经平台)。"""
    return minio.Minio(
        _MINIO_ENDPOINT,
        access_key=_ACCESS_KEY,
        secret_key=_SECRET_KEY,
        secure=False,
    )


@pytest.fixture(autouse=True)
def _platform_minio_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """注入平台 MinIO 配置(测试环境不读 .env,须 monkeypatch settings)。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "storage_minio_endpoint", _MINIO_ENDPOINT_HTTP)
    monkeypatch.setattr(settings, "storage_minio_access_key", _ACCESS_KEY)
    monkeypatch.setattr(settings, "storage_minio_secret_key", _SECRET_KEY)


@pytest_asyncio.fixture
async def _login_admin(client: AsyncClient, seed_users: None) -> None:
    """以 admin 身份请求(写端点 require_admin)。"""
    from app.services.auth import sign_token

    client.cookies.set("adp_session", sign_token("admin"))


@pytest.fixture
def test_prefix() -> str:
    """唯一前缀,隔离本次用例,用后清理(递归删 + 去掉文件夹标记)。"""
    prefix = f"adp-test-files/{uuid.uuid4().hex}/"
    yield prefix
    client = _raw_client()
    try:
        for obj in client.list_objects(_BUCKET, prefix=prefix, recursive=True):
            try:
                client.remove_object(_BUCKET, obj.object_name)
            except S3Error:
                pass
    except S3Error:
        pass


async def test_admin_full_flow(
    client: AsyncClient, _login_admin: None, test_prefix: str
) -> None:
    """新建文件夹 → 上传对象 → 列出 → download-url → 删对象 → 递归删目录。"""
    folder_name = "sub"
    # 1) 新建文件夹 test_prefix + sub/
    resp = await client.post(
        "/api/v1/files/folder",
        json={"bucket": _BUCKET, "prefix": test_prefix, "name": folder_name},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["success"] is True

    # 2) 上传一个对象到 test_prefix 下
    resp = await client.post(
        "/api/v1/files/upload",
        data={"bucket": _BUCKET, "prefix": test_prefix},
        files={"file": ("sample.jsonl", b'{"text": "hi"}\n', "application/x-ndjson")},
    )
    assert resp.status_code == 200, resp.text
    uploaded_key = resp.json()["data"]["key"]
    assert uploaded_key == f"{test_prefix}sample.jsonl"

    # 3) GET /files 列出:folders 含 sub、files 含 sample.jsonl
    resp = await client.get(
        "/api/v1/files", params={"bucket": _BUCKET, "prefix": test_prefix}
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert folder_name in data["folders"]
    file_names = {f["name"] for f in data["files"]}
    assert "sample.jsonl" in file_names
    sample = next(f for f in data["files"] if f["name"] == "sample.jsonl")
    assert sample["key"] == uploaded_key
    assert sample["size"] == len(b'{"text": "hi"}\n')

    # 4) download-url 返回 presigned URL 字符串
    resp = await client.get(
        "/api/v1/files/download-url",
        params={"bucket": _BUCKET, "key": uploaded_key},
    )
    assert resp.status_code == 200, resp.text
    url = resp.json()["data"]["url"]
    assert isinstance(url, str) and url.startswith("http")

    # 5) 删对象
    resp = await client.delete(
        "/api/v1/files/object",
        params={"bucket": _BUCKET, "key": uploaded_key},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["success"] is True

    # 6) 递归删目录(至少删掉 sub/ 标记对象)
    resp = await client.post(
        "/api/v1/files/delete-folder",
        json={"bucket": _BUCKET, "prefix": test_prefix},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["deleted"] >= 1


async def test_soft_guard_blocks_referenced_object(
    client: AsyncClient,
    _login_admin: None,
    session_factory,
    test_prefix: str,
) -> None:
    """上传对象 → 插入引用该 key 的 hosted 版本 → 删对象 409;清理后可删。"""
    from app.models.dataset_version import DatasetVersion

    # 上传
    resp = await client.post(
        "/api/v1/files/upload",
        data={"bucket": _BUCKET, "prefix": test_prefix},
        files={"file": ("guard.jsonl", b'{"text": "x"}\n', "application/x-ndjson")},
    )
    assert resp.status_code == 200, resp.text
    key = resp.json()["data"]["key"]

    # 插入引用该 key 的 hosted 版本
    storage_uri = f"s3://{_BUCKET}/{key}"
    async with session_factory() as session:
        session.add(
            DatasetVersion(
                id=f"dsv-{uuid.uuid4().hex[:6]}",
                dataset_id="ds-guard",
                version_no=1,
                storage_uri=storage_uri,
                format="jsonl",
                origin="hosted",
                source_datasource_id="ds-x",
            )
        )
        await session.commit()

    # 删对象 → 409
    resp = await client.delete(
        "/api/v1/files/object", params={"bucket": _BUCKET, "key": key}
    )
    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["success"] is False
    assert "托管数据集引用" in body["message"]

    # 清理引用行后,删除应放行(同时清掉对象)
    async with session_factory() as session:
        from sqlalchemy import delete

        await session.execute(
            delete(DatasetVersion).where(DatasetVersion.storage_uri == storage_uri)
        )
        await session.commit()
    resp = await client.delete(
        "/api/v1/files/object", params={"bucket": _BUCKET, "key": key}
    )
    assert resp.status_code == 200, resp.text


async def test_rbac_non_admin_and_anonymous(
    client: AsyncClient, seed_users: None
) -> None:
    """非 admin 写 → 403;匿名写 → 401;非 admin 读 → 200。"""
    from app.services.auth import sign_token

    # 非 admin 上传 → 403
    client.cookies.set("adp_session", sign_token("user"))
    resp = await client.post(
        "/api/v1/files/upload",
        data={"bucket": _BUCKET, "prefix": "adp-test-files/rbac/"},
        files={"file": ("x.txt", b"x", "text/plain")},
    )
    assert resp.status_code == 403, resp.text

    # 非 admin 删对象 → 403
    resp = await client.delete(
        "/api/v1/files/object",
        params={"bucket": _BUCKET, "key": "adp-test-files/rbac/x.txt"},
    )
    assert resp.status_code == 403, resp.text

    # 非 admin 读目录 → 200
    resp = await client.get(
        "/api/v1/files", params={"bucket": _BUCKET, "prefix": "adp-test-files/rbac/"}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["success"] is True

    # 匿名上传 → 401
    client.cookies.clear()
    resp = await client.post(
        "/api/v1/files/upload",
        data={"bucket": _BUCKET, "prefix": "adp-test-files/rbac/"},
        files={"file": ("x.txt", b"x", "text/plain")},
    )
    assert resp.status_code == 401, resp.text
