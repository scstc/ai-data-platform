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
    user_count: int = 0
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
