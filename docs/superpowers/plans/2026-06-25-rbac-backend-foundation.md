# RBAC 后端地基(P0 地基 + P1 鉴权内核)实现计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 建立完整动态 RBAC 的后端地基——5+2 张 RBAC 表 + 业务表归属列 + 迁移种子,以及鉴权内核(角色/权限/数据范围解析、菜单树下发、`require_perm`、`getRouters`、扩展 currentUser),为后续 P2(CRUD)/P3(数据权限接业务)/前端 P4-P5 铺底。

**Architecture:** 沿用现有 async SQLAlchemy 2.0(`Mapped`/`mapped_column`)+ FastAPI + Alembic。新增 RBAC 表与归属列;`services/rbac.py` 承载所有鉴权计算(纯函数 + DB 查询,可独立单测);`api/deps.py` 加 `require_perm` 工厂依赖(`require_admin` 不动);新增 `getRouters` 端点;`compat.py` 的 currentUser 载荷扩 `roles/permissions`。会话机制(cookie+HMAC 令牌)不变。

**Tech Stack:** Python 3.11、FastAPI、SQLAlchemy 2.0 async、Alembic、pytest + pytest-asyncio + httpx ASGITransport、PostgreSQL。

## Global Constraints

- 主键格式 `xxx-{6hex}`(`role-`/`dept-`/`menu-`),与现有 `usr-`/`dset-` 一致。
- 迁移**自包含**:不 import `app` 代码;种子哈希(若需)内联 hashlib;`op.create_table`/`op.bulk_insert` 风格同 `0006`。
- 迁移顺序号严格递增:新迁移 `revision="0019_rbac_system"`,`down_revision="0018_dataset_tags"`。
- **`users.role` 列保留并继续填值;`api/deps.py` 的 `require_admin`/`require_user`/`current_user` 一字不改**(14 文件 90 处调用 + `test_rbac.py` 红线)。
- 超管判定:`user.role == "admin"` ⇒ 权限通配 `{"*:*:*"}`(桥接遗留 admin,且兼容测试库 `seed_users` 无 user_roles 的情形)。
- `data_scope ∈ {all, custom, dept, dept_and_child, self}`;`menu_type ∈ {M, C, F}`;`status`/`visible` 用 `"0"`正常/显、`"1"`停用/隐(字符串,同若依风味)。
- 不引第三方新依赖。
- 测试库经 `Base.metadata.create_all` 初始化(**非 alembic**):新模型必须注册进 `app/models/__init__.py` 才会被建表;迁移种子在测试库**不存在**,鉴权用例靠新增 `seed_rbac` fixture 造数。
- 新模型加的列一律 `nullable=True` 或带 `default`/`server_default`,确保 `create_all` 与存量行回填都不破坏现有用例。

## File Structure

**新建:**
- `backend/app/models/role.py` — `Role`(角色)
- `backend/app/models/department.py` — `Department`(部门树)
- `backend/app/models/menu.py` — `Menu`(菜单=路由+按钮权限)
- `backend/app/models/rbac_links.py` — `UserRole`/`RoleMenu`/`RoleDept`(三张连接表,变更同源,合一文件)
- `backend/app/services/rbac.py` — 鉴权内核(perms/data_scope/菜单树/`apply_data_scope`)
- `backend/app/api/v1/system/__init__.py`、`backend/app/api/v1/system/menus.py` — `getRouters` 端点(系统域 router,后续 P2 在此目录扩 users/roles/depts/menus CRUD)
- `backend/alembic/versions/0019_rbac_system.py` — 建表 + 业务列 + 种子 + 回填
- `backend/tests/test_rbac_kernel.py` — 鉴权内核单测
- `backend/tests/test_system_routers.py` — getRouters + currentUser 扩展 API 测试

**修改:**
- `backend/app/models/user.py` — `+ dept_id`
- `backend/app/models/dataset.py`、`datasource.py`、`ingest_task.py`、`job.py`、`upload.py` — `+ dept_id`;`datasource/ingest_task/upload` 另 `+ creator`
- `backend/app/models/__init__.py` — 注册新模型
- `backend/app/api/deps.py` — `+ require_perm(code)`(仅追加)
- `backend/app/api/compat.py` — currentUser 载荷加 `roles`/`permissions`
- `backend/app/main.py` — 注册 system router
- `backend/tests/conftest.py` — `+ seed_rbac` fixture

**注:** 业务表的 `dept_id`/`creator` 列在 P0 一次性加好(schema 就位),但**按部门过滤的接入逻辑在 P3**;本计划只加列 + 回填,不改业务查询。

---

### Task 1: RBAC 核心 ORM 模型

**Files:**
- Create: `backend/app/models/role.py`, `backend/app/models/department.py`, `backend/app/models/menu.py`, `backend/app/models/rbac_links.py`
- Modify: `backend/app/models/__init__.py`
- Test: `backend/tests/test_rbac_kernel.py`

**Interfaces:**
- Produces: `Role(id, name, role_key, sort, data_scope, status, remark, created_at, updated_at)`;`Department(id, parent_id, ancestors, name, sort, leader, phone, email, status, created_at)`;`Menu(id, parent_id, name, menu_type, path, component, perms, icon, sort, visible, status, is_frame, query, created_at)`;`UserRole(user_id, role_id)`、`RoleMenu(role_id, menu_id)`、`RoleDept(role_id, dept_id)`(均复合主键)。

- [ ] **Step 1: 写失败测试**(`backend/tests/test_rbac_kernel.py`)

```python
"""RBAC 鉴权内核测试(DB 后端,经 create_all 建表 + seed_rbac 造数)。

测试意图(为何重要):
- self 范围用户的列表**绝不**含他人数据——这是数据权限的根本红线(不靠前端);
- 多角色权限聚合 / 最宽 data_scope 选取 / 部门子树 / 菜单树裁剪必须正确,
  否则要么越权(放大可见域)要么误锁(管理员被一起锁死)。
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.asyncio


async def test_rbac_models_roundtrip(session_factory) -> None:
    """模型可建表并往返:create_all 已建 RBAC 表,插入角色可查回。"""
    from app.models.role import Role

    async with session_factory() as s:
        s.add(
            Role(
                id="role-aaaaaa",
                name="测试角色",
                role_key="tester",
                sort=1,
                data_scope="self",
                status="0",
            )
        )
        await s.commit()

    from sqlalchemy import select

    async with session_factory() as s:
        row = (
            await s.scalars(select(Role).where(Role.role_key == "tester"))
        ).first()
        assert row is not None
        assert row.data_scope == "self"
        assert row.name == "测试角色"
```

- [ ] **Step 2: 运行,确认失败**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_rbac_kernel.py::test_rbac_models_roundtrip -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.models.role'`

- [ ] **Step 3: 写 `role.py`**

