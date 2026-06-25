# RBAC P2 后端五模块 CRUD 实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: superpowers:executing-plans。Steps 用 `- [ ]` 勾选。

**Goal:** 在 P0+P1 地基上,提供「系统管理」用户/角色/部门/菜单的 CRUD + 授权端点(user-role / role-menu / role-dept)+ 权限总览,全部用 `require_perm` 细粒度门控(超管 `*:*:*` 通过)。

**Architecture:** 镜像现有 `app/api/v1/categories.py` 的 CRUD 范式(`secrets.token_hex(3)` 主键、`JSONResponse {data/success}`、404/409 守卫、树形 parent 存在性+环检测)。新增 `schemas/system.py` 与 `app/api/v1/system/{roles,users,depts,menus,permissions}.py`,在 `main.py` 注册。部门/菜单是树(仿 categories);用户/角色是分页表(`PageResponse[T]`)。

**Tech Stack:** FastAPI、SQLAlchemy 2.0 async、Pydantic(`CamelModel`)、pytest + httpx。

## Global Constraints

- 主键:`role-`/`dept-`/`menu-` + `secrets.token_hex(3)`(用户沿用 `usr-`)。
- 写端点门控:`dependencies=[Depends(require_perm("system:<mod>:<act>"))]`,act ∈ {list 不门控读? 见下, add, edit, remove}。读端点 `require_user`(登录即可)。**resetPwd 用 `system:user:edit`;角色授权用 `system:role:edit`**(复用已种子的 edit 码,不新增码)。
- 种子已存在的码:`system:{user,role,dept,menu}:{list,add,edit,remove}`、`system:perm:{list,add,edit,remove}`(见 0019)。
- 响应:`CamelModel.model_dump(by_alias=True, mode="json")`;错误 `{success:False, message:...}`。
- 密码:`app.services.auth.hash_password`;username 唯一(409)。
- `users.role` 旧列继续填:创建/改用户时,若其角色含 role_key=`admin` 则旧列写 `admin`,否则 `user`(维持 `require_admin` 语义)。
- 删除守卫:角色被用户引用→409;部门有子或有用户→409;菜单有子→409。
- 测试库 create_all + `seed_rbac`/`seed_users` fixture;远程库慢(~12s/用例),按任务窄跑。
- 鉴权用例可用 `sign_token("u-super")`(seed_rbac,role=admin⇒通配)过 require_perm。

## File Structure

- Create `backend/app/schemas/system.py` — Role/Dept/Menu/User 的 Create/Update/Read + 授权体 + `PageResponse` 复用。
- Create `backend/app/api/v1/system/roles.py`、`users.py`、`depts.py`、`menus.py`、`permissions.py`。
- Modify `backend/app/api/v1/system/__init__.py`(无需,routers 各自注册)、`backend/app/main.py`(include 五 router)。
- Modify `backend/app/services/rbac.py` — 加 `_new_id` 复用?否,各 router 内联 `_new_id`(同 categories 风格)。加 `get_user_menu_ids`/`ancestors` 助手按需。
- Tests:`backend/tests/test_system_roles.py`、`test_system_users.py`、`test_system_depts.py`、`test_system_menus.py`、`test_system_permissions.py`。

---

### Task 1: schemas/system.py

**Files:** Create `backend/app/schemas/system.py`;Test `backend/tests/test_system_roles.py`(占位 import 测试)

**Interfaces — Produces:**
- `RoleRead(id, name, roleKey, sort, dataScope, status, remark, createdAt)`、`RoleCreate(name, role_key, sort?, data_scope?, status?, remark?, menu_ids?: list[str], dept_ids?: list[str])`、`RoleUpdate`(全可选)。
- `DeptRead(id, parentId, ancestors, name, sort, leader?, phone?, email?, status, children: list[DeptRead])`、`DeptCreate(name, parent_id?, sort?, leader?, phone?, email?, status?)`、`DeptUpdate`。
- `MenuRead(id, parentId, name, menuType, path?, component?, perms?, icon?, sort, visible, status, isFrame, children: list[MenuRead])`、`MenuCreate(...)`、`MenuUpdate`。
- `UserRead(id, username, displayName?, deptId?, status(disabled 反转?保留 disabled), roles: list[str], createdAt)`、`UserCreate(username, password, display_name?, dept_id?, role_ids: list[str])`、`UserUpdate(display_name?, dept_id?, role_ids?, disabled?)`、`ResetPwd(password)`。
- 复用 `app.schemas.common.PageResponse`、`CamelModel`、`UtcDateTime`。

- [ ] **Step 1: 写最小 import 测试**(`test_system_roles.py`)

```python
"""系统-角色 API 测试。"""
from __future__ import annotations
import pytest
pytestmark = pytest.mark.asyncio


async def test_schemas_importable() -> None:
    from app.schemas.system import RoleCreate, RoleRead
    r = RoleCreate(name="x", role_key="x")
    assert r.data_scope == "self"  # 默认
    assert RoleRead.model_config["from_attributes"] is True
```

- [ ] **Step 2: 运行 → 失败**(`ModuleNotFoundError: app.schemas.system`)

Run: `./.venv/Scripts/python.exe -m pytest tests/test_system_roles.py::test_schemas_importable -q`(注:设 `TEST_DATABASE_URL`,见地基计划)

- [ ] **Step 3: 写 `schemas/system.py`**

```python
"""系统管理 schema:角色/部门/菜单/用户(CamelModel:snake⇄camel)。"""

from __future__ import annotations

from app.schemas.common import CamelModel, UtcDateTime


class RoleCreate(CamelModel):
    name: str
    role_key: str
    sort: int = 0
    data_scope: str = "self"
    status: str = "0"
    remark: str | None = None
    menu_ids: list[str] = []
    dept_ids: list[str] = []


class RoleUpdate(CamelModel):
    name: str | None = None
    sort: int | None = None
    data_scope: str | None = None
    status: str | None = None
    remark: str | None = None
    menu_ids: list[str] | None = None
    dept_ids: list[str] | None = None


class RoleRead(CamelModel):
    id: str
    name: str
    role_key: str
    sort: int
    data_scope: str
    status: str
    remark: str | None = None
    created_at: UtcDateTime


class DeptCreate(CamelModel):
    name: str
    parent_id: str | None = None
    sort: int = 0
    leader: str | None = None
    phone: str | None = None
    email: str | None = None
    status: str = "0"


class DeptUpdate(CamelModel):
    name: str | None = None
    parent_id: str | None = None
    sort: int | None = None
    leader: str | None = None
    phone: str | None = None
    email: str | None = None
    status: str | None = None


class DeptRead(CamelModel):
    id: str
    parent_id: str | None = None
    ancestors: str
    name: str
    sort: int
    leader: str | None = None
    phone: str | None = None
    email: str | None = None
    status: str
    children: list[DeptRead] = []


class MenuCreate(CamelModel):
    name: str
    parent_id: str | None = None
    menu_type: str  # M|C|F
    path: str | None = None
    component: str | None = None
    perms: str | None = None
    icon: str | None = None
    sort: int = 0
    visible: str = "0"
    status: str = "0"
    is_frame: bool = False


class MenuUpdate(CamelModel):
    name: str | None = None
    parent_id: str | None = None
    menu_type: str | None = None
    path: str | None = None
    component: str | None = None
    perms: str | None = None
    icon: str | None = None
    sort: int | None = None
    visible: str | None = None
    status: str | None = None
    is_frame: bool | None = None


class MenuRead(CamelModel):
    id: str
    parent_id: str | None = None
    name: str
    menu_type: str
    path: str | None = None
    component: str | None = None
    perms: str | None = None
    icon: str | None = None
    sort: int
    visible: str
    status: str
    is_frame: bool
    children: list[MenuRead] = []


class UserCreate(CamelModel):
    username: str
    password: str
    display_name: str | None = None
    dept_id: str | None = None
    role_ids: list[str] = []


class UserUpdate(CamelModel):
    display_name: str | None = None
    dept_id: str | None = None
    role_ids: list[str] | None = None
    disabled: bool | None = None


class ResetPwd(CamelModel):
    password: str


class UserRead(CamelModel):
    id: str
    username: str
    display_name: str | None = None
    dept_id: str | None = None
    disabled: bool
    roles: list[str] = []
    created_at: UtcDateTime
```

