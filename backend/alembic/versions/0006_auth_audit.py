"""auth + audit: users / audit_logs + 种子用户

Revision ID: 0006_auth_audit
Revises: 0005_ingest_jobs
Create Date: 2026-06-15

访问安全(#5)落地(设计见 docs/plan/06-访问安全设计.md):
- users:登录主体(username 唯一/索引、password_hash、role、disabled)。
- audit_logs:写操作审计(created_at 索引,供倒序分页)。
- 种子两个账号(admin/user,口令均 ant.design),保证升级后 admin/ant.design 仍可登。

种子口令哈希在迁移内用 hashlib 内联算出(不 import app 代码,保持迁移自包含),
格式与 app/services/auth.py 的 verify_password 完全一致:
``pbkdf2$<iter>$<salt_hex>$<hash_hex>``(iter=200000,sha256)。每个用户用不同固定 salt。
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_auth_audit"
down_revision: str | None = "0005_ingest_jobs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 种子哈希参数(与 app/services/auth.py 锁死一致)
_ITER = 200_000
# 每个种子用户的固定 salt(hex,16 字节),保证迁移幂等且可被 verify_password 验证
_ADMIN_SALT = "a1b2c3d4e5f60718293a4b5c6d7e8f90"
_USER_SALT = "0f1e2d3c4b5a69788796a5b4c3d2e1f0"


def _seed_hash(password: str, salt_hex: str) -> str:
    """内联算出 pbkdf2 存储格式(与 verify_password 解析格式逐字一致)。"""
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), bytes.fromhex(salt_hex), _ITER
    ).hex()
    return f"pbkdf2${_ITER}${salt_hex}${digest}"


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("password_hash", sa.String(), nullable=False),
        sa.Column("role", sa.String(), nullable=False),
        sa.Column("display_name", sa.String(), nullable=True),
        sa.Column(
            "disabled",
            sa.Boolean(),
            server_default=sa.false(),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("username"),
    )
    op.create_index("ix_users_username", "users", ["username"], unique=True)

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("username", sa.String(), nullable=False),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("method", sa.String(), nullable=False),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("target", sa.String(), nullable=True),
        sa.Column("status_code", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_audit_logs_created_at", "audit_logs", ["created_at"]
    )

    # 种子用户:口令均 ant.design;保证升级后 admin/ant.design 可登(回归红线)。
    op.bulk_insert(
        sa.table(
            "users",
            sa.column("id", sa.String),
            sa.column("username", sa.String),
            sa.column("password_hash", sa.String),
            sa.column("role", sa.String),
            sa.column("display_name", sa.String),
            sa.column("disabled", sa.Boolean),
        ),
        [
            {
                "id": "usr-000001",
                "username": "admin",
                "password_hash": _seed_hash("ant.design", _ADMIN_SALT),
                "role": "admin",
                "display_name": "管理员",
                "disabled": False,
            },
            {
                "id": "usr-000002",
                "username": "user",
                "password_hash": _seed_hash("ant.design", _USER_SALT),
                "role": "user",
                "display_name": "普通用户",
                "disabled": False,
            },
        ],
    )


def downgrade() -> None:
    op.drop_index("ix_audit_logs_created_at", table_name="audit_logs")
    op.drop_table("audit_logs")
    op.drop_index("ix_users_username", table_name="users")
    op.drop_table("users")
