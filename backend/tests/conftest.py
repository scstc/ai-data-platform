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
import pytest
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
    # 先导入 app.main,确保所有路由引用的模型(如 tag/dataset_tags,仅在 datasets.py
    # 等路由模块内导入)注册进 Base.metadata,否则 create_all 会漏建表。
    import app.main  # noqa: F401

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
async def db_session(session_factory):
    """函数级 async session(基于测试 engine);服务层单测用。"""
    async with session_factory() as session:
        yield session


@pytest_asyncio.fixture
async def client(session_factory, seed_users) -> AsyncGenerator[AsyncClient, None]:
    """覆盖 get_session 依赖、指向测试库的 httpx AsyncClient。

    默认以 admin(usr-test01)身份登录——很多接口(require_perm)在没登录
    时 401,登录 admin 通配 WILDCARD 直接过。个别需要切用户的用例
    (如 RBAC 可见性)用 client.cookies.set('adp_session', sign_token(...))
    自己覆盖。
    """
    from app.core.db import get_session
    from app.main import app
    from app.services.auth import sign_token

    async def _override_get_session() -> AsyncGenerator[AsyncSession, None]:
        async with session_factory() as session:
            yield session

    app.dependency_overrides[get_session] = _override_get_session
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        ac.cookies.set("adp_session", sign_token("admin"))
        yield ac
    app.dependency_overrides.clear()


@pytest.fixture(autouse=True)
def _job_runner_test_db(session_factory, monkeypatch) -> None:
    """加工任务后台执行用独立会话(默认连业务库);测试里改指测试库,保证隔离。

    job_runner._run_job 用 async_session_factory() 自建会话(请求会话已关闭),不走
    get_session 覆盖,故须单独把该工厂指向测试库;并按每用例的新事件循环重建并发
    信号量,避免 asyncio 原语「bound to a different event loop」。
    """
    import asyncio

    from app.core.config import settings
    from app.services import engine, job_runner

    monkeypatch.setattr(job_runner, "async_session_factory", session_factory)
    monkeypatch.setattr(
        engine, "_semaphore", asyncio.Semaphore(settings.engine_concurrency)
    )
    # 多模态就绪是进程级缓存,逐用例清掉,避免跨用例串味
    monkeypatch.setattr(engine, "_multimodal_ready", None)


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
        s.add_all(
            [
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
                Menu(id="m-hidden", parent_id="m-sys", name="隐藏页", menu_type="C",
                     path="/system/hidden", component="system/hidden", sort=2, visible="1", status="0"),
                # 给测试数据集相关接口用的最小权限集(挂在 r-dc / r-self,
                # 实际生产由菜单管理页面配,这里 seed_rbac 仅为打通单测)。
                Menu(id="m-dataset", parent_id=None, name="数据集", menu_type="C",
                     path="/dataset", component="dataset/index", sort=10, visible="0", status="0"),
                Menu(id="m-dataset-list", parent_id="m-dataset", name="数据集列表", menu_type="F",
                     perms="dataset:list", sort=1, visible="0", status="0"),
                Menu(id="m-dataset-detail", parent_id="m-dataset", name="数据集详情", menu_type="F",
                     perms="dataset:detail", sort=2, visible="0", status="0"),
                Menu(id="m-upload-list", parent_id=None, name="上传", menu_type="C",
                     path="/upload", component="upload/index", sort=11, visible="0", status="0"),
                Menu(id="m-upload-perm", parent_id="m-upload-list", name="上传列表", menu_type="F",
                     perms="upload:list", sort=1, visible="0", status="0"),
                RoleDept(role_id="r-custom", dept_id="d-a"),
                RoleMenu(role_id="r-dc", menu_id="m-sys"),
                RoleMenu(role_id="r-dc", menu_id="m-user"),
                RoleMenu(role_id="r-dc", menu_id="m-add"),
                RoleMenu(role_id="r-dc", menu_id="m-hidden"),
                RoleMenu(role_id="r-dc", menu_id="m-dataset"),
                RoleMenu(role_id="r-dc", menu_id="m-dataset-list"),
                RoleMenu(role_id="r-dc", menu_id="m-dataset-detail"),
                RoleMenu(role_id="r-dc", menu_id="m-upload-list"),
                RoleMenu(role_id="r-dc", menu_id="m-upload-perm"),
                RoleMenu(role_id="r-self", menu_id="m-dataset"),
                RoleMenu(role_id="r-self", menu_id="m-dataset-list"),
                RoleMenu(role_id="r-self", menu_id="m-dataset-detail"),
                RoleMenu(role_id="r-self", menu_id="m-upload-list"),
                RoleMenu(role_id="r-self", menu_id="m-upload-perm"),
                User(id="u-super", username="u-super", password_hash="x", role="admin", dept_id="d-root"),
                User(id="u-mgr", username="u-mgr", password_hash="x", role="user", dept_id="d-root"),
                User(id="u-staff", username="u-staff", password_hash="x", role="user", dept_id="d-a"),
                UserRole(user_id="u-mgr", role_id="r-dc"),
                UserRole(user_id="u-staff", role_id="r-self"),
            ]
        )
        await s.commit()