- [ ] **Step 4: 运行 → 通过**;**Step 5: 提交** `git commit -m "feat(rbac): 系统管理 schema(角色/部门/菜单/用户)"`

---

### Task 2: 角色 CRUD + 授权(canonical 范式)

**Files:** Create `backend/app/api/v1/system/roles.py`;Modify `main.py`;Test `tests/test_system_roles.py`

**Interfaces — Produces(全部挂 `/api/v1` 前缀,router `prefix="/system/roles"`):**
- `GET /system/roles?current&pageSize&keyword?` → `PageResponse[RoleRead]`(require_perm `system:role:list`)。
- `GET /system/roles/{id}` → `{data: RoleRead + menuIds[] + deptIds[]}`(`system:role:list`)。
- `POST /system/roles`(`system:role:add`)→ 建角色 + 写 role_menus/role_depts;role_key 重复 409。
- `PUT /system/roles/{id}`(`system:role:edit`)→ 改;含 menu_ids/dept_ids 则整体替换授权。
- `DELETE /system/roles/{id}`(`system:role:remove`)→ 被用户引用(user_roles)409;role_key=`admin` 禁删 409;否则删 + 清 role_menus/role_depts。

- [ ] **Step 1: 写测试**(`test_system_roles.py` 追加)

```python
async def test_role_crud_and_authorize(client, seed_rbac) -> None:
    from app.services.auth import sign_token
    client.cookies.set("adp_session", sign_token("u-super"))  # 通配

    # 建
    r = await client.post("/api/v1/system/roles", json={
        "name": "数据员", "roleKey": "dataops", "dataScope": "dept",
        "menuIds": ["m-user"], "deptIds": []})
    assert r.status_code == 200, r.text
    rid = r.json()["data"]["id"]

    # 列表含新角色
    lst = await client.get("/api/v1/system/roles?current=1&pageSize=20")
    assert lst.status_code == 200
    assert any(x["roleKey"] == "dataops" for x in lst.json()["data"])

    # 详情带 menuIds
    detail = await client.get(f"/api/v1/system/roles/{rid}")
    assert "m-user" in detail.json()["data"]["menuIds"]

    # role_key 重复 409
    dup = await client.post("/api/v1/system/roles", json={"name": "x", "roleKey": "dataops"})
    assert dup.status_code == 409

    # 删
    d = await client.delete(f"/api/v1/system/roles/{rid}")
    assert d.status_code == 200


async def test_role_delete_blocked_when_assigned(client, seed_rbac) -> None:
    """r-dc 已分配给 u-mgr ⇒ 删除 409。"""
    from app.services.auth import sign_token
    client.cookies.set("adp_session", sign_token("u-super"))
    d = await client.delete("/api/v1/system/roles/r-dc")
    assert d.status_code == 409


async def test_role_list_forbidden_without_perm(client, seed_rbac) -> None:
    """u-staff(self 角色,无 system:role:list)→ 403。"""
    from app.services.auth import sign_token
    client.cookies.set("adp_session", sign_token("u-staff"))
    r = await client.get("/api/v1/system/roles?current=1&pageSize=20")
    assert r.status_code == 403
```

- [ ] **Step 2: 运行 → 失败**(404,路由未注册)

- [ ] **Step 3: 写 `system/roles.py`**

