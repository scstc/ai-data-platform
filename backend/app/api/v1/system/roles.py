"""系统-角色:CRUD + 菜单/部门授权。"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from app.api.deps import SessionDep, require_perm
from app.models.rbac_links import RoleDept, RoleMenu, UserRole
from app.models.role import Role
from app.schemas.system import RoleCreate, RoleRead, RoleUpdate

router = APIRouter(prefix="/system/roles", tags=["system-roles"])


def _new_id() -> str:
    """生成形如 role-<6位hex> 的主键。"""
    return f"role-{secrets.token_hex(3)}"


def _role_payload(role: Role) -> dict:
    return RoleRead.model_validate(role).model_dump(by_alias=True, mode="json")


@router.get("", dependencies=[Depends(require_perm("system:role:list"))])
async def list_roles(
    session: SessionDep,
    current: int = 1,
    pageSize: int = 20,
    keyword: str | None = None,
) -> JSONResponse:
    """分页列出角色(可按名称模糊)。"""
    stmt = select(Role)
    if keyword:
        stmt = stmt.where(Role.name.ilike(f"%{keyword}%"))
    total = (
        await session.scalar(select(func.count()).select_from(stmt.subquery()))
    ) or 0
    rows = (
        await session.scalars(
            stmt.order_by(Role.sort)
            .offset((current - 1) * pageSize)
            .limit(pageSize)
        )
    ).all()
    return JSONResponse(
        {
            "data": [_role_payload(r) for r in rows],
            "total": total,
            "success": True,
        }
    )


@router.get("/{role_id}", dependencies=[Depends(require_perm("system:role:list"))])
async def get_role(role_id: str, session: SessionDep) -> JSONResponse:
    """角色详情 + 当前授权的 menuIds / deptIds。"""
    role = await session.get(Role, role_id)
    if role is None:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "角色不存在"}
        )
    menu_ids = list(
        (
            await session.scalars(
                select(RoleMenu.menu_id).where(RoleMenu.role_id == role_id)
            )
        ).all()
    )
    dept_ids = list(
        (
            await session.scalars(
                select(RoleDept.dept_id).where(RoleDept.role_id == role_id)
            )
        ).all()
    )
    data = _role_payload(role) | {"menuIds": menu_ids, "deptIds": dept_ids}
    return JSONResponse({"data": data, "success": True})


@router.post("", dependencies=[Depends(require_perm("system:role:add"))])
async def create_role(body: RoleCreate, session: SessionDep) -> JSONResponse:
    """新建角色 + 写菜单/部门授权;role_key 重复 409。"""
    dup = await session.scalar(
        select(Role.id).where(Role.role_key == body.role_key)
    )
    if dup is not None:
        return JSONResponse(
            status_code=409,
            content={"success": False, "message": "角色标识已存在"},
        )
    role = Role(
        id=_new_id(),
        name=body.name,
        role_key=body.role_key,
        sort=body.sort,
        data_scope=body.data_scope,
        status=body.status,
        remark=body.remark,
    )
    session.add(role)
    for mid in body.menu_ids:
        session.add(RoleMenu(role_id=role.id, menu_id=mid))
    for did in body.dept_ids:
        session.add(RoleDept(role_id=role.id, dept_id=did))
    await session.commit()
    await session.refresh(role)
    return JSONResponse({"data": _role_payload(role), "success": True})


@router.put("/{role_id}", dependencies=[Depends(require_perm("system:role:edit"))])
async def update_role(
    role_id: str, body: RoleUpdate, session: SessionDep
) -> JSONResponse:
    """改角色;传 menu_ids/dept_ids 则整体替换授权。"""
    role = await session.get(Role, role_id)
    if role is None:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "角色不存在"}
        )
    updates = body.model_dump(exclude_unset=True)
    menu_ids = updates.pop("menu_ids", None)
    dept_ids = updates.pop("dept_ids", None)
    for field, value in updates.items():
        setattr(role, field, value)
    if menu_ids is not None:
        await session.execute(
            RoleMenu.__table__.delete().where(RoleMenu.role_id == role_id)
        )
        for mid in menu_ids:
            session.add(RoleMenu(role_id=role_id, menu_id=mid))
    if dept_ids is not None:
        await session.execute(
            RoleDept.__table__.delete().where(RoleDept.role_id == role_id)
        )
        for did in dept_ids:
            session.add(RoleDept(role_id=role_id, dept_id=did))
    await session.commit()
    await session.refresh(role)
    return JSONResponse({"data": _role_payload(role), "success": True})


@router.delete(
    "/{role_id}", dependencies=[Depends(require_perm("system:role:remove"))]
)
async def delete_role(role_id: str, session: SessionDep) -> JSONResponse:
    """删除角色:超管角色禁删 409;被用户引用 409;否则删 + 清授权。"""
    role = await session.get(Role, role_id)
    if role is None:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "角色不存在"}
        )
    if role.role_key == "admin":
        return JSONResponse(
            status_code=409,
            content={"success": False, "message": "超级管理员角色不可删除"},
        )
    assigned = (
        await session.scalar(
            select(func.count())
            .select_from(UserRole)
            .where(UserRole.role_id == role_id)
        )
    ) or 0
    if assigned > 0:
        return JSONResponse(
            status_code=409,
            content={
                "success": False,
                "message": f"角色已分配给 {assigned} 个用户,无法删除",
            },
        )
    await session.execute(
        RoleMenu.__table__.delete().where(RoleMenu.role_id == role_id)
    )
    await session.execute(
        RoleDept.__table__.delete().where(RoleDept.role_id == role_id)
    )
    await session.delete(role)
    await session.commit()
    return JSONResponse({"success": True})
