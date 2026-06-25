"""版本级发布门测试(#4,设计见 docs/plan/11)。

覆盖发布门的端点行为与不变量:
- 发布门:scan_verdict 必须 passed 才能 publish,否则 409(unscanned/failed)。
- 状态机:publish 幂等(不改写首次 published_at);unpublish 仅对 published 生效;
  发布/下架后 publish_status / published_at 正确。
- 人工覆盖:接受风险(→passed)后可发布;驳回(→failed)一个已发布版本会同时下架它
  (维持不变量 published ⟹ passed);非法 verdict → 400。
- RBAC:三端点 require_admin——非 admin → 403、匿名 → 401(真后端门控,非前端隐藏)。

测试意图(为何重要):
- 发布门是把 #4「通过性指标」落成的安全闸门:未过扫描的数据绝不能流出成训练集,
  这些用例正是该不变量的回归防线。
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.dataset import Dataset
from app.models.dataset_version import DatasetVersion

pytestmark = pytest.mark.asyncio

DATASET_ID = "dset-pg1"


@pytest_asyncio.fixture(autouse=True)
async def _admin_session(client: AsyncClient, seed_users: None) -> None:
    """发布门写端点均 require_admin:默认以 admin 身份请求。"""
    from app.services.auth import sign_token

    client.cookies.set("adp_session", sign_token("admin"))


async def _seed(
    session_factory: async_sessionmaker,
    *,
    version_id: str,
    scan_verdict: str = "unscanned",
    publish_status: str = "draft",
    version_no: int = 1,
) -> None:
    """落库一个数据集 + 指定状态的版本。"""
    async with session_factory() as session:
        if (await session.get(Dataset, DATASET_ID)) is None:
            session.add(Dataset(id=DATASET_ID, name="发布门测试集"))
        session.add(
            DatasetVersion(
                id=version_id,
                dataset_id=DATASET_ID,
                version_no=version_no,
                storage_uri=f"/tmp/{version_id}.jsonl",
                format="jsonl",
                rows=1,
                scan_verdict=scan_verdict,
                publish_status=publish_status,
            )
        )
        await session.commit()


# --- 发布门:只有 passed 能发布 -------------------------------------------
async def test_publish_blocked_when_unscanned(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    await _seed(session_factory, version_id="dsv-uns")
    resp = await client.post("/api/v1/dataset-versions/dsv-uns/publish")
    assert resp.status_code == 409
    assert resp.json()["success"] is False
    assert "安全扫描" in resp.json()["message"]


async def test_publish_blocked_when_failed(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    await _seed(session_factory, version_id="dsv-fail", scan_verdict="failed")
    resp = await client.post("/api/v1/dataset-versions/dsv-fail/publish")
    assert resp.status_code == 409


async def test_publish_succeeds_when_passed_and_is_idempotent(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    await _seed(session_factory, version_id="dsv-pass", scan_verdict="passed")
    resp = await client.post("/api/v1/dataset-versions/dsv-pass/publish")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["publishStatus"] == "published"
    assert data["publishedAt"] is not None
    first_published_at = data["publishedAt"]

    # 幂等:再次发布不改写首次 published_at
    resp2 = await client.post("/api/v1/dataset-versions/dsv-pass/publish")
    assert resp2.status_code == 200
    assert resp2.json()["data"]["publishedAt"] == first_published_at


async def test_publish_version_not_found(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/dataset-versions/dsv-none/publish")
    assert resp.status_code == 404


# --- 列表展示:已发布版本号优先于更新的草稿版本号 ------------------------
async def test_list_latest_label_prefers_published_over_newer_draft(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    """列表 latestVersionLabel 必须展示「已发布版本」号,而非更新的草稿版本号。

    场景:同一数据集有 published v1,之后又产出更新的 draft v2/v3。publish 不变量
    保证同数据集至多一个 published 版本(算法侧消费的唯一当前发布版),故列表应显示
    已发布的 #1;若取 version_no 最大者会显示草稿 #3,与详情页「实际发布版本」对不上。
    """
    await _seed(
        session_factory,
        version_id="dsv-lv-pub",
        scan_verdict="passed",
        publish_status="published",
        version_no=1,
    )
    await _seed(session_factory, version_id="dsv-lv-d2", version_no=2)
    await _seed(session_factory, version_id="dsv-lv-d3", version_no=3)

    resp = await client.get("/api/v1/datasets")
    assert resp.status_code == 200, resp.text
    item = next(
        (d for d in resp.json()["data"] if d["id"] == DATASET_ID), None
    )
    assert item is not None, "种子数据集应出现在列表中"
    label = item["latestVersionLabel"]
    assert label is not None
    # 展示已发布版本 #1,而非最新草稿 #3
    assert label.endswith("(#1)"), label
    assert "(#3)" not in label, label


# --- 下架:仅对 published 生效 --------------------------------------------
async def test_unpublish_published(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    await _seed(
        session_factory,
        version_id="dsv-pub",
        scan_verdict="passed",
        publish_status="published",
    )
    resp = await client.post("/api/v1/dataset-versions/dsv-pub/unpublish")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["publishStatus"] == "unpublished"
    assert data["publishedAt"] is None


async def test_unpublish_rejects_draft(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    await _seed(session_factory, version_id="dsv-draft", scan_verdict="passed")
    resp = await client.post("/api/v1/dataset-versions/dsv-draft/unpublish")
    assert resp.status_code == 409
    assert "已发布" in resp.json()["message"]


# --- 人工覆盖 verdict ------------------------------------------------------
async def test_manual_accept_risk_then_publish(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    """接受风险:failed → 人工 passed(manual)→ 可发布。"""
    await _seed(session_factory, version_id="dsv-ar", scan_verdict="failed")
    resp = await client.post(
        "/api/v1/dataset-versions/dsv-ar/verdict",
        json={"verdict": "passed", "note": "已人工核验,误报"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["scanVerdict"] == "passed"
    assert data["verdictSource"] == "manual"
    assert data["verdictNote"] == "已人工核验,误报"
    # 覆盖后即可发布
    resp = await client.post("/api/v1/dataset-versions/dsv-ar/publish")
    assert resp.status_code == 200


async def test_reject_published_version_unpublishes_it(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    """驳回一个已发布版本 → 同时下架(维持 published ⟹ passed 不变量)。"""
    await _seed(
        session_factory,
        version_id="dsv-rev",
        scan_verdict="passed",
        publish_status="published",
    )
    resp = await client.post(
        "/api/v1/dataset-versions/dsv-rev/verdict",
        json={"verdict": "failed", "note": "复核发现问题"},
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["scanVerdict"] == "failed"
    assert data["publishStatus"] == "unpublished"
    assert data["publishedAt"] is None


async def test_verdict_rejects_invalid_value(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    await _seed(session_factory, version_id="dsv-badv", scan_verdict="passed")
    resp = await client.post(
        "/api/v1/dataset-versions/dsv-badv/verdict",
        json={"verdict": "maybe"},
    )
    assert resp.status_code == 400


# --- RBAC:三端点真后端门控 ------------------------------------------------
async def test_publish_gate_requires_admin(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    """非 admin → 403;匿名 → 401。后端是真闸门,不靠前端 access 隐藏。"""
    from app.services.auth import sign_token

    await _seed(session_factory, version_id="dsv-rbac", scan_verdict="passed")

    client.cookies.set("adp_session", sign_token("user"))
    resp = await client.post("/api/v1/dataset-versions/dsv-rbac/publish")
    assert resp.status_code == 403

    client.cookies.delete("adp_session")
    resp = await client.post("/api/v1/dataset-versions/dsv-rbac/publish")
    assert resp.status_code == 401