```python
"""系统-角色:CRUD + 菜单/部门授权。"""

from __future__ import annotations

import secrets
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import func, select

from app.api.deps import SessionDep, require_perm, require_user
from app.models.rbac_links import RoleDept, RoleMenu, UserRole
from app.models.role import Role
from app.schemas.system import RoleCreate, RoleRead, RoleUpdate

router = APIRouter(prefix="/system/roles", tags=["system-roles"])


def _new_id() -> str:
    return f"role-{secrets.token_hex(3)}"


def _role_payload(role: Role) -> dict:
    return RoleRead.model_validate(role).model_dump(by_alias=True, mode="json")


@router.get("", dependencies=[Depends(require_perm("system:role:list"))])
async def list_roles(
    session: SessionDep, current: int = 1, pageSize: int = 20, keyword: str | None = None
) -> JSONResponse:
    stmt = select(Role)
    if keyword:
        stmt = stmt.where(Role.name.ilike(f"%{keyword}%"))
    total = await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (
        await session.scalars(
            stmt.order_by(Role.sort).offset((current - 1) * pageSize).limit(pageSize)
        )
    ).all()
    return JSONResponse({
        "data": [_role_payload(r) for r in rows], "total": total, "success": True,
    })


@router.get("/{role_id}", dependencies=[Depends(require_perm("system:role:list"))])
async def get_role(role_id: str, session: SessionDep) -> JSONResponse:
    role = await session.get(Role, role_id)
    if role is None:
        return JSONResponse(status_code=404, content={"success": False, "message": "角色不存在"})
    menu_ids = list((await session.scalars(
        select(RoleMenu.menu_id).where(RoleMenu.role_id == role_id))).all())
    dept_ids = list((await session.scalars(
        select(RoleDept.dept_id).where(RoleDept.role_id == role_id))).all())
    data = _role_payload(role) | {"menuIds": menu_ids, "deptIds": dept_ids}
    return JSONResponse({"data": data, "success": True})


@router.post("", dependencies=[Depends(require_perm("system:role:add"))])
async def create_role(body: RoleCreate, session: SessionDep) -> JSONResponse:
    dup = await session.scalar(select(Role.id).where(Role.role_key == body.role_key))
    if dup is not None:
        return JSONResponse(status_code=409, content={"success": False, "message": "角色标识已存在"})
    role = Role(id=_new_id(), name=body.name, role_key=body.role_key, sort=body.sort,
                data_scope=body.data_scope, status=body.status, remark=body.remark)
    session.add(role)
    for mid in body.menu_ids:
        session.add(RoleMenu(role_id=role.id, menu_id=mid))
    for did in body.dept_ids:
        session.add(RoleDept(role_id=role.id, dept_id=did))
    await session.commit()
    await session.refresh(role)
    return JSONResponse({"data": _role_payload(role), "success": True})


@router.put("/{role_id}", dependencies=[Depends(require_perm("system:role:edit"))])
async def update_role(role_id: str, body: RoleUpdate, session: SessionDep) -> JSONResponse:
    role = await session.get(Role, role_id)
    if role is None:
        return JSONResponse(status_code=404, content={"success": False, "message": "角色不存在"})
    updates = body.model_dump(exclude_unset=True)
    menu_ids = updates.pop("menu_ids", None)
    dept_ids = updates.pop("dept_ids", None)
    for field, value in updates.items():
        setattr(role, field, value)
    if menu_ids is not None:  # 整体替换授权
        await session.execute(RoleMenu.__table__.delete().where(RoleMenu.role_id == role_id))
        for mid in menu_ids:
            session.add(RoleMenu(role_id=role_id, menu_id=mid))
    if dept_ids is not None:
        await session.execute(RoleDept.__table__.delete().where(RoleDept.role_id == role_id))
        for did in dept_ids:
            session.add(RoleDept(role_id=role_id, dept_id=did))
    await session.commit()
    await session.refresh(role)
    return JSONResponse({"data": _role_payload(role), "success": True})


@router.delete("/{role_id}", dependencies=[Depends(require_perm("system:role:remove"))])
async def delete_role(role_id: str, session: SessionDep) -> JSONResponse:
    role = await session.get(Role, role_id)
    if role is None:
        return JSONResponse(status_code=404, content={"success": False, "message": "角色不存在"})
    if role.role_key == "admin":
        return JSONResponse(status_code=409, content={"success": False, "message": "超级管理员角色不可删除"})
    assigned = await session.scalar(
        select(func.count()).select_from(UserRole).where(UserRole.role_id == role_id)) or 0
    if assigned > 0:
        return JSONResponse(status_code=409, content={"success": False, "message": f"角色已分配给 {assigned} 个用户,无法删除"})
    await session.execute(RoleMenu.__table__.delete().where(RoleMenu.role_id == role_id))
    await session.execute(RoleDept.__table__.delete().where(RoleDept.role_id == role_id))
    await session.delete(role)
    await session.commit()
    return JSONResponse({"success": True})
```