```python
"""角色 ORM 模型(RBAC:权限与数据范围的载体)。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Role(Base):
    """角色:role_key 为权限字符(如 admin);data_scope 决定数据范围。"""

    __tablename__ = "roles"

    # 主键形如 "role-" + 6 位 hex
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    # 权限字符(唯一),前端/后端按它识别超管等
    role_key: Mapped[str] = mapped_column(
        String, nullable=False, unique=True, index=True
    )
    sort: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # all | custom | dept | dept_and_child | self
    data_scope: Mapped[str] = mapped_column(
        String, nullable=False, default="self"
    )
    # "0" 正常 / "1" 停用
    status: Mapped[str] = mapped_column(String, nullable=False, default="0")
    remark: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )
```

- [ ] **Step 4: 写 `department.py`**

```python
"""部门 ORM 模型(RBAC 数据权限的组织维度,树结构)。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Department(Base):
    """部门:parent_id 构成树;ancestors 存逗号分隔祖先 id 路径(根='0'),

    供「本部门及子」按 ``ancestors like '%,<deptId>,%'`` 一次查出子树。
    """

    __tablename__ = "departments"

    # 主键形如 "dept-" + 6 位 hex
    id: Mapped[str] = mapped_column(String, primary_key=True)
    parent_id: Mapped[str | None] = mapped_column(String, nullable=True)
    # 祖先路径,形如 "0,dept-000000,";根部门为 "0"
    ancestors: Mapped[str] = mapped_column(
        String, nullable=False, default="0"
    )
    name: Mapped[str] = mapped_column(String, nullable=False)
    sort: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    leader: Mapped[str | None] = mapped_column(String, nullable=True)
    phone: Mapped[str | None] = mapped_column(String, nullable=True)
    email: Mapped[str | None] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="0")
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )
```

- [ ] **Step 5: 写 `menu.py`**

```python
"""菜单 ORM 模型(RBAC:菜单=路由 + 按钮权限,树结构)。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Menu(Base):
    """菜单项:menu_type M 目录 / C 菜单(路由)/ F 按钮(仅承载 perms)。

    C 用 path+component 生成动态路由;F 用 perms 串(如 system:user:add)做按钮门控。
    """

    __tablename__ = "menus"

    # 主键形如 "menu-" + 6 位 hex
    id: Mapped[str] = mapped_column(String, primary_key=True)
    parent_id: Mapped[str | None] = mapped_column(String, nullable=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    # M | C | F
    menu_type: Mapped[str] = mapped_column(String, nullable=False)
    path: Mapped[str | None] = mapped_column(String, nullable=True)
    component: Mapped[str | None] = mapped_column(String, nullable=True)
    # 权限标识(F/C 用),如 system:user:add
    perms: Mapped[str | None] = mapped_column(String, nullable=True)
    icon: Mapped[str | None] = mapped_column(String, nullable=True)
    sort: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # "0" 显示 / "1" 隐藏
    visible: Mapped[str] = mapped_column(String, nullable=False, default="0")
    status: Mapped[str] = mapped_column(String, nullable=False, default="0")
    is_frame: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    query: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )
```

- [ ] **Step 6: 写 `rbac_links.py`**

```python
"""RBAC 连接表:用户↔角色、角色↔菜单、角色↔部门(数据权限 custom)。

三张纯连接表变更同源,合于一文件。均复合主键、无额外列。
"""

from __future__ import annotations

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class UserRole(Base):
    __tablename__ = "user_roles"

    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    role_id: Mapped[str] = mapped_column(String, primary_key=True)


class RoleMenu(Base):
    __tablename__ = "role_menus"

    role_id: Mapped[str] = mapped_column(String, primary_key=True)
    menu_id: Mapped[str] = mapped_column(String, primary_key=True)


class RoleDept(Base):
    """仅 data_scope=custom 用:角色自定义可见部门集。"""

    __tablename__ = "role_depts"

    role_id: Mapped[str] = mapped_column(String, primary_key=True)
    dept_id: Mapped[str] = mapped_column(String, primary_key=True)
```

- [ ] **Step 7: 注册进 `models/__init__.py`**

在 import 区与 `__all__` 按字母序插入(保持现有风格):

```python
from app.models.department import Department
from app.models.menu import Menu
from app.models.rbac_links import RoleDept, RoleMenu, UserRole
from app.models.role import Role
```

并在 `__all__` 列表加入 `"Department"`, `"Menu"`, `"Role"`, `"RoleDept"`, `"RoleMenu"`, `"UserRole"`。

- [ ] **Step 8: 运行,确认通过**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_rbac_kernel.py::test_rbac_models_roundtrip -v`
Expected: PASS

- [ ] **Step 9: 提交**

```bash
git add backend/app/models/role.py backend/app/models/department.py backend/app/models/menu.py backend/app/models/rbac_links.py backend/app/models/__init__.py backend/tests/test_rbac_kernel.py
git commit -m "feat(rbac): RBAC 核心 ORM 模型(role/department/menu + 连接表)"
```

---

### Task 2: 用户与业务表归属列

**Files:**
- Modify: `backend/app/models/user.py`(+`dept_id`)、`dataset.py`/`datasource.py`/`ingest_task.py`/`job.py`/`upload.py`
- Test: `backend/tests/test_rbac_kernel.py`

**Interfaces:**
- Produces: `User.dept_id: str|None`;各业务模型新增 `dept_id: str|None`;`DataSource.creator`/`IngestTask.creator`/`UploadRecord.creator`(default `"admin"`)。归属列对照(供 P3 `apply_data_scope` 用):`Dataset→creator`、`Job→created_by`、`DataSource/IngestTask/UploadRecord→creator`。

- [ ] **Step 1: 写失败测试**(追加到 `test_rbac_kernel.py`)

```python
async def test_user_and_business_have_dept_id(session_factory) -> None:
    """User 与各业务模型都具备 dept_id 列(create_all 后可写读)。"""
    from sqlalchemy import select

    from app.models.dataset import Dataset
    from app.models.user import User

    async with session_factory() as s:
        s.add(
            User(
                id="usr-deptck",
                username="deptck",
                password_hash="x",
                role="user",
                dept_id="dept-000000",
            )
        )
        s.add(
            Dataset(id="dset-deptck", name="d", dept_id="dept-000000")
        )
        await s.commit()

    async with session_factory() as s:
        u = (
            await s.scalars(select(User).where(User.id == "usr-deptck"))
        ).first()
        d = (
            await s.scalars(select(Dataset).where(Dataset.id == "dset-deptck"))
        ).first()
        assert u.dept_id == "dept-000000"
        assert d.dept_id == "dept-000000"
```

- [ ] **Step 2: 运行,确认失败**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_rbac_kernel.py::test_user_and_business_have_dept_id -v`
Expected: FAIL — `TypeError: 'dept_id' is an invalid keyword argument for User`

- [ ] **Step 3: `user.py` 加列**

在 `created_at` 之前插入:

```python
    # 所属部门(RBAC 数据权限维度);存量/未分配为空
    dept_id: Mapped[str | None] = mapped_column(String, nullable=True)
```

- [ ] **Step 4: 五张业务模型加 `dept_id`**

对 `dataset.py`、`datasource.py`、`ingest_task.py`、`job.py`、`upload.py`,各在 `created_at` 前插入(注释按表微调):

```python
    # 所属部门(RBAC 数据权限快照,创建时取创建人部门);存量回填为根部门
    dept_id: Mapped[str | None] = mapped_column(String, nullable=True)
```

`String` 若文件未导入则在 import 处补 `from sqlalchemy import String`(多数已导入)。

- [ ] **Step 5: 三张缺归属的业务模型加 `creator`**

对 `datasource.py`、`ingest_task.py`、`upload.py`,在 `dept_id` 旁插入:

```python
    # 创建人(RBAC self 数据范围依据);存量回填 "admin"
    creator: Mapped[str] = mapped_column(
        String, nullable=False, default="admin", server_default="admin"
    )
```

（`dataset.py` 已有 `creator`、`job.py` 已有 `created_by`,不重复加。）

- [ ] **Step 6: 运行,确认通过**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_rbac_kernel.py::test_user_and_business_have_dept_id -v`
Expected: PASS

- [ ] **Step 7: 回归——确认未碰坏现有用例**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_rbac.py tests/test_datasources.py -q`
Expected: PASS(归属列均带 default,不破坏现有插入路径)

- [ ] **Step 8: 提交**

```bash
git add backend/app/models/user.py backend/app/models/dataset.py backend/app/models/datasource.py backend/app/models/ingest_task.py backend/app/models/job.py backend/app/models/upload.py backend/tests/test_rbac_kernel.py
git commit -m "feat(rbac): User + 业务表加 dept_id/creator 归属列(schema 就位,过滤逻辑留待 P3)"
```

---

### Task 3: Alembic 0019 迁移(建表 + 业务列 + 种子 + 回填)

**Files:**
- Create: `backend/alembic/versions/0019_rbac_system.py`

**Interfaces:**
- Produces:种子常量(后续测试与前端按它对齐)—— 根部门 `dept-000000`("AI 数据平台",ancestors `"0"`);角色 `role-000001`(超级管理员,`role_key=admin`,`data_scope=all`)、`role-000002`(普通用户,`role_key=common`,`data_scope=self`);系统管理菜单根 `menu-000001`("系统管理",M);五个 C 菜单 + 各 F 按钮(见下);`user_roles`:`usr-000001→role-000001`、`usr-000002→role-000002`;`role_menus`:超管授全部菜单。

- [ ] **Step 1: 写迁移**(无 pytest;DDL+DML 一体,验证在 Step 2 真实 upgrade)

