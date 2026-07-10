"""审计写入隔离回归。

测试意图(为何重要):
审计中间件(app/core/audit.py)不走 get_session 依赖注入,而是模块顶层
import async_session_factory 自建会话。若 conftest 只覆盖 get_session,
测试期间的审计行会漏写进 backend/.env 指向的业务库(真实发生过:业务库
audit_logs 里出现测试用例创建的 llm-* 幽灵记录)。本用例锁住
_bind_test_session_factory 对 core.audit 的补丁:审计行必须落测试库。
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select

from app.models.audit_log import AuditLog

pytestmark = pytest.mark.asyncio


async def test_audit_rows_land_in_test_db(client: AsyncClient, session_factory):
    """经审计的写端点执行后,审计行出现在测试库(而非漏写业务库)。"""
    resp = await client.post(
        "/api/v1/llm-providers",
        json={
            "name": "audit-probe",
            "provider": "openai",
            "baseUrl": "https://api.openai.com/v1",
            "apiKey": "sk-x",
        },
    )
    assert resp.status_code == 200, resp.text

    async with session_factory() as s:
        rows = (
            await s.scalars(
                select(AuditLog).where(AuditLog.path == "/api/v1/llm-providers")
            )
        ).all()
    assert rows, "审计行未写入测试库——core.audit 的会话工厂可能仍指向业务库"
