"""RBAC 越权防护测试(DB 后端):登录链路 + 写端点门控。

测试意图(为何重要):
- 真实凭据登录后 currentUser 必须回 access=admin(前端 access.ts 据此放行管理功能);
- user 角色调管理类写端点必须被后端 403 拦截——这是真正的越权防护(不靠前端隐藏);
- admin 调同端点不能被 403 拦(权限矩阵不能把管理员一起锁死)。

种子用户经 seed_users fixture 用 hash_password 插入(测试库 create_all 初始化,
迁移种子在测试库不存在)。
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_login_chain_admin_access(
    client: AsyncClient, seed_users: None
) -> None:
    """admin 登录 200 且下发 cookie;带 cookie 查 currentUser → access=admin。"""
    resp = await client.post(
        "/api/login/account",
        json={"username": "admin", "password": "ant.design", "type": "account"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "ok"
    assert resp.cookies.get("adp_session") is not None

    me = await client.get("/api/currentUser")
    assert me.status_code == 200, me.text
    assert me.json()["data"]["access"] == "admin"


async def test_login_chain_bad_credentials(
    client: AsyncClient, seed_users: None
) -> None:
    """错误口令 → status:error、不下发 cookie。"""
    resp = await client.post(
        "/api/login/account",
        json={"username": "admin", "password": "nope", "type": "account"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "error"
    assert resp.cookies.get("adp_session") is None


async def test_user_role_cannot_delete_dataset(
    client: AsyncClient, seed_users: None
) -> None:
    """user 角色 cookie 调 DELETE /datasets/{任意id} → 403(后端越权拦截)。"""
    from app.services.auth import sign_token

    client.cookies.set("adp_session", sign_token("user"))
    resp = await client.delete("/api/v1/datasets/dset-anything")
    assert resp.status_code == 403, resp.text
    body = resp.json()
    # require_admin 的 403 形状:detail = {success:false, message:"无权限"}
    assert body["detail"]["success"] is False
    assert body["detail"]["message"] == "无权限"


async def test_admin_role_not_blocked_on_delete_dataset(
    client: AsyncClient, seed_users: None
) -> None:
    """admin 调同一端点不被 403 拦截(不存在的 id 返回 404,业务层处理)。"""
    from app.services.auth import sign_token

    client.cookies.set("adp_session", sign_token("admin"))
    resp = await client.delete("/api/v1/datasets/dset-anything")
    assert resp.status_code != 403, resp.text
    # 门控放行后进入业务逻辑:目标不存在 → 404
    assert resp.status_code == 404


async def test_anonymous_cannot_delete_dataset(client: AsyncClient) -> None:
    """DELETE /datasets/{id} 按设计对匿名放行(业务层 owner/admin 校验),
    但未登录无 owner → 应被业务层拒绝(403/404)。这里断言至少不是 200。
    真正 401 门控看其他写端点(如 POST /datasets/{id}/members)。
    """
    resp = await client.delete("/api/v1/datasets/dset-anything")
    # 不存在 → 404;若路由改造收紧鉴权,可能直接 401
    assert resp.status_code in (401, 403, 404), resp.text