```python
"""rbac system: roles/departments/menus + 连接表 + 业务归属列 + 种子/回填

Revision ID: 0019_rbac_system
Revises: 0018_dataset_tags
Create Date: 2026-06-25

完整动态 RBAC 地基(设计见 docs/superpowers/specs/2026-06-25-rbac-system-management-design.md)。
迁移自包含:不 import app 代码。种子 id 用固定值,保证幂等与可被测试/前端对齐。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0019_rbac_system"
down_revision: str | None = "0018_dataset_tags"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_ROOT_DEPT = "dept-000000"
_ROLE_ADMIN = "role-000001"
_ROLE_COMMON = "role-000002"

# 业务表名核对(已读模型):upload 表名为 upload_records;
# dataset/datasource 已有 creator、job 已有 created_by ⇒ 仅 ingest_tasks/upload_records 补 creator。
_BIZ_ADD_DEPT = ["datasets", "datasources", "ingest_tasks", "jobs", "upload_records"]
_BIZ_ADD_CREATOR = ["ingest_tasks", "upload_records"]


def upgrade() -> None:
    # ---- RBAC 主表 ----
    op.create_table(
        "roles",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("role_key", sa.String(), nullable=False),
        sa.Column("sort", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("data_scope", sa.String(), nullable=False, server_default="self"),
        sa.Column("status", sa.String(), nullable=False, server_default="0"),
        sa.Column("remark", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("role_key"),
    )
    op.create_index("ix_roles_role_key", "roles", ["role_key"], unique=True)

    op.create_table(
        "departments",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("parent_id", sa.String(), nullable=True),
        sa.Column("ancestors", sa.String(), nullable=False, server_default="0"),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("sort", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("leader", sa.String(), nullable=True),
        sa.Column("phone", sa.String(), nullable=True),
        sa.Column("email", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "menus",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("parent_id", sa.String(), nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("menu_type", sa.String(), nullable=False),
        sa.Column("path", sa.String(), nullable=True),
        sa.Column("component", sa.String(), nullable=True),
        sa.Column("perms", sa.String(), nullable=True),
        sa.Column("icon", sa.String(), nullable=True),
        sa.Column("sort", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("visible", sa.String(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(), nullable=False, server_default="0"),
        sa.Column("is_frame", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("query", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )

    for tbl, a, b in [
        ("user_roles", "user_id", "role_id"),
        ("role_menus", "role_id", "menu_id"),
        ("role_depts", "role_id", "dept_id"),
    ]:
        op.create_table(
            tbl,
            sa.Column(a, sa.String(), nullable=False),
            sa.Column(b, sa.String(), nullable=False),
            sa.PrimaryKeyConstraint(a, b),
        )

    # ---- users 与业务表归属列 ----
    op.add_column("users", sa.Column("dept_id", sa.String(), nullable=True))
    for tbl in _BIZ_ADD_DEPT:
        op.add_column(tbl, sa.Column("dept_id", sa.String(), nullable=True))
    for tbl in _BIZ_ADD_CREATOR:
        op.add_column(
            tbl,
            sa.Column("creator", sa.String(), nullable=False, server_default="admin"),
        )

    # ---- 种子 ----
    op.bulk_insert(
        sa.table(
            "departments",
            sa.column("id", sa.String), sa.column("parent_id", sa.String),
            sa.column("ancestors", sa.String), sa.column("name", sa.String),
            sa.column("sort", sa.Integer), sa.column("status", sa.String),
        ),
        [{"id": _ROOT_DEPT, "parent_id": None, "ancestors": "0",
          "name": "AI 数据平台", "sort": 0, "status": "0"}],
    )
    op.bulk_insert(
        sa.table(
            "roles",
            sa.column("id", sa.String), sa.column("name", sa.String),
            sa.column("role_key", sa.String), sa.column("sort", sa.Integer),
            sa.column("data_scope", sa.String), sa.column("status", sa.String),
        ),
        [
            {"id": _ROLE_ADMIN, "name": "超级管理员", "role_key": "admin",
             "sort": 1, "data_scope": "all", "status": "0"},
            {"id": _ROLE_COMMON, "name": "普通用户", "role_key": "common",
             "sort": 2, "data_scope": "self", "status": "0"},
        ],
    )

    menus = _seed_menu_rows()
    op.bulk_insert(
        sa.table(
            "menus",
            sa.column("id", sa.String), sa.column("parent_id", sa.String),
            sa.column("name", sa.String), sa.column("menu_type", sa.String),
            sa.column("path", sa.String), sa.column("component", sa.String),
            sa.column("perms", sa.String), sa.column("icon", sa.String),
            sa.column("sort", sa.Integer), sa.column("visible", sa.String),
            sa.column("status", sa.String),
        ),
        menus,
    )

    # 用户→角色;超管→全部菜单
    op.bulk_insert(
        sa.table("user_roles", sa.column("user_id", sa.String), sa.column("role_id", sa.String)),
        [{"user_id": "usr-000001", "role_id": _ROLE_ADMIN},
         {"user_id": "usr-000002", "role_id": _ROLE_COMMON}],
    )
    op.bulk_insert(
        sa.table("role_menus", sa.column("role_id", sa.String), sa.column("menu_id", sa.String)),
        [{"role_id": _ROLE_ADMIN, "menu_id": m["id"]} for m in menus],
    )

    # ---- 回填存量行:dept_id=根部门;缺失 creator/owner 置 admin ----
    for tbl in _BIZ_ADD_DEPT:
        op.execute(f"UPDATE {tbl} SET dept_id = '{_ROOT_DEPT}' WHERE dept_id IS NULL")
    op.execute(f"UPDATE users SET dept_id = '{_ROOT_DEPT}' WHERE dept_id IS NULL")


def _seed_menu_rows() -> list[dict]:
    """系统管理菜单树:1 目录(M)+5 菜单(C)+各按钮(F)。component 指向前端 P5 组件。"""
    root = {"id": "menu-000001", "parent_id": None, "name": "系统管理",
            "menu_type": "M", "path": "/system", "component": None, "perms": None,
            "icon": "setting", "sort": 90, "visible": "0", "status": "0"}
    pages = [
        ("menu-000010", "用户管理", "/system/user", "system/user", "system:user", "user"),
        ("menu-000020", "角色管理", "/system/role", "system/role", "system:role", "team"),
        ("menu-000030", "部门管理", "/system/dept", "system/dept", "system:dept", "cluster"),
        ("menu-000040", "菜单管理", "/system/menu", "system/menu", "system:menu", "menu"),
        ("menu-000050", "权限管理", "/system/permission", "system/permission", "system:perm", "safety"),
    ]
    rows = [root]
    for i, (mid, name, path, comp, base, icon) in enumerate(pages, start=1):
        rows.append({"id": mid, "parent_id": "menu-000001", "name": name,
                     "menu_type": "C", "path": path, "component": comp, "perms": None,
                     "icon": icon, "sort": i, "visible": "0", "status": "0"})
        # 每页四个标准按钮(列表/新增/编辑/删除)
        for j, (act, label) in enumerate(
            [("list", "查询"), ("add", "新增"), ("edit", "修改"), ("remove", "删除")], start=1
        ):
            rows.append({"id": f"{mid}{j}", "parent_id": mid, "name": f"{name}{label}",
                         "menu_type": "F", "path": None, "component": None,
                         "perms": f"{base}:{act}", "icon": None, "sort": j,
                         "visible": "0", "status": "0"})
    return rows


def downgrade() -> None:
    for tbl in _BIZ_ADD_CREATOR:
        op.drop_column(tbl, "creator")
    for tbl in _BIZ_ADD_DEPT:
        op.drop_column(tbl, "dept_id")
    op.drop_column("users", "dept_id")
    for tbl in ["role_depts", "role_menus", "user_roles", "menus", "departments"]:
        op.drop_table(tbl)
    op.drop_index("ix_roles_role_key", table_name="roles")
    op.drop_table("roles")
```

- [ ] **Step 2: 真实库验证 upgrade**(测试库走 create_all,迁移须对开发库验证)

Run:
```bash
cd backend && ./.venv/Scripts/alembic upgrade head
```
Expected: 无报错,末行 `Running upgrade 0018_dataset_tags -> 0019_rbac_system`。

- [ ] **Step 3: 校验种子与回填**

Run:
```bash
cd backend && ./.venv/Scripts/python.exe -c "import asyncio; from sqlalchemy import text; from app.core.db import engine; \
import anyio; \
print(anyio.run(lambda: None))" 2>NUL || true
```
改用 psql 直查(开发库 DSN 见 `backend/.env`):
```bash
cd backend && ./.venv/Scripts/python.exe - <<'PY'
import asyncio
from sqlalchemy import text
from app.core.db import async_session_factory

async def main():
    async with async_session_factory() as s:
        roles = (await s.execute(text("select role_key,data_scope from roles order by sort"))).all()
        menus = (await s.execute(text("select count(*) from menus"))).scalar()
        grant = (await s.execute(text("select count(*) from role_menus where role_id='role-000001'"))).scalar()
        ur = (await s.execute(text("select role_id from user_roles where user_id='usr-000001'"))).scalar()
        backfill = (await s.execute(text("select count(*) from datasets where dept_id is null"))).scalar()
        print("roles", roles); print("menus", menus, "superadmin_grant", grant)
        print("admin_role", ur, "datasets_null_dept", backfill)
        assert ("admin","all") in [(r[0],r[1]) for r in roles]
        assert menus == grant and menus >= 26  # 1 + 5 + 5*4
        assert ur == "role-000001"
        assert backfill == 0
        print("OK")
asyncio.run(main())
PY
```
Expected: 末行 `OK`(超管授全部菜单、admin 映射超管、datasets 无空 dept)。

- [ ] **Step 4: 提交**

```bash
git add backend/alembic/versions/0019_rbac_system.py
git commit -m "feat(rbac): alembic 0019 建 RBAC 表+业务归属列+种子(超管/根部门/系统菜单)+回填"
```

---

### Task 4: `seed_rbac` fixture + 角色/权限聚合

**Files:**
- Modify: `backend/tests/conftest.py`(+`seed_rbac`)
- Create: `backend/app/services/rbac.py`
- Test: `backend/tests/test_rbac_kernel.py`

