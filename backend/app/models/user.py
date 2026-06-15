"""用户 ORM 模型(RBAC 主体)。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import Boolean, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class User(Base):
    """平台用户:承载登录凭据与角色(admin|user)。

    密码以 PBKDF2 哈希存储(见 app/services/auth.py);种子用户由迁移 0006 插入。
    """

    __tablename__ = "users"

    # 主键形如 "usr-" + 6 位 hex
    id: Mapped[str] = mapped_column(String, primary_key=True)
    username: Mapped[str] = mapped_column(
        String, nullable=False, unique=True, index=True
    )
    # 口令哈希:pbkdf2$<iter>$<salt_hex>$<hash_hex>
    password_hash: Mapped[str] = mapped_column(String, nullable=False)
    # 角色:admin | user
    role: Mapped[str] = mapped_column(String, nullable=False)
    display_name: Mapped[str | None] = mapped_column(String, nullable=True)
    # 停用标记:停用后视为未登录
    disabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )
