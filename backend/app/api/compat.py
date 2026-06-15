"""模板登录兼容层:真实鉴权实现(DB 用户 + 签名令牌)。

前端 MOCK=none 后,登录/当前用户/登出/验证码/通知这套接口由后端真实提供。
本模块响应形状逐字镜像模板 mock(权威参考:frontend/mock/user.ts、
frontend/src/services/ant-design-pro/api.ts),仅把用户信息本地化为本平台文案。

接口挂在 /api 前缀下(不是 /api/v1),与模板前端请求路径一致。

会话方案(#5 访问安全):
登录校验 DB 用户口令(PBKDF2),成功签发 HMAC-SHA256 令牌写入 httpOnly cookie
``adp_session``(samesite=lax,7 天)。currentUser 验签 cookie→查 User→返回
``access``=role 供前端 access.ts 门控。无效 cookie 返回模板 401 形状。
密钥/算法见 app/services/auth.py。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Cookie, Depends, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.models.user import User
from app.services.auth import parse_token, sign_token, verify_password

router = APIRouter(tags=["compat-auth"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]

# cookie 名沿用历史约定;7 天有效期(秒)。
_COOKIE_NAME = "adp_session"
_COOKIE_MAX_AGE = 7 * 24 * 3600

# 模板 defaultUser 的头像 URL 原样保留;其余字段本地化为本平台文案。
_AVATAR_URL = "https://gw.alipayobjects.com/zos/antfincdn/XAosXuNZyF/BiazfanxmamNRoxxVxka.png"


class LoginParams(BaseModel):
    """登录请求体,镜像模板 mock 的 {username, password, type}。"""

    username: str | None = None
    password: str | None = None
    type: str | None = None


def _current_user_payload(user: User) -> dict:
    """构造 currentUser 的 data 体。

    沿用模板 defaultUser 形状(avatar/email/signature/title/group/geographic 等),
    name/access/userid 取自真实 User;geographic 形状照搬模板。
    """
    return {
        "name": user.display_name or user.username,
        "avatar": _AVATAR_URL,
        "userid": user.id,
        "email": "admin@adp.local",
        "signature": "面向大模型的数据工程与数据集管理平台",
        "title": "平台管理员",
        "group": "AI 数据平台",
        "tags": [],
        "notifyCount": 0,
        "unreadCount": 0,
        "country": "China",
        "geographic": {
            "province": {"label": "浙江省", "key": "330000"},
            "city": {"label": "杭州市", "key": "330100"},
        },
        "address": "",
        "phone": "",
        "access": user.role,
    }


async def _get_user(session: AsyncSession, username: str) -> User | None:
    """按 username 查用户(停用与否由调用方判定)。"""
    return (
        await session.scalars(select(User).where(User.username == username))
    ).first()


@router.post("/login/account")
async def login_account(
    body: LoginParams, session: SessionDep
) -> JSONResponse:
    """登录:查 user→verify_password→成功签发令牌 cookie,失败 guest 不设 cookie。

    成功 {"status":"ok","type":<回显>,"currentAuthority":<角色>} 并设 cookie;
    失败 {"status":"error","type":<回显>,"currentAuthority":"guest"} 且不设 cookie。
    """
    user = (
        await _get_user(session, body.username) if body.username else None
    )
    if (
        user is not None
        and not user.disabled
        and body.password is not None
        and verify_password(body.password, user.password_hash)
    ):
        response = JSONResponse(
            {
                "status": "ok",
                "type": body.type,
                "currentAuthority": user.role,
            }
        )
        # 签名令牌入 httpOnly cookie;samesite=lax 兼容开发期前端同源请求。
        response.set_cookie(
            _COOKIE_NAME,
            sign_token(user.username),
            httponly=True,
            samesite="lax",
            max_age=_COOKIE_MAX_AGE,
        )
        return response

    return JSONResponse(
        {"status": "error", "type": body.type, "currentAuthority": "guest"}
    )


@router.get("/currentUser")
async def current_user(
    session: SessionDep,
    adp_session: Annotated[str | None, Cookie()] = None,
) -> JSONResponse:
    """当前用户:验签 cookie→查 user→200 本地化用户体;无效 cookie → 401 模板形状。"""
    username = parse_token(adp_session) if adp_session else None
    user = await _get_user(session, username) if username else None
    if user is None or user.disabled:
        return JSONResponse(
            status_code=401,
            content={
                "data": {"isLogin": False},
                "errorCode": "401",
                "errorMessage": "请先登录！",
                "success": True,
            },
        )
    return JSONResponse(
        {"success": True, "data": _current_user_payload(user)}
    )


@router.post("/login/outLogin")
async def out_login(response: Response) -> dict:
    """登出:清除会话 cookie,返回模板形状 {"data":{},"success":true}。"""
    response.delete_cookie(_COOKIE_NAME)
    return {"data": {}, "success": True}


@router.get("/login/captcha")
async def login_captcha() -> str:
    """验证码:模板 mock 返回 JSON 字符串 "captcha-xxx"。不模拟 2 秒延迟。"""
    return "captcha-xxx"


@router.get("/notices")
async def notices() -> dict:
    """通知列表:模板形状 {"data":[],"success":true},本平台暂无通知。"""
    return {"data": [], "success": True}