**Interfaces:**
- Produces:`rbac.has_perm(perms: set[str], code: str) -> bool`;`async rbac.get_user_roles(session, user: User) -> list[Role]`;`async rbac.get_user_perms(session, user: User) -> set[str]`(`user.role=="admin"` ⇒ `{"*:*:*"}`)。
- Consumes:`User`(`app.models.user`)、`Role`、`UserRole`、`RoleMenu`、`Menu`。
- `seed_rbac` fixture 造数(供本任务及 Task 5/6/8):部门 `d-root`(ancestors `"0"`)、`d-a`(子,ancestors `"0,d-root,"`)、`d-b`(子);角色 `r-all`(all)、`r-dc`(dept_and_child)、`r-self`(self)、`r-custom`(custom,role_depts→`d-a`);菜单 `m-sys`(M)、`m-user`(C,component `system/user`)、`m-add`(F,perms `system:user:add`,父 `m-user`);授权 `r-dc→{m-sys,m-user,m-add}`;用户 `u-super`(role 列 `admin`)、`u-mgr`(role 列 `user`,dept `d-root`,角色 `r-dc`)、`u-staff`(role 列 `user`,dept `d-a`,角色 `r-self`)。

- [ ] **Step 1: 加 `seed_rbac` fixture**(`conftest.py` 末尾追加)

```python
@pytest_asyncio.fixture
async def seed_rbac(session_factory) -> None:
    """RBAC 造数:部门树 + 四种 data_scope 角色 + 系统菜单 + 三个测试用户。

    测试库走 create_all(无迁移种子),鉴权内核用例靠本 fixture 构造确定数据。
    """
    from app.models.department import Department
    from app.models.menu import Menu
    from app.models.rbac_links import RoleDept, RoleMenu, UserRole
    from app.models.role import Role
    from app.models.user import User

    async with session_factory() as s:
        s.add_all([
            Department(id="d-root", parent_id=None, ancestors="0", name="根", status="0"),
            Department(id="d-a", parent_id="d-root", ancestors="0,d-root,", name="甲", status="0"),
            Department(id="d-b", parent_id="d-root", ancestors="0,d-root,", name="乙", status="0"),
            Role(id="r-all", name="全部", role_key="r_all", data_scope="all", status="0"),
            Role(id="r-dc", name="部门及子", role_key="r_dc", data_scope="dept_and_child", status="0"),
            Role(id="r-self", name="仅本人", role_key="r_self", data_scope="self", status="0"),
            Role(id="r-custom", name="自定义", role_key="r_custom", data_scope="custom", status="0"),
            Menu(id="m-sys", parent_id=None, name="系统管理", menu_type="M",
                 path="/system", icon="setting", sort=90, visible="0", status="0"),
            Menu(id="m-user", parent_id="m-sys", name="用户管理", menu_type="C",
                 path="/system/user", component="system/user", sort=1, visible="0", status="0"),
            Menu(id="m-add", parent_id="m-user", name="用户新增", menu_type="F",
                 perms="system:user:add", sort=1, visible="0", status="0"),
            RoleDept(role_id="r-custom", dept_id="d-a"),
            RoleMenu(role_id="r-dc", menu_id="m-sys"),
            RoleMenu(role_id="r-dc", menu_id="m-user"),
            RoleMenu(role_id="r-dc", menu_id="m-add"),
            User(id="u-super", username="u-super", password_hash="x", role="admin", dept_id="d-root"),
            User(id="u-mgr", username="u-mgr", password_hash="x", role="user", dept_id="d-root"),
            User(id="u-staff", username="u-staff", password_hash="x", role="user", dept_id="d-a"),
            UserRole(user_id="u-mgr", role_id="r-dc"),
            UserRole(user_id="u-staff", role_id="r-self"),
        ])
        await s.commit()
```

- [ ] **Step 2: 写失败测试**(追加到 `test_rbac_kernel.py`)

```python
async def test_get_user_perms_admin_is_wildcard(session_factory, seed_rbac) -> None:
    """role 列为 admin 的用户 ⇒ 通配权限(桥接遗留超管)。"""
    from sqlalchemy import select

    from app.models.user import User
    from app.services import rbac

    async with session_factory() as s:
        u = (await s.scalars(select(User).where(User.id == "u-super"))).first()
        perms = await rbac.get_user_perms(s, u)
        assert perms == {"*:*:*"}
        assert rbac.has_perm(perms, "system:user:add") is True


async def test_get_user_perms_aggregates_granted_menus(session_factory, seed_rbac) -> None:
    """u-mgr(角色 r-dc 授 m-add)聚合得 system:user:add;无关 perm 为假。"""
    from sqlalchemy import select

    from app.models.user import User
    from app.services import rbac

    async with session_factory() as s:
        u = (await s.scalars(select(User).where(User.id == "u-mgr"))).first()
        perms = await rbac.get_user_perms(s, u)
        assert "system:user:add" in perms
        assert rbac.has_perm(perms, "system:role:remove") is False
```

