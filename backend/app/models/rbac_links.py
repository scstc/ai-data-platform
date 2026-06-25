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
