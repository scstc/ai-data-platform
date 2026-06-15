"""模板登录兼容层测试。

覆盖（形状逐字镜像模板 mock）：
- 登录成功两角色（admin/user）：status/type 回显/currentAuthority/cookie；
- 登录失败 guest：不设 cookie；
- currentUser 无 cookie → 401 形状逐字段；带 cookie → 200 含 access；
- outLogin 后再查 currentUser → 401；
- captcha 返回 JSON 字符串、notices 返回 {data:[],success:true}。

挂载前缀为 /api（不是 /api/v1）。
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio

_COOKIE_NAME = "adp_session"


async def test_login_admin_success_sets_cookie(
    client: AsyncClient, seed_users: None
) -> None:
    """admin/ant.design → status ok、type 回显、currentAuthority=admin、下发 cookie。"""
    resp = await client.post(
        "/api/login/account",
        json={"username": "admin", "password": "ant.design", "type": "account"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {
        "status": "ok",
        "type": "account",
        "currentAuthority": "admin",
    }
    # 成功登录下发签名令牌 cookie（不再是明文角色值）。
    assert resp.cookies.get(_COOKIE_NAME) is not None


async def test_login_user_success_sets_cookie(
    client: AsyncClient, seed_users: None
) -> None:
    """user/ant.design → currentAuthority=user、下发签名令牌 cookie。"""
    resp = await client.post(
        "/api/login/account",
        json={"username": "user", "password": "ant.design", "type": "account"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {
        "status": "ok",
        "type": "account",
        "currentAuthority": "user",
    }
    assert resp.cookies.get(_COOKIE_NAME) is not None


async def test_login_failure_guest_no_cookie(
    client: AsyncClient, seed_users: None
) -> None:
    """错误凭据 → status error、currentAuthority=guest、不设 cookie。"""
    resp = await client.post(
        "/api/login/account",
        json={"username": "admin", "password": "wrong", "type": "account"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body == {
        "status": "error",
        "type": "account",
        "currentAuthority": "guest",
    }
    assert resp.cookies.get(_COOKIE_NAME) is None


async def test_current_user_without_cookie_401(client: AsyncClient) -> None:
    """无 cookie → 401，且响应体逐字段镜像模板 mock。"""
    resp = await client.get("/api/currentUser")
    assert resp.status_code == 401, resp.text
    body = resp.json()
    assert body == {
        "data": {"isLogin": False},
        "errorCode": "401",
        "errorMessage": "请先登录！",
        "success": True,
    }


async def test_current_user_with_admin_cookie(
    client: AsyncClient, seed_users: None
) -> None:
    """登录 admin 后查 currentUser → 200，本地化字段 + access=admin。"""
    await client.post(
        "/api/login/account",
        json={"username": "admin", "password": "ant.design", "type": "account"},
    )
    resp = await client.get("/api/currentUser")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    data = body["data"]
    assert data["access"] == "admin"
    assert data["name"] == "管理员"
    # userid 取真实 User 主键(种子 fixture 的 id)。
    assert data["userid"] == "usr-test01"
    assert data["email"] == "admin@adp.local"
    assert data["signature"] == "面向大模型的数据工程与数据集管理平台"
    assert data["title"] == "平台管理员"
    assert data["group"] == "AI 数据平台"
    assert data["tags"] == []
    assert data["notifyCount"] == 0
    assert data["unreadCount"] == 0
    assert data["country"] == "China"
    # geographic 形状照搬模板（province/city 的 label/key）。
    assert data["geographic"] == {
        "province": {"label": "浙江省", "key": "330000"},
        "city": {"label": "杭州市", "key": "330100"},
    }
    assert data["address"] == ""
    assert data["phone"] == ""
    # 头像保留模板 URL。
    assert data["avatar"].startswith("https://gw.alipayobjects.com/")


async def test_current_user_with_user_cookie_name(
    client: AsyncClient, seed_users: None
) -> None:
    """user 角色 → name 本地化为“普通用户”、access=user。"""
    await client.post(
        "/api/login/account",
        json={"username": "user", "password": "ant.design", "type": "account"},
    )
    resp = await client.get("/api/currentUser")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["name"] == "普通用户"
    assert data["access"] == "user"


async def test_out_login_then_current_user_401(
    client: AsyncClient, seed_users: None
) -> None:
    """登录后登出，cookie 被清，再查 currentUser → 401。"""
    await client.post(
        "/api/login/account",
        json={"username": "admin", "password": "ant.design", "type": "account"},
    )
    out = await client.post("/api/login/outLogin")
    assert out.status_code == 200, out.text
    assert out.json() == {"data": {}, "success": True}

    resp = await client.get("/api/currentUser")
    assert resp.status_code == 401
    assert resp.json()["data"] == {"isLogin": False}


async def test_captcha_shape(client: AsyncClient) -> None:
    """验证码返回 JSON 字符串 "captcha-xxx"。"""
    resp = await client.get("/api/login/captcha")
    assert resp.status_code == 200, resp.text
    assert resp.json() == "captcha-xxx"


async def test_notices_shape(client: AsyncClient) -> None:
    """通知列表返回 {data:[],success:true}。"""
    resp = await client.get("/api/notices")
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"data": [], "success": True}