- [ ] **Step 3: 运行,确认失败**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_rbac_kernel.py -k get_user_perms -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'app.services.rbac'`

- [ ] **Step 4: 写 `services/rbac.py`(本任务部分)**

```python
"""RBAC 鉴权内核:角色/权限聚合、数据范围解析、菜单树、数据权限过滤。

纯计算 + DB 只读查询,不写库。会话由调用方注入,便于单测。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.menu import Menu
from app.models.rbac_links import RoleMenu, UserRole
from app.models.role import Role
from app.models.user import User

# 超管权限通配:匹配任意 code
WILDCARD = "*:*:*"


def has_perm(perms: set[str], code: str) -> bool:
    """权限判定:持通配或精确命中即放行。"""
    return WILDCARD in perms or code in perms


async def get_user_roles(session: AsyncSession, user: User) -> list[Role]:
    """用户的角色列表(经 user_roles)。无则空列表。"""
    stmt = (
        select(Role)
        .join(UserRole, UserRole.role_id == Role.id)
        .where(UserRole.user_id == user.id)
    )
    return list((await session.scalars(stmt)).all())


async def get_user_perms(session: AsyncSession, user: User) -> set[str]:
    """聚合用户权限码。遗留 admin(user.role=='admin')⇒ 通配。

    否则:经 user_roles→role_menus→menus.perms 收集非空 perms。
    """
    if user.role == "admin":
        return {WILDCARD}
    stmt = (
        select(Menu.perms)
        .join(RoleMenu, RoleMenu.menu_id == Menu.id)
        .join(UserRole, UserRole.role_id == RoleMenu.role_id)
        .where(UserRole.user_id == user.id, Menu.perms.is_not(None))
    )
    return {p for p in (await session.scalars(stmt)).all() if p}
```

- [ ] **Step 5: 运行,确认通过**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_rbac_kernel.py -k get_user_perms -v`
Expected: PASS(2 passed)

- [ ] **Step 6: 提交**

```bash
git add backend/tests/conftest.py backend/app/services/rbac.py backend/tests/test_rbac_kernel.py
git commit -m "feat(rbac): rbac 服务-角色/权限聚合 + seed_rbac 测试 fixture"
```

---

### Task 5: 数据范围解析 + `apply_data_scope`

**Files:**
- Modify: `backend/app/services/rbac.py`
- Test: `backend/tests/test_rbac_kernel.py`

**Interfaces:**
- Produces:`@dataclass ScopeContext(scope: str, dept_ids: set[str], user_id: str, user_dept_id: str | None)`;`async rbac.get_effective_data_scope(session, user) -> ScopeContext`(多角色取最宽:`all>dept_and_child>custom>dept>self`;`dept_and_child` 用 ancestors 解析子树并入 `dept_ids`;`custom` 取 role_depts 并入 `dept_ids`);`rbac.apply_data_scope(stmt, model, creator_attr: str, ctx: ScopeContext) -> Select`。
- Consumes:`Department`、`RoleDept`、`get_user_roles`。

- [ ] **Step 1: 写失败测试**(追加)

```python
async def test_effective_scope_widest_and_subtree(session_factory, seed_rbac) -> None:
    """u-mgr 角色 r-dc=dept_and_child,dept=d-root ⇒ dept_ids 含 d-root 及子 d-a/d-b。"""
    from sqlalchemy import select

    from app.models.user import User
    from app.services import rbac

    async with session_factory() as s:
        u = (await s.scalars(select(User).where(User.id == "u-mgr"))).first()
        ctx = await rbac.get_effective_data_scope(s, u)
        assert ctx.scope == "dept_and_child"
        assert {"d-root", "d-a", "d-b"} <= ctx.dept_ids


async def test_apply_data_scope_self_excludes_others(session_factory, seed_rbac) -> None:
    """self 范围:apply_data_scope 后只查得本人 creator 的数据集(越权红线)。"""
    from sqlalchemy import select

    from app.models.dataset import Dataset
    from app.models.user import User
    from app.services import rbac

    async with session_factory() as s:
        s.add_all([
            Dataset(id="dset-mine", name="mine", creator="u-staff", dept_id="d-a"),
            Dataset(id="dset-other", name="other", creator="u-mgr", dept_id="d-root"),
        ])
        await s.commit()
        u = (await s.scalars(select(User).where(User.id == "u-staff"))).first()
        ctx = await rbac.get_effective_data_scope(s, u)
        stmt = rbac.apply_data_scope(select(Dataset), Dataset, "creator", ctx)
        ids = {d.id for d in (await s.scalars(stmt)).all()}
        assert ids == {"dset-mine"}  # 绝不含 dset-other
```

- [ ] **Step 2: 运行,确认失败**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_rbac_kernel.py -k scope -v`
Expected: FAIL — `AttributeError: module 'app.services.rbac' has no attribute 'get_effective_data_scope'`

- [ ] **Step 3: 扩 `rbac.py`**

文件顶部 import 区补:

```python
from dataclasses import dataclass, field

from app.models.department import Department
from app.models.rbac_links import RoleDept
```

追加:

```python
# 数据范围由宽到窄;取用户多角色中最宽者
_SCOPE_ORDER = ["all", "dept_and_child", "custom", "dept", "self"]


@dataclass
class ScopeContext:
    """生效数据范围:scope + 预解析的可见部门集(dept_and_child/custom 用)。"""

    scope: str
    dept_ids: set[str] = field(default_factory=set)
    user_id: str = ""
    user_dept_id: str | None = None


async def _subtree_dept_ids(session: AsyncSession, dept_id: str) -> set[str]:
    """部门子树(含自身):自身 + ancestors 命中 '...,<dept_id>,' 的后代。"""
    if not dept_id:
        return set()
    like = f"%,{dept_id},%"
    stmt = select(Department.id).where(
        (Department.id == dept_id) | (Department.ancestors.like(like))
    )
    return set((await session.scalars(stmt)).all())


async def get_effective_data_scope(
    session: AsyncSession, user: User
) -> ScopeContext:
    """解析用户生效数据范围。admin/无角色 的处理见下。"""
    if user.role == "admin":
        return ScopeContext("all", user_id=user.id, user_dept_id=user.dept_id)
    roles = await get_user_roles(session, user)
    scopes = {r.data_scope for r in roles}
    # 无角色 ⇒ 退化为 self(只能看自己),绝不放成 all
    scope = next((s for s in _SCOPE_ORDER if s in scopes), "self")
    ctx = ScopeContext(scope, user_id=user.id, user_dept_id=user.dept_id)
    if scope == "dept_and_child" and user.dept_id:
        ctx.dept_ids = await _subtree_dept_ids(session, user.dept_id)
    elif scope == "custom":
        role_ids = [r.id for r in roles if r.data_scope == "custom"]
        if role_ids:
            stmt = select(RoleDept.dept_id).where(RoleDept.role_id.in_(role_ids))
            ctx.dept_ids = set((await session.scalars(stmt)).all())
    return ctx


def apply_data_scope(stmt, model, creator_attr: str, ctx: ScopeContext):
    """按生效范围给 SELECT 注入 WHERE。creator_attr 为该模型的归属列名。

    all 不过滤;self 比 creator==本人;dept 比 dept_id==本人部门;
    dept_and_child/custom 比 dept_id ∈ 预解析集(空集 ⇒ 匹配不到,安全失败)。
    """
    if ctx.scope == "all":
        return stmt
    if ctx.scope == "self":
        return stmt.where(getattr(model, creator_attr) == ctx.user_id)
    if ctx.scope == "dept":
        return stmt.where(model.dept_id == ctx.user_dept_id)
    # dept_and_child | custom
    if not ctx.dept_ids:
        return stmt.where(model.dept_id.in_(["__none__"]))
    return stmt.where(model.dept_id.in_(ctx.dept_ids))
```

- [ ] **Step 4: 运行,确认通过**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_rbac_kernel.py -k scope -v`
Expected: PASS(2 passed)

- [ ] **Step 5: 提交**

```bash
git add backend/app/services/rbac.py backend/tests/test_rbac_kernel.py
git commit -m "feat(rbac): 数据范围解析(最宽/子树/custom)+ apply_data_scope 过滤助手"
```

---

### Task 6: `build_router_tree`(菜单树下发)

**Files:**
- Modify: `backend/app/services/rbac.py`
- Test: `backend/tests/test_rbac_kernel.py`

**Interfaces:**
- Produces:`async rbac.build_router_tree(session, user) -> list[dict]`。返回 M/C 菜单(去 F)的树,按 sort;每节点 `{"id","name","path","component","icon","children":[...]}`。admin ⇒ 全部 M/C;否则仅 user_roles→role_menus 授予的 M/C。

- [ ] **Step 1: 写失败测试**(追加)

```python
async def test_build_router_tree_filters_buttons_and_by_role(
    session_factory, seed_rbac
) -> None:
    """u-mgr 授 m-sys/m-user(+按钮 m-add):路由树含 system 目录及 user 子项,

    但**不含** F 按钮 m-add(按钮不进路由)。
    """
    from sqlalchemy import select

    from app.models.user import User
    from app.services import rbac

    async with session_factory() as s:
        u = (await s.scalars(select(User).where(User.id == "u-mgr"))).first()
        tree = await rbac.build_router_tree(s, u)
        assert len(tree) == 1 and tree[0]["path"] == "/system"
        children = tree[0]["children"]
        assert [c["path"] for c in children] == ["/system/user"]
        assert children[0]["component"] == "system/user"
        # 按钮不出现在任何层级
        flat = [tree[0], *children, *[g for c in children for g in c["children"]]]
        assert all(n["path"] is not None for n in flat)


async def test_build_router_tree_admin_sees_all(session_factory, seed_rbac) -> None:
    """admin(u-super)无 role_menus 授权也应看到全部 M/C 菜单。"""
    from sqlalchemy import select

    from app.models.user import User
    from app.services import rbac

    async with session_factory() as s:
        u = (await s.scalars(select(User).where(User.id == "u-super"))).first()
        tree = await rbac.build_router_tree(s, u)
        assert tree and tree[0]["path"] == "/system"
        assert [c["path"] for c in tree[0]["children"]] == ["/system/user"]
```

- [ ] **Step 2: 运行,确认失败**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_rbac_kernel.py -k router_tree -v`
Expected: FAIL — `AttributeError: ... 'build_router_tree'`

- [ ] **Step 3: 扩 `rbac.py`**

```python
def _build_tree(rows: list[Menu], parent: str | None) -> list[dict]:
    """把扁平菜单按 parent_id 组装成树(已按 sort 排序)。"""
    out = []
    for m in rows:
        if m.parent_id == parent:
            out.append({
                "id": m.id, "name": m.name, "path": m.path,
                "component": m.component, "icon": m.icon,
                "children": _build_tree(rows, m.id),
            })
    return out


async def build_router_tree(session: AsyncSession, user: User) -> list[dict]:
    """当前用户可见的 M/C 菜单树(去 F 按钮)。admin 全量,否则按授权。"""
    base = (
        select(Menu)
        .where(Menu.menu_type.in_(["M", "C"]), Menu.status == "0")
        .order_by(Menu.sort)
    )
    if user.role == "admin":
        rows = list((await session.scalars(base)).all())
    else:
        stmt = (
            base.join(RoleMenu, RoleMenu.menu_id == Menu.id)
            .join(UserRole, UserRole.role_id == RoleMenu.role_id)
            .where(UserRole.user_id == user.id)
            .distinct()
        )
        rows = list((await session.scalars(stmt)).all())
    return _build_tree(rows, None)
```

- [ ] **Step 4: 运行,确认通过**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_rbac_kernel.py -k router_tree -v`
Expected: PASS(2 passed)

- [ ] **Step 5: 全量内核回归**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_rbac_kernel.py -v`
Expected: PASS(全部)

- [ ] **Step 6: 提交**

```bash
git add backend/app/services/rbac.py backend/tests/test_rbac_kernel.py
git commit -m "feat(rbac): build_router_tree 按角色裁剪下发 M/C 菜单树"
```

---

### Task 7: `require_perm` 依赖工厂

**Files:**
- Modify: `backend/app/api/deps.py`(仅追加)
- Test: `backend/tests/test_rbac_kernel.py`

**Interfaces:**
- Produces:`deps.require_perm(code: str) -> Callable`,产出 FastAPI 依赖:未登录 401;无 perm 403 `{"success":False,"message":"无权限"}`;通过返回 `User`。
- Consumes:`current_user`(现有)、`rbac.get_user_perms`/`has_perm`。

- [ ] **Step 1: 写失败测试**(追加;用临时挂载路由验证依赖行为)

```python
async def test_require_perm_gates(session_factory, seed_rbac) -> None:
    """require_perm:无 perm→403、有 perm/超管→200、匿名→401。"""
    from httpx import ASGITransport, AsyncClient
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.api.deps import require_perm
    from app.core.db import get_session
    from app.main import app
    from app.services.auth import sign_token

    # 临时路由:要求 system:user:add
    @app.get("/__test__/need-perm")
    async def _need_perm(user=__import__("fastapi").Depends(require_perm("system:user:add"))):
        return {"ok": user.id}

    async def _ov():
        async with session_factory() as s:
            yield s

    app.dependency_overrides[get_session] = _ov
    transport = ASGITransport(app=app)
    try:
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # u-super(admin 通配)→200
            ac.cookies.set("adp_session", sign_token("u-super"))
            assert (await ac.get("/__test__/need-perm")).status_code == 200
            # u-staff(self 角色,无该 perm)→403
            ac.cookies.set("adp_session", sign_token("u-staff"))
            r = await ac.get("/__test__/need-perm")
            assert r.status_code == 403
            assert r.json()["detail"]["message"] == "无权限"
            # 匿名→401
            ac.cookies.delete("adp_session")
            assert (await ac.get("/__test__/need-perm")).status_code == 401
    finally:
        app.dependency_overrides.clear()
```

> 注:`sign_token("u-super")` 用 username;seed_rbac 里 `u-super` 的 username 即 `"u-super"`,故令牌可解析回该用户。

- [ ] **Step 2: 运行,确认失败**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_rbac_kernel.py -k require_perm -v`
Expected: FAIL — `ImportError: cannot import name 'require_perm'`

- [ ] **Step 3: 在 `deps.py` 追加**(现有 import 已含 `Annotated/Depends/HTTPException`;补 `from collections.abc import Callable` 与 rbac import)

```python
from collections.abc import Callable

from app.services import rbac


def require_perm(code: str) -> Callable:
    """权限门控工厂:要求当前用户持 code(或通配)。未登录 401 / 无权 403。

    与 require_admin 并存:管理类细粒度写端点(P2+)用本依赖。
    """

    async def _dep(
        session: SessionDep,
        user: Annotated[User | None, Depends(current_user)],
    ) -> User:
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
```

- [ ] **Step 4: 运行,确认通过**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_rbac_kernel.py -k require_perm -v`
Expected: PASS

- [ ] **Step 5: 回归——既有门控未受影响**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_rbac.py -q`
Expected: PASS(`require_admin` 行为不变)

- [ ] **Step 6: 提交**

```bash
git add backend/app/api/deps.py backend/tests/test_rbac_kernel.py
git commit -m "feat(rbac): require_perm 细粒度权限依赖(与 require_admin 并存)"
```

---

### Task 8: `getRouters` 端点 + currentUser 扩展

**Files:**
- Create: `backend/app/api/v1/system/__init__.py`, `backend/app/api/v1/system/menus.py`
- Modify: `backend/app/main.py`, `backend/app/api/compat.py`
- Test: `backend/tests/test_system_routers.py`

**Interfaces:**
- Produces:`GET /api/v1/system/menus/routers` → `{"success": True, "data": <router tree>}`(登录即可,内容按角色裁剪)。`/api/currentUser` 的 `data` 增 `roles: list[str]`(role_key)、`permissions: list[str]`(admin ⇒ `["*:*:*"]`)。
- Consumes:`require_user`(现有)、`rbac.build_router_tree`/`get_user_roles`/`get_user_perms`。

- [ ] **Step 1: 写失败测试**(`backend/tests/test_system_routers.py`)

```python
"""系统域:getRouters + currentUser 扩展(API 层)。"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_get_routers_requires_login(client: AsyncClient) -> None:
    """匿名取 routers → 401。"""
    resp = await client.get("/api/v1/system/menus/routers")
    assert resp.status_code == 401


