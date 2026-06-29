"""个人中心(自助):任何登录用户读/改自己的资料与口令。

与 system/users.py(管理员对任意用户的 CRUD,需 system:user:* 权限)区分:
本路由只作用于"当前登录用户自己",仅需登录(require_user)。可改字段受
User 模型约束——目前仅 display_name(昵称)与 password(需校验旧口令)。
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.api.deps import SessionDep, require_user
from app.models.user import User
from app.services.auth import hash_password, verify_password

router = APIRouter(prefix="/profile", tags=["profile"])


class ProfileUpdate(BaseModel):
    """更新当前用户资料:目前仅昵称(display_name)。"""

    displayName: str = Field(min_length=1, max_length=64)


class ChangePwd(BaseModel):
    """修改当前用户口令:需校验旧口令。"""

    oldPassword: str = Field(min_length=1)
    newPassword: str = Field(min_length=6, max_length=128)


def _profile_payload(user: User) -> dict:
    """当前用户自助视图:身份与可改字段(不含口令哈希)。"""
    return {
        "id": user.id,
        "username": user.username,
        "displayName": user.display_name or user.username,
        "role": user.role,
    }


@router.get("")
async def get_profile(
    user: Annotated[User, Depends(require_user)],
) -> JSONResponse:
    """读当前登录用户的资料。"""
    return JSONResponse({"success": True, "data": _profile_payload(user)})


@router.put("")
async def update_profile(
    body: ProfileUpdate,
    session: SessionDep,
    user: Annotated[User, Depends(require_user)],
) -> JSONResponse:
    """改当前用户昵称(display_name)。"""
    user.display_name = body.displayName.strip()
    await session.commit()
    await session.refresh(user)
    return JSONResponse({"success": True, "data": _profile_payload(user)})


@router.put("/password")
async def change_password(
    body: ChangePwd,
    session: SessionDep,
    user: Annotated[User, Depends(require_user)],
) -> JSONResponse:
    """改当前用户口令:先校验旧口令,失败返回 400(不改库)。"""
    if not verify_password(body.oldPassword, user.password_hash):
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "原密码不正确"},
        )
    user.password_hash = hash_password(body.newPassword)
    await session.commit()
    return JSONResponse({"success": True})