- [ ] **Step 4: 注册** `main.py`:import `from app.api.v1.system import roles as system_roles`(并入既有 system import 行)+ `app.include_router(system_roles.router, prefix="/api/v1")`。

- [ ] **Step 5: 运行 → 通过**;**Step 6: 提交** `git commit -m "feat(rbac): 角色 CRUD + 菜单/部门授权端点"`

---

### Task 3: 用户 CRUD + 重置密码 + 分配角色

**Files:** Create `backend/app/api/v1/system/users.py`;Modify `main.py`;Test `tests/test_system_users.py`

**Interfaces — Produces(router `prefix="/system/users"`):**
- `GET /system/users?current&pageSize&keyword?&deptId?` → `PageResponse[UserRead]`(`system:user:list`;每行 roles=role_key 列表)。
- `POST /system/users`(`system:user:add`)→ username 重复 409;`hash_password`;写 user_roles;旧 `role` 列按是否含 admin 角色置 `admin`/`user`。
- `PUT /system/users/{id}`(`system:user:edit`)→ 改 display_name/dept_id/disabled/role_ids(整体替换);同步旧 `role` 列。
- `PUT /system/users/{id}/password`(`system:user:edit`)→ ResetPwd。
- `DELETE /system/users/{id}`(`system:user:remove`)→ username=`admin` 禁删 409;否则删 + 清 user_roles。

**关键逻辑(同步旧 role 列):** 解析 role_ids 对应的 Role.role_key,若含 `admin` 则 `user.role="admin"` 否则 `"user"`。helper:
```python
async def _sync_legacy_role(session, user, role_ids: list[str]) -> None:
    keys = list((await session.scalars(select(Role.role_key).where(Role.id.in_(role_ids)))).all()) if role_ids else []
    user.role = "admin" if "admin" in keys else "user"
```

- [ ] **Step 1: 测试**(`test_system_users.py`)

```python
"""系统-用户 API 测试。"""
from __future__ import annotations
import pytest
pytestmark = pytest.mark.asyncio


async def test_user_create_list_resetpwd(client, seed_rbac) -> None:
    from app.services.auth import sign_token, verify_password
    from sqlalchemy import select
    client.cookies.set("adp_session", sign_token("u-super"))

    r = await client.post("/api/v1/system/users", json={
        "username": "zhang", "password": "p@ss1234", "displayName": "张三",
        "deptId": "d-a", "roleIds": ["r-dc"]})
    assert r.status_code == 200, r.text
    uid = r.json()["data"]["id"]

    lst = await client.get("/api/v1/system/users?current=1&pageSize=50")
    assert any(u["username"] == "zhang" and "r_dc" in u["roles"] for u in lst.json()["data"])

    # 重复 username 409
    dup = await client.post("/api/v1/system/users", json={"username": "zhang", "password": "x1234567"})
    assert dup.status_code == 409

    # 重置密码
    rp = await client.put(f"/api/v1/system/users/{uid}/password", json={"password": "new12345"})
    assert rp.status_code == 200


async def test_user_admin_undeletable(client, seed_users) -> None:
    from app.services.auth import sign_token
    client.cookies.set("adp_session", sign_token("admin"))
    # seed_users 的 admin(usr-test01) username=admin
    d = await client.delete("/api/v1/system/users/usr-test01")
    assert d.status_code == 409
```

