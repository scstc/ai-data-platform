"""pytest 测试基建。

- session 级：连 postgres 库 CREATE DATABASE adp_test（已存在则跳过）。
- 函数级：在测试库 create_all / drop_all 建表清表，提供覆盖 get_session
  依赖的 httpx.AsyncClient。

环境变量 TEST_DATABASE_URL 可覆盖默认测试库地址。
"""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator

import asyncpg
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

TEST_DATABASE_URL = os.environ.get(
    "TEST_DATABASE_URL",
    "postgresql+asyncpg://adp:adp_dev_pw@127.0.0.1:55433/adp_test",
)


def _admin_dsn() -> str:
    """用于连接 postgres 维护库执行 CREATE DATABASE 的原生 asyncpg DSN。"""
    # 把 SQLAlchemy URL 转为 asyncpg 可用 DSN，并切到 postgres 维护库
    base = TEST_DATABASE_URL.replace("postgresql+asyncpg://", "postgresql://")
    head, _, _db = base.rpartition("/")
    return f"{head}/postgres"


def _test_db_name() -> str:
    return TEST_DATABASE_URL.rpartition("/")[2]


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _create_test_database() -> AsyncGenerator[None, None]:
    """session 级：确保 adp_test 库存在。"""
    db_name = _test_db_name()
    conn = await asyncpg.connect(_admin_dsn())
    try:
        exists = await conn.fetchval(
            "SELECT 1 FROM pg_database WHERE datname = $1", db_name
        )
        if not exists:
            await conn.execute(f'CREATE DATABASE "{db_name}"')
    finally:
        await conn.close()
    yield


@pytest_asyncio.fixture
async def engine(_create_test_database: None):
    """函数级 engine：建表 → 用例 → 清表。"""
    from app.models import Base

    eng = create_async_engine(TEST_DATABASE_URL, future=True)
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    try:
        yield eng
    finally:
        async with eng.begin() as conn:
            await conn.run_sync(Base.metadata.drop_all)
        await eng.dispose()


@pytest_asyncio.fixture
async def session_factory(engine) -> async_sessionmaker[AsyncSession]:
    """基于测试 engine 的 session 工厂。"""
    return async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest_asyncio.fixture
async def client(session_factory) -> AsyncGenerator[AsyncClient, None]:
    """覆盖 get_session 依赖、指向测试库的 httpx AsyncClient。"""
    from app.core.db import get_session
    from app.main import app

    async def _override_get_session() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = _override_get_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


@pytest_asyncio.fixture
async def seed_users(session_factory) -> None:
    """种子用户:测试库用 create_all 初始化(非 alembic),迁移种子不存在,

    故鉴权类用例需经此 fixture 用 hash_password 插入已知账号
    (admin/ant.design→admin、user/ant.design→user),与迁移 0006 同口令。
    """
    from app.models.user import User
    from app.services.auth import hash_password

    async with session_factory() as session:
        session.add_all(
            [
                User(
                    id="usr-test01",
                    username="admin",
                    password_hash=hash_password("ant.design"),
                    role="admin",
                    display_name="管理员",
                    disabled=False,
                ),
                User(
                    id="usr-test02",
                    username="user",
                    password_hash=hash_password("ant.design"),
                    role="user",
                    display_name="普通用户",
                    disabled=False,
                ),
            ]
        )
        await session.commit()