async def test_get_routers_scoped_by_role(client: AsyncClient, seed_rbac) -> None:
    """u-mgr 登录态取 routers → 含 /system 目录及 /system/user 子项。"""
    from app.services.auth import sign_token

    client.cookies.set("adp_session", sign_token("u-mgr"))
    resp = await client.get("/api/v1/system/menus/routers")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data[0]["path"] == "/system"
    assert data[0]["children"][0]["path"] == "/system/user"


async def test_current_user_carries_roles_and_perms(
    client: AsyncClient, seed_users
) -> None:
    """admin 登录后 currentUser 带 permissions=['*:*:*']、access=admin(回归不破)。"""
    await client.post(
        "/api/login/account",
        json={"username": "admin", "password": "ant.design", "type": "account"},
    )
    me = await client.get("/api/currentUser")
    body = me.json()["data"]
    assert body["access"] == "admin"
    assert body["permissions"] == ["*:*:*"]
    assert "admin" in body["roles"] or body["roles"] == []  # 见下注
```

> 注:`seed_users` 不建 user_roles,故 admin 的 `roles` 经 user_roles 查为空——但 `permissions` 因 `role=='admin'` 仍为通配。`roles` 断言用宽松式(空或含 admin),两种 seed 都过。

- [ ] **Step 2: 运行,确认失败**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_system_routers.py -v`
Expected: FAIL — 404(端点未注册)/ KeyError `permissions`

- [ ] **Step 3: 建 `system/__init__.py`**

```python
"""系统管理域 API(RBAC)。P1 仅 menus(getRouters);P2 扩 users/roles/depts/menus CRUD。"""
```

- [ ] **Step 4: 建 `system/menus.py`**

```python
"""系统-菜单:getRouters(下发当前用户菜单树供前端动态路由)。"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from app.api.deps import SessionDep, require_user
from app.models.user import User
from app.services import rbac

router = APIRouter(prefix="/system/menus", tags=["system-menus"])


@router.get("/routers")
async def get_routers(
    session: SessionDep,
    user: Annotated[User, Depends(require_user)],
) -> dict:
    """当前用户可见菜单树(M/C),供前端 patchClientRoutes 生成动态路由。"""
    return {"success": True, "data": await rbac.build_router_tree(session, user)}
```

- [ ] **Step 5: 在 `main.py` 注册**

找到现有 v1 router 注册区(与 `datasets`/`tags` 等同处),按相同风格加入:

```python
from app.api.v1.system import menus as system_menus

app.include_router(system_menus.router, prefix="/api/v1")
```

> 实施者注:核对 `main.py` 既有 include 写法(前缀可能由统一 `api_router` 聚合),与之保持一致;若用聚合 router,则把 `system_menus.router` 并入该聚合。

- [ ] **Step 6: 扩 `compat.py` 的 currentUser**

`_current_user_payload` 仍同步返回基础体;在 `current_user` 端点内,查到 user 后注入 roles/permissions:

```python
    payload = _current_user_payload(user)
    from app.services import rbac

    payload["roles"] = [r.role_key for r in await rbac.get_user_roles(session, user)]
    payload["permissions"] = sorted(await rbac.get_user_perms(session, user))
    return JSONResponse({"success": True, "data": payload})
```

(替换原先直接 `return JSONResponse({"success": True, "data": _current_user_payload(user)})` 一行。)

- [ ] **Step 7: 运行,确认通过**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_system_routers.py -v`
Expected: PASS(3 passed)

- [ ] **Step 8: 全量回归**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_rbac.py tests/test_rbac_kernel.py tests/test_system_routers.py tests/test_compat_auth.py -q`
Expected: PASS(含既有越权防护红线 + 新内核 + 新端点)

- [ ] **Step 9: 提交**

```bash
git add backend/app/api/v1/system/ backend/app/main.py backend/app/api/compat.py backend/tests/test_system_routers.py
git commit -m "feat(rbac): getRouters 端点 + currentUser 下发 roles/permissions"
```

---

## Self-Review(对照 spec 检查)

**Spec 覆盖:**
- §2 数据模型(5+2 表 + users.dept_id + 业务 dept_id/creator):Task 1/2/3 ✓
- §2 种子(超管/普通/根部门/系统菜单树/用户映射/超管授全菜单/回填):Task 3 ✓
- §3 鉴权内核(get_user_perms / data_scope / 子树 / apply_data_scope / build_router_tree / require_perm / getRouters):Task 4–8 ✓
- §3 currentUser 扩 roles/permissions:Task 8 ✓
- §6 决策(保留 users.role + require_admin 不动):Task 7 仅追加 require_perm;Task 8 currentUser 保留 access=role;回归测试 `test_rbac.py` 每个写端点任务后跑 ✓
- **本计划范围外(后续 plan)**:P2 五模块 CRUD、P3 把 apply_data_scope 接进业务读/写端点、P4/P5 前端。`apply_data_scope` 与 `dept_id`/`creator` 列已在此就位,P3 仅需接线。

**占位扫描:** 无 TBD/TODO;每步含可运行代码与命令。Step(Task3/Step5 注册区、Task8/Step5 main.py 注册)给了"核对既有写法"提示——因 `main.py` 聚合方式需就地确认,非占位,附了判断依据。

**类型一致性:** `ScopeContext`(Task5)字段在 `apply_data_scope`(Task5)、Task8 一致;`get_user_perms`/`has_perm`/`get_user_roles`/`build_router_tree` 签名在 Task4/6/7/8 引用一致;归属列名 `creator`(dataset/datasource/ingest_task/upload)与 `created_by`(job)在 Interfaces 标注,P3 接线按此。

## 执行说明

每个 Task 自带 TDD 循环与提交,可被 fresh subagent 独立执行+评审。建议执行顺序即 Task 1→8(强依赖:模型→迁移→内核→端点)。完成后另起 plan 覆盖 P2/P3 与前端 P4/P5。
