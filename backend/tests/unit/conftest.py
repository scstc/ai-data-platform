"""隔离上层 tests/conftest.py 的 DB 依赖。

本目录下为纯单测（不依赖 DB / 网络 / 配置）。上层 conftest 定义了一个
`scope="session", autouse=True` 的 `_create_test_database` fixture，会在收集任何用例前
连接 PostgreSQL；这里用同名 fixture 在子目录范围内将其覆盖为 no-op，
使纯单测无需数据库即可运行。
"""

from __future__ import annotations

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio


@pytest_asyncio.fixture(scope="session", autouse=True)
async def _create_test_database() -> AsyncGenerator[None, None]:
    """覆盖上层同名 fixture：纯单测不连数据库。"""
    yield


@pytest.fixture(autouse=True)
def _job_runner_test_db() -> None:
    """覆盖上层同名 fixture：纯单测不需要 job runner。"""
    yield
