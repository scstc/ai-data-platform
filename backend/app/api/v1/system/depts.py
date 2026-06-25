"""系统-部门:树形 CRUD(ancestors 维护 + 移父子树重排 + 环检测 + 守卫)。

ancestors 约定:逗号分隔祖先 id 路径,每个 id 两侧带逗号以便 ``LIKE '%,<id>,%'``
子树查询(见 services/rbac.py)。根部门 ancestors 为哨兵 "0"。
"""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from app.api.deps import SessionDep, require_perm
from app.models.department import Department
from app.models.user import User
from app.schemas.system import DeptCreate, DeptRead, DeptUpdate

router = APIRouter(prefix="/system/depts", tags=["system-depts"])

_ROOT_DEPT = "dept-000000"


def _new_id() -> str:
    """生成形如 dept-<6位hex> 的主键。"""
    return f"dept-{secrets.token_hex(3)}"


def _child_ancestors(parent: Department) -> str:
    """子部门的 ancestors:父 ancestors 规整为单尾逗号后接 父id + ","。

    对根("0")得 "0,<rootId>,";对 "0,a," 得 "0,a,<id>,"。保证每个 id 两侧带逗号。
    """
    return f"{parent.ancestors.rstrip(',')},{parent.id},"


@router.get("", dependencies=[Depends(require_perm("system:dept:list"))])
async def list_depts(session: SessionDep) -> JSONResponse:
    """全部部门组装为树(同层按 sort)。"""
    rows = (
        await session.scalars(select(Department).order_by(Department.sort))
    ).all()
    by_id: dict[str, DeptRead] = {d.id: DeptRead.model_validate(d) for d in rows}
    roots: list[DeptRead] = []
    for d in rows:
        item = by_id[d.id]
        if d.parent_id and d.parent_id in by_id:
            by_id[d.parent_id].children.append(item)
        else:
            roots.append(item)
    data = [r.model_dump(by_alias=True, mode="json") for r in roots]
    return JSONResponse({"data": data, "success": True})


@router.post("", dependencies=[Depends(require_perm("system:dept:add"))])
async def create_dept(body: DeptCreate, session: SessionDep) -> JSONResponse:
    """新建部门;上级不存在 404;ancestors 由上级推导(根为 "0")。"""
    parent_id = body.parent_id or None
    if parent_id:
        parent = await session.get(Department, parent_id)
        if parent is None:
            return JSONResponse(
                status_code=404,
                content={"success": False, "message": "上级部门不存在"},
            )
        ancestors = _child_ancestors(parent)
    else:
        ancestors = "0"
    dept = Department(
        id=_new_id(),
        parent_id=parent_id,
        ancestors=ancestors,
        name=body.name,
        sort=body.sort,
        leader=body.leader,
        phone=body.phone,
        email=body.email,
        status=body.status,
    )
    session.add(dept)
    await session.commit()
    await session.refresh(dept)
    return JSONResponse(
        {
            "data": DeptRead.model_validate(dept).model_dump(
                by_alias=True, mode="json"
            ),
            "success": True,
        }
    )


@router.put("/{dept_id}", dependencies=[Depends(require_perm("system:dept:edit"))])
async def update_dept(
    dept_id: str, body: DeptUpdate, session: SessionDep
) -> JSONResponse:
    """改部门;移父做环检测并重排本节点及全部后代的 ancestors。"""
    dept = await session.get(Department, dept_id)
    if dept is None:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "部门不存在"}
        )
    updates = body.model_dump(exclude_unset=True)
    move = "parent_id" in updates
    new_parent_id = updates.pop("parent_id", None)
    for field, value in updates.items():
        setattr(dept, field, value)

    if move:
        new_parent_id = new_parent_id or None
        if new_parent_id == dept_id:
            return JSONResponse(
                status_code=409,
                content={"success": False, "message": "不能将上级设为自身"},
            )
        if new_parent_id:
            np = await session.get(Department, new_parent_id)
            if np is None:
                return JSONResponse(
                    status_code=404,
                    content={"success": False, "message": "上级部门不存在"},
                )
            # 新上级若是本节点的后代(ancestors 含本 id)则成环
            if f",{dept_id}," in np.ancestors:
                return JSONResponse(
                    status_code=409,
                    content={
                        "success": False,
                        "message": "不能将上级设为子部门(会成环)",
                    },
                )
            new_self = _child_ancestors(np)
        else:
            new_self = "0"
        # 重排后代:把旧前缀 <old_self>,<id>, 整体替换为 <new_self>,<id>,
        old_prefix = f"{dept.ancestors.rstrip(',')},{dept_id},"
        new_prefix = f"{new_self.rstrip(',')},{dept_id},"
        descendants = (
            await session.scalars(
                select(Department).where(
                    Department.ancestors.like(f"{old_prefix}%")
                )
            )
        ).all()
        for d in descendants:
            d.ancestors = new_prefix + d.ancestors[len(old_prefix) :]
        dept.ancestors = new_self
        dept.parent_id = new_parent_id

    await session.commit()
    await session.refresh(dept)
    return JSONResponse(
        {
            "data": DeptRead.model_validate(dept).model_dump(
                by_alias=True, mode="json"
            ),
            "success": True,
        }
    )


@router.delete(
    "/{dept_id}", dependencies=[Depends(require_perm("system:dept:remove"))]
)
async def delete_dept(dept_id: str, session: SessionDep) -> JSONResponse:
    """删除部门:根部门禁删;有子部门 409;有用户 409;否则删。"""
    dept = await session.get(Department, dept_id)
    if dept is None:
        return JSONResponse(
            status_code=404, content={"success": False, "message": "部门不存在"}
        )
    if dept_id == _ROOT_DEPT:
        return JSONResponse(
            status_code=409,
            content={"success": False, "message": "根部门不可删除"},
        )
    children = (
        await session.scalar(
            select(func.count())
            .select_from(Department)
            .where(Department.parent_id == dept_id)
        )
    ) or 0
    if children > 0:
        return JSONResponse(
            status_code=409,
            content={
                "success": False,
                "message": f"部门有 {children} 个子部门,请先处理",
            },
        )
    users = (
        await session.scalar(
            select(func.count()).select_from(User).where(User.dept_id == dept_id)
        )
    ) or 0
    if users > 0:
        return JSONResponse(
            status_code=409,
            content={"success": False, "message": f"部门下有 {users} 名用户,无法删除"},
        )
    await session.delete(dept)
    await session.commit()
    return JSONResponse({"success": True})
