"""数据集到期提醒端点 GET /datasets/expiring(登录后弹窗数据源)。

测试意图(为何重要):
- 只提醒"我负责的"(owner 或 creator 命中当前用户),别人的数据集不泄露到我的弹窗;
- 命中口径含已过期(expired=true)且按 valid_until 升序(最紧急在前);
- valid_until 为空、或超出 days 阈值的数据集不进提醒,避免噪声。
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.models.dataset import Dataset

pytestmark = pytest.mark.asyncio


async def _add(session_factory, **kw) -> None:
    async with session_factory() as s:
        s.add(Dataset(**kw))
        await s.commit()


async def test_expiring_scopes_to_mine_and_orders(
    client, session_factory, seed_users
) -> None:
    """admin 只看到自己 owner/creator 且 14 天内(含已过期)的数据集,按到期升序。"""
    from app.services.auth import sign_token

    now = datetime.now(UTC).replace(tzinfo=None)
    rows = [
        # (id, owner, creator, valid_until, 是否应命中)
        ("dset-exp3", "admin", "admin", now + timedelta(days=3), True),
        ("dset-exp10", "admin", "admin", now + timedelta(days=10), True),
        ("dset-expired", "admin", "admin", now - timedelta(days=2), True),
        ("dset-far", "admin", "admin", now + timedelta(days=40), False),
        ("dset-null", "admin", "admin", None, False),
        ("dset-other", "user", "user", now + timedelta(days=1), False),
        # owner 是别人但 creator 是我 → 命中(我负责)
        ("dset-mine-by-creator", "user", "admin", now + timedelta(days=2), True),
    ]
    for did, owner, creator, vu, _hit in rows:
        await _add(
            session_factory,
            id=did,
            name=did,
            owner=owner,
            creator=creator,
            valid_until=vu,
        )

    client.cookies.set("adp_session", sign_token("admin"))
    resp = await client.get("/api/v1/datasets/expiring", params={"days": 14})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    data = body["data"]

    ids = [d["id"] for d in data]
    # 命中集合 = 我负责且在阈值内(含过期);升序:过期(-2)→ +2 → +3 → +10
    assert ids == [
        "dset-expired",
        "dset-mine-by-creator",
        "dset-exp3",
        "dset-exp10",
    ]
    by_id = {d["id"]: d for d in data}
    assert by_id["dset-expired"]["expired"] is True
    assert by_id["dset-expired"]["daysLeft"] < 0
    assert by_id["dset-exp3"]["expired"] is False
    assert by_id["dset-exp3"]["daysLeft"] == 3
    # validUntil 序列化为带 Z 的 UTC
    assert by_id["dset-exp3"]["validUntil"].endswith("Z")


async def test_expiring_requires_login(client) -> None:
    """未登录访问到期提醒端点 → 401(require_user 门控)。"""
    client.cookies.delete("adp_session")
    resp = await client.get("/api/v1/datasets/expiring")
    assert resp.status_code == 401
