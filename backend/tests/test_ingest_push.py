"""API 推送入站集成测(httpx,无外部依赖)—— 数据接入重构 §4.7 / §7 / §9。

锁的是「外部服务 API 推送」(接入④)的语义自洽,而不仅仅是端点能返回 200:

1. **归并语义**:同一 api 数据源连续推送 = 追加到**同一逻辑数据集**,每次产
   **新版本**(而非每次新建 dataset)。这是把「真做」补成「真做且语义自洽」
   的核心(§4.7),防高频推送爆量产单版本数据集。
2. **token 即凭证**:坏 token → 401(且不泄漏数据源是否存在)。
3. **限流**:同 token 超过窗口上限 → 429。
4. **token 轮换**:轮换后旧 token 立即失效(401),新 token 可用。

api 数据源经真实 POST /api/v1/datasources 创建(require_admin),后端生成
pushToken 并把真实入站 url 回填 config —— 测试从 url 反解 token,贴近真实链路。
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.dataset import Dataset
from app.models.dataset_version import DatasetVersion

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def _admin_session(client: AsyncClient, seed_users: None) -> None:
    """数据源写端点 + rotate-token 都加了 require_admin:统一以 admin 身份请求。"""
    from app.services.auth import sign_token

    client.cookies.set("adp_session", sign_token("admin"))


@pytest.fixture(autouse=True)
def _datasets_dir(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """推送落地写 jsonl 到 settings.datasets_dir:指到 tmp,勿污染真实数据目录。"""
    from app.core.config import settings

    monkeypatch.setattr(settings, "datasets_dir", str(tmp_path))


@pytest_asyncio.fixture(autouse=True)
async def _reset_push_state() -> None:
    """每用例清空 push 端点的内存限流/幂等状态,避免跨用例串味。

    限流 / 幂等键都是模块级 dict;不清会让上一个用例的计数/键漏进下一个,
    导致 429/幂等断言飘。
    """
    from app.api.v1 import ingest_push
    from app.services.connectors import push as push_svc

    ingest_push._rate_state.clear()
    push_svc._idempotency_cache.clear()
    yield
    ingest_push._rate_state.clear()
    push_svc._idempotency_cache.clear()


async def _create_api_datasource(
    client: AsyncClient, *, semantic_type: str | None = None
) -> tuple[str, str]:
    """建一个 api 推送数据源,返回 (datasource_id, push_token)。

    token 由后端生成并回填进 config.url(…/ingest/push/<token>),从 url 反解。
    """
    config: dict = {}
    if semantic_type is not None:
        config["semanticType"] = semantic_type
    resp = await client.post(
        "/api/v1/datasources",
        json={"name": "外部推送源", "type": "api", "config": config},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    url = data["config"]["url"]
    token = url.rstrip("/").rsplit("/", 1)[-1]
    assert token  # 后端确实生成了 token
    return data["id"], token


async def test_two_pushes_merge_into_one_dataset_two_versions(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    """连推两次 → 同一 dataset 两个版本(version_no 1、2),不新建第二个 dataset。"""
    _ds_id, token = await _create_api_datasource(client)

    # 第一次推送
    r1 = await client.post(
        f"/api/v1/ingest/push/{token}",
        json={"records": [{"text": "hello"}, {"text": "world"}]},
    )
    assert r1.status_code == 200, r1.text
    d1 = r1.json()["data"]
    assert d1["versionNo"] == 1
    assert d1["rows"] == 2
    dataset_id = d1["datasetId"]

    # 第二次推送:同 token → 同 dataset、版本 +1
    r2 = await client.post(
        f"/api/v1/ingest/push/{token}",
        json={"records": [{"text": "again"}]},
    )
    assert r2.status_code == 200, r2.text
    d2 = r2.json()["data"]
    assert d2["datasetId"] == dataset_id  # 归并:同一逻辑数据集
    assert d2["versionNo"] == 2  # 自增版本
    assert d2["rows"] == 1
    assert d2["versionId"] != d1["versionId"]

    # DB 视角:仅一个 dataset,两条 version
    async with session_factory() as session:
        datasets = (await session.scalars(select(Dataset))).all()
        assert len(datasets) == 1
        assert datasets[0].id == dataset_id
        versions = (
            await session.scalars(
                select(DatasetVersion).where(
                    DatasetVersion.dataset_id == dataset_id
                )
            )
        ).all()
        assert {v.version_no for v in versions} == {1, 2}


async def test_push_applies_semantic_type_to_version_snapshot(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    """body 指定 semanticType → 归一别名并写版本级 semantic_type 快照(§4.7/§5.2)。"""
    _ds_id, token = await _create_api_datasource(client)

    resp = await client.post(
        f"/api/v1/ingest/push/{token}",
        json={
            "records": [{"q": "1+1?", "a": "2"}],  # qa 别名
            "semanticType": "qa",
        },
    )
    assert resp.status_code == 200, resp.text
    version_id = resp.json()["data"]["versionId"]

    async with session_factory() as session:
        version = await session.get(DatasetVersion, version_id)
        assert version is not None
        assert version.semantic_type == "qa"  # 版本级语义快照


async def test_bad_token_returns_401(client: AsyncClient) -> None:
    """坏 token → 401 + {success:false}(不泄漏数据源是否存在)。"""
    resp = await client.post(
        "/api/v1/ingest/push/totally-wrong-token",
        json={"records": [{"text": "x"}]},
    )
    assert resp.status_code == 401
    assert resp.json()["success"] is False


async def test_empty_records_returns_400(client: AsyncClient) -> None:
    """有效 token 但无可落地记录 → 400(诚实拒绝,不产空版本)。"""
    _ds_id, token = await _create_api_datasource(client)
    resp = await client.post(
        f"/api/v1/ingest/push/{token}",
        json={"records": []},
    )
    assert resp.status_code == 400
    assert resp.json()["success"] is False


async def test_rate_limit_returns_429_when_exceeded(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """同 token 超过窗口上限 → 429。把上限调小以免真发 60 次。"""
    from app.api.v1 import ingest_push

    # 把窗口上限压到 2,避免测试发太多请求;窗口时间不变(同一窗口内累计)
    monkeypatch.setattr(ingest_push, "_RATE_LIMIT", 2)

    _ds_id, token = await _create_api_datasource(client)

    # 前 2 次放行
    for _ in range(2):
        ok = await client.post(
            f"/api/v1/ingest/push/{token}",
            json={"records": [{"text": "x"}]},
        )
        assert ok.status_code == 200, ok.text

    # 第 3 次超限 → 429(限流先于 DB 查询,不落地)
    over = await client.post(
        f"/api/v1/ingest/push/{token}",
        json={"records": [{"text": "x"}]},
    )
    assert over.status_code == 429
    assert over.json()["success"] is False


async def test_rotate_token_invalidates_old_and_issues_new(
    client: AsyncClient,
) -> None:
    """轮换 token:旧 token 立即失效(401),新 token 可用(200)。"""
    ds_id, old_token = await _create_api_datasource(client)

    # 旧 token 先确认可用
    pre = await client.post(
        f"/api/v1/ingest/push/{old_token}",
        json={"records": [{"text": "before"}]},
    )
    assert pre.status_code == 200, pre.text

    # 轮换
    rot = await client.post(f"/api/v1/datasources/{ds_id}/rotate-push-token")
    assert rot.status_code == 200, rot.text
    rot_data = rot.json()["data"]
    new_token = rot_data["pushToken"]
    assert new_token != old_token
    assert new_token in rot_data["url"]

    # 旧 token 立即失效 → 401
    old = await client.post(
        f"/api/v1/ingest/push/{old_token}",
        json={"records": [{"text": "stale"}]},
    )
    assert old.status_code == 401
    assert old.json()["success"] is False

    # 新 token 可用 → 200
    new = await client.post(
        f"/api/v1/ingest/push/{new_token}",
        json={"records": [{"text": "fresh"}]},
    )
    assert new.status_code == 200, new.text
    assert new.json()["data"]["rows"] == 1


async def test_idempotency_key_dedupes_within_ttl(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    """同 idempotencyKey 在 TTL 内重复推送 → 返回首版结果,不重复落地。"""
    _ds_id, token = await _create_api_datasource(client)

    body = {"records": [{"text": "once"}], "idempotencyKey": "dedupe-key-1"}
    r1 = await client.post(f"/api/v1/ingest/push/{token}", json=body)
    assert r1.status_code == 200, r1.text
    v1 = r1.json()["data"]["versionId"]

    # 同 key 重复 → 返回同一 versionId,不产新版本
    r2 = await client.post(f"/api/v1/ingest/push/{token}", json=body)
    assert r2.status_code == 200, r2.text
    assert r2.json()["data"]["versionId"] == v1

    async with session_factory() as session:
        versions = (await session.scalars(select(DatasetVersion))).all()
        assert len(versions) == 1  # 幂等:只落了一版
