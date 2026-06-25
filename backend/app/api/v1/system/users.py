"""系统-用户:CRUD + 重置密码 + 分配角色(同步遗留 users.role 列)。"""

from __future__ import annotations

import secrets
from collections import defaultdict

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import func, or_, select

from app.api.deps import SessionDep, require_perm
from app.models.rbac_links import UserRole
from app.models.role import Role
from app.models.user import User
from app.schemas.system import ResetPwd, UserCreate, UserRead, UserUpdate
from app.services.auth import hash_password

router = APIRouter(prefix="/system/users", tags=["system-users"])


def _new_id() -> str:
    """生成形如 usr-<6位hex> 的主键。"""
    return f"usr-{secrets.token_hex(3)}"


def _user_payload(user: User, roles: list[str]) -> dict:
    data = UserRead.model_validate(user).model_dump(by_alias=True, mode="json")
    data["roles"] = roles
    return data


async def _role_keys(session: SessionDep, role_ids: list[str]) -> list[str]:
    """给定角色 id 列表 → 其 role_key 列表(用于同步遗留 role 列与响应)。"""
    if not role_ids:
        return []
    return list(
        (
            await session.scalars(
                select(Role.role_key).where(Role.id.in_(role_ids))
            )
        ).all()
    )


async def _user_role_keys(session: SessionDep, user_id: str) -> list[str]:
    """用户当前已授角色的 role_key 列表。"""
    return list(
        (
            await session.scalars(
                select(Role.role_key)
                .join(UserRole, UserRole.role_id == Role.id)
                .where(UserRole.user_id == user_id)
            )
        ).all()
    )


def _legacy_role(role_keys: list[str]) -> str:
    """遗留 users.role 列:含 admin 角色 ⇒ admin,否则 user(维持 require_admin 语义)。"""
    return "admin" if "admin" in role_keys else "user"


@router.get("", dependencies=[Depends(require_perm("system:user:list"))])
async def list_users(
    session: SessionDep,
    current: int = 1,
    pageSize: int = 20,
    keyword: str | None = None,
    deptId: str | None = None,
) -> JSONResponse:
    """分页列出用户(可按用户名/昵称模糊、按部门过滤),每行附 role_key 列表。"""
    stmt = select(User)
    if keyword:
        stmt = stmt.where(
            or_(
                User.username.ilike(f"%{keyword}%"),
                User.display_name.ilike(f"%{keyword}%"),
            )
        )
    if deptId:
        stmt = stmt.where(User.dept_id == deptId)
    total = (
        await session.scalar(select(func.count()).select_from(stmt.subquery()))
    ) or 0
    rows = (
        await session.scalars(
            stmt.order_by(User.created_at.desc())
            .offset((current - 1) * pageSize)
            .limit(pageSize)
        )
    ).all()
    # 批量回填角色(一次 join 查询,避免 N+1)
    role_map: dict[str, list[str]] = defaultdict(list)
    ids = [u.id for u in rows]
    if ids:
        pairs = (
            await session.execute(
                select(UserRole.user_id, Role.role_key)
                .join(Role, Role.id == UserRole.role_id)
                .where(UserRole.user_id.in_(ids))
            )
        ).all()
        for uid, rkey in pairs:
            role_map[uid].append(rkey)
    return JSONResponse(
        {
            "data": [_user_payload(u, role_map.get(u.id, [])) for u in rows],
            "total": total,
            "success": True,
        }
    )


@router.post("", dependencies=[Depends(require_perm("system:user:add"))])
async def create_user(body: UserCreate, session: SessionDep) -> JSONResponse:
    """新建用户:username 重复 409;口令 PBKDF2 哈希;写 user_roles + 同步遗留 role 列。"""
    dup = await session.scalar(
        select(User.id).where(User.username == body.username)
    )
    if dup is not None:
        return JSONResponse(
            status_code=409,
            content={"success": False, "message": "用户名已存在"},
        )
    keys = await _role_keys(session, body.role_ids)
    user = User(
        id=_new_id(),
        username=body.username,
        password_hash=hash_password(body.password),
        role=_legacy_role(keys),
        display_name=body.display_name,
        dept_id=body.dept_id,
        disabled=False,
    )
    session.add(user)
    for rid in body.role_ids:
        session.add(UserRole(user_id=user.id, role_id=rid))
    await session.commit()
    await session.refresh(user)
    return JSONResponse({"data": _user_payload(user, keys), "success": True})


@router.put("/{user_id}", dependencies=[Depends(require_perm("system:user:edit"))])
async def update_user(
    user_id: str, body: UserUpdate, session: SessionDep
) -> JSONResponse:
    """改昵称/部门/启停/角色;传 role_ids 则整体替换并同步遗留 role 列。"""
    user = await session.get(User, user_id)
    if user is None:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "用户不存在"}
        )
    updates = body.model_dump(exclude_unset=True)
    role_ids = updates.pop("role_ids", None)
    for field, value in updates.items():
        setattr(user, field, value)
    if role_ids is not None:
        await session.execute(
            UserRole.__table__.delete().where(UserRole.user_id == user_id)
        )
        for rid in role_ids:
            session.add(UserRole(user_id=user_id, role_id=rid))
        user.role = _legacy_role(await _role_keys(session, role_ids))
    await session.commit()
    await session.refresh(user)
    keys = await _user_role_keys(session, user_id)
    return JSONResponse({"data": _user_payload(user, keys), "success": True})


@router.put(
    "/{user_id}/password",
    dependencies=[Depends(require_perm("system:user:edit"))],
)
async def reset_password(
    user_id: str, body: ResetPwd, session: SessionDep
) -> JSONResponse:
    """重置口令(管理员操作,无需旧口令)。"""
    user = await session.get(User, user_id)
    if user is None:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "用户不存在"}
        )
    user.password_hash = hash_password(body.password)
    await session.commit()
    return JSONResponse({"success": True})


@router.delete(
    "/{user_id}", dependencies=[Depends(require_perm("system:user:remove"))]
)
async def delete_user(user_id: str, session: SessionDep) -> JSONResponse:
    """删除用户:内置 admin 禁删 409;否则删 + 清 user_roles。"""
    user = await session.get(User, user_id)
    if user is None:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "用户不存在"}
        )
    if user.username == "admin":
        return JSONResponse(
            status_code=409,
            content={"success": False, "message": "内置管理员不可删除"},
        )
    await session.execute(
        UserRole.__table__.delete().where(UserRole.user_id == user_id)
    )
    await session.delete(user)
    await session.commit()
    return JSONResponse({"success": True})