- [ ] **Step 2–6:** 写 `users.py`(仿 roles.py CRUD;create 用 `hash_password`、`_sync_legacy_role`;list 每行附 roles via 一次 user_roles+roles join 批量,避免 N+1)、注册 main.py、跑测试、提交 `git commit -m "feat(rbac): 用户 CRUD + 重置密码 + 分配角色(同步旧 role 列)"`。

> users.py 完整代码骨架同 roles.py;差异点:① create/`hash_password`;② `_sync_legacy_role`;③ list 批量回填 roles(`select(UserRole.user_id, Role.role_key).join(Role).where(UserRole.user_id.in_(ids))` 聚成 dict);④ username 唯一守卫;⑤ `admin` 用户禁删/禁停。读模型 `UserRead`(disabled 原样)。

---

### Task 4: 部门树 CRUD(仿 categories)

**Files:** Create `backend/app/api/v1/system/depts.py`;Modify `main.py`;Test `tests/test_system_depts.py`

**Interfaces — Produces(router `prefix="/system/depts"`):**
- `GET /system/depts` → `{data: [DeptRead 树], success}`(`system:dept:list`;仿 categories 组树)。
- `POST /system/depts`(`system:dept:add`)→ 上级不存在 404;计算 `ancestors`=父.ancestors + 父id + ","(根父=None ⇒ `"0"`)。
- `PUT /system/depts/{id}`(`system:dept:edit`)→ 改;移父做环检测(同 categories `_is_self_or_descendant`)+ 重算本节点及**所有后代**的 ancestors。
- `DELETE /system/depts/{id}`(`system:dept:remove`)→ 有子部门 409;有用户(users.dept_id)409;根 `dept-000000` 禁删 409。

**ancestors 维护:** 建/移时 `ancestors = ("0" if not parent else parent.ancestors + parent.id + ",")`。移父后,对每个后代 `d`:把其 ancestors 中本节点旧前缀替换为新前缀(或按新父链重算子树)。helper:
```python
async def _reseat_subtree(session, node, old_anc, new_anc) -> None:
    """node.ancestors: old_anc→new_anc;后代 ancestors 同样把 old_anc+node.id 前缀换成 new_anc+node.id。"""
    old_prefix = f"{old_anc}{node.id},"
    new_prefix = f"{new_anc}{node.id},"
    rows = (await session.scalars(select(Department).where(Department.ancestors.like(f"{old_prefix}%")))).all()
    for d in rows:
        d.ancestors = new_prefix + d.ancestors[len(old_prefix):]
    node.ancestors = new_anc
```

- [ ] **Step 1: 测试**(`test_system_depts.py`)— 建子部门 ancestors 正确;移父后子树 ancestors 重算;有用户的部门删 409;根禁删。

```python
async def test_dept_create_ancestors_and_guard(client, seed_rbac) -> None:
    from app.services.auth import sign_token
    client.cookies.set("adp_session", sign_token("u-super"))
    r = await client.post("/api/v1/system/depts", json={"name": "丙", "parentId": "d-a"})
    assert r.status_code == 200, r.text
    assert r.json()["data"]["ancestors"] == "0,d-root,d-a,"
    # d-a 有用户 u-staff ⇒ 删 409
    d = await client.delete("/api/v1/system/depts/d-a")
    assert d.status_code == 409
```

- [ ] **Step 2–6:** 写 `depts.py`(树组装仿 `categories.list_categories`;环检测复用 `_is_self_or_descendant` 思路;ancestors 维护如上)、注册、跑测试、提交 `git commit -m "feat(rbac): 部门树 CRUD(ancestors 维护 + 子树重排 + 守卫)"`。

---

