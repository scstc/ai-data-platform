"""鉴权依赖:从 cookie 解析当前用户、管理员门控。

- current_user:解析 adp_session 令牌→查 User→停用视为未登录(None)。
- require_admin:无登录抛 401;非 admin 抛 403 {success:false,message:"无权限"}。

真正的越权防护在后端这一层(前端 access 门控只是 UX);写端点用 require_admin。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import Cookie, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.models.user import User
from app.services.auth import parse_token

SessionDep = Annotated[AsyncSession, Depends(get_session)]

# cookie 名沿用兼容层约定
_COOKIE_NAME = "adp_session"


async def current_user(
    session: SessionDep,
    adp_session: Annotated[str | None, Cookie()] = None,
) -> User | None:
    """解析 cookie 令牌→按 username 查 User;无 cookie/令牌无效/用户停用均返回 None。"""
    if not adp_session:
        return None
    username = parse_token(adp_session)
    if username is None:
        return None
    user = (
        await session.scalars(select(User).where(User.username == username))
    ).first()
    if user is None or user.disabled:
        return None
    return user


async def require_user(
    user: Annotated[User | None, Depends(current_user)],
) -> User:
    """登录门控:任何登录用户放行;未登录→401(供仅需登录、不限角色的读端点)。"""
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    return user


async def require_admin(
    user: Annotated[User | None, Depends(current_user)],
) -> User:
    """管理员门控:未登录→401;角色非 admin→403 {success:false,message:"无权限"}。"""
    if user is None:
        raise HTTPException(status_code=401, detail="请先登录")
    if user.role != "admin":
        raise HTTPException(
            status_code=403,
            detail={"success": False, "message": "无权限"},
        )
    return user


def require_perm(code: str) -> Callable:
    """权限门控工厂:要求当前用户持 code(或通配)。未登录 401 / 无权 403。

    与 require_admin 并存:管理类细粒度写端点(P2+)用本依赖;
    遗留写端点继续用 require_admin(靠 users.role 保持不变)。
    """

    async def _dep(
        session: SessionDep,
        user: Annotated[User | None, Depends(current_user)],
    ) -> User:
        # 惰性引入,避免与 services 包的潜在导入环
        from app.services import rbac

        if user is None:
            raise HTTPException(status_code=401, detail="请先登录")
        perms = await rbac.get_user_perms(session, user)
        if not rbac.has_perm(perms, code):
            raise HTTPException(
                status_code=403,
                detail={"success": False, "message": "无权限"},
            )
        return user

    return _dep
