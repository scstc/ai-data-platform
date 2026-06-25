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