### Task 5: 菜单树 CRUD

**Files:** Create `backend/app/api/v1/system/menus.py` 扩展(已有 getRouters,同文件加 CRUD);Modify `main.py`(已注册 system_menus.router,**复用**);Test `tests/test_system_menus.py`

**Interfaces — Produces(同 router `prefix="/system/menus"`,与 getRouters 同文件):**
- `GET /system/menus` → `{data: [MenuRead 树], success}`(`system:menu:list`;全量树,含 F)。
- `POST /system/menus`(`system:menu:add`)→ menu_type 校验 {M,C,F};上级不存在 404。
- `PUT /system/menus/{id}`(`system:menu:edit`)→ 改;移父环检测。
- `DELETE /system/menus/{id}`(`system:menu:remove`)→ 有子 409;清 role_menus 引用后删(或被授权时 409,取**有子 409**;授权引用随删清理 role_menus)。

> 注:`/system/menus/routers`(getRouters,P1)路径与 `/system/menus`(列表)不冲突(后者无尾段)。CRUD 用 `secrets.token_hex(3)` → `menu-xxxxxx`。

- [ ] **Step 1: 测试**(`test_system_menus.py`)— 建 F 按钮挂在 C 下;树含 F;有子菜单删 409;`require_perm` 门控。
- [ ] **Step 2–6:** 在 `system/menus.py` 追加 CRUD(树组装含全部 menu_type;删除清 role_menus)、跑测试、提交 `git commit -m "feat(rbac): 菜单树 CRUD(M/C/F + perms 维护)"`。

---

### Task 6: 权限总览 + 全量回归

**Files:** Create `backend/app/api/v1/system/permissions.py`;Modify `main.py`;Test `tests/test_system_permissions.py`

**Interfaces — Produces(router `prefix="/system/permissions"`):**
- `GET /system/permissions/overview`(`system:perm:list`)→ `{data: {roles:[{id,name,roleKey,perms:[...]}], menus:[全部 F 的 perms 去重]}, success}`。供「权限管理」页做角色×权限总览。perms 经 role_menus→menus(F).perms 聚合(超管角色标 `*:*:*`)。

- [ ] **Step 1: 测试**(`test_system_permissions.py`)— overview 返回 roles 列含 r-dc 且其 perms 含 `system:user:add`;门控 403(u-staff)。
- [ ] **Step 2–4:** 写 `permissions.py`(聚合查询)、注册、跑测试、提交。
- [ ] **Step 5: 全量回归**(分批,远程库慢):
  - `pytest tests/test_system_*.py -q`(新五模块)
  - `pytest tests/test_rbac.py tests/test_rbac_kernel.py tests/test_compat_auth.py -q`(地基 + 兼容红线)
- [ ] **Step 6: 提交** `git commit -m "feat(rbac): 权限总览端点 + P2 全量回归"`

---

## Self-Review

- **Spec 覆盖**:§6「五页」之后端 CRUD(user/role/dept/menu)+ 授权(user-role/role-menu/role-dept)+ 权限总览 ⇒ Task 2–6 ✓。门控用 `require_perm` + 种子码 ✓。`users.role` 同步保旧兼容 ✓。
- **范围外**:P3(apply_data_scope 接业务读写端点)、P4/P5 前端。本计划只建系统管理 CRUD,不碰业务端点。
- **占位**:Task 3/4/5 的 CRUD 骨架以 Task 2(roles.py)与 `categories.py` 为模板,仅列差异点——属"遵循既有范式"而非占位;关键差异(密码/ancestors/树/守卫)均给了代码。
- **类型一致**:perm 码 `system:<mod>:<act>` 与 0019 种子一致;schema 字段与 ORM 列名一致;`_new_id` 前缀对应各表。

## 执行

按 Task 1→6 顺序(schema 先于 router;roles 为 canonical 范式,后三模块仿之)。每 Task 自带 TDD + 提交。完成后另起 P3(数据权限接业务)与前端 P4/P5 计划。
