"""audit log detail: 审计日志加 ip / target_name

Revision ID: 0066_audit_log_detail
Revises: 0065_member_source_kind
Create Date: 2026-07-10

安全审计详细化:
- audit_logs 加 2 列:
  * ip VARCHAR nullable —— 客户端 IP(X-Forwarded-For 首跳,无代理时取对端地址),
    存量行为空。
  * target_name VARCHAR nullable —— 被操作对象的名称快照(中间件按资源类型
    反查落库;DELETE 在请求前解析,否则响应后解析),解析不到为空。

仅 add column(nullable),无数据迁移、无回填风险。downgrade 反向 drop。

**只对整改库 adp_trace 执行 upgrade**。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0066_audit_log_detail"
down_revision: str | None = "0065_member_source_kind"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("audit_logs", sa.Column("ip", sa.String(), nullable=True))
    op.add_column(
        "audit_logs", sa.Column("target_name", sa.String(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("audit_logs", "target_name")
    op.drop_column("audit_logs", "ip")
