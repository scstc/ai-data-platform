"""rbac system: roles/departments/menus + 连接表 + 业务归属列 + 种子/回填

Revision ID: 0019_rbac_system
Revises: 0018_dataset_tags
Create Date: 2026-06-25

完整动态 RBAC 地基(设计见 docs/superpowers/specs/2026-06-25-rbac-system-management-design.md)。
迁移自包含:不 import app 代码。种子 id 用固定值,保证幂等与可被测试/前端对齐。

表名核对:upload 表为 upload_records;datasource/dataset 已有 creator、job 已有
created_by ⇒ 仅 ingest_tasks/upload_records 需补 creator。
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

    # ---- 回填存量行:dept_id=根部门(creator 由列 server_default 落 admin)----
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
