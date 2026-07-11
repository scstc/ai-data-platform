"""连接器分批读取与语句超时的纯单元测试(不连库)。

编码业务意图:
- 分批游标/`fetchmany` 循环是为「不把整个结果集一次读入内存」而改的;正确性红线
  是**分批读取必须与一次读全等价——不丢行、不重复、遇空批即终止**。若日后有人把
  循环写错(如漏加偏移、提前 break),这些断言会失败。
- 语句级超时可配(环境变量),但必须有安全兜底:未配/配错时回落默认 600s,配 0
  表示不限制——避免误配导致连接器要么永不超时、要么被误值打断。
"""

from __future__ import annotations

import pytest

from app.services.connectors import mysql as mysql_conn
from app.services.connectors import pg as pg_conn


class _FakeCursor:
    """模拟 DB-API 异步游标:按请求的 size 分批吐行,取尽后返回空列表。"""

    def __init__(self, rows: list) -> None:
        self._rows = rows
        self._i = 0
        self.calls = 0

    async def fetchmany(self, size: int) -> list:
        self.calls += 1
        batch = self._rows[self._i : self._i + size]
        self._i += len(batch)
        return batch


@pytest.mark.asyncio
async def test_drain_cursor_reads_all_rows_across_batches() -> None:
    """分批读取跨多批仍取全部行、保序、不重复(等价 fetchall)。"""
    rows = [(i,) for i in range(23)]
    cur = _FakeCursor(rows)
    got = await mysql_conn._drain_cursor(cur, batch_size=5)
    assert got == rows  # 顺序与内容完全一致
    # 23 行 / 每批 5:5 批满 + 1 批 3 行 + 1 批空 → 至少多次调用,证明确实分批
    assert cur.calls >= 5


@pytest.mark.asyncio
async def test_drain_cursor_empty_result_terminates() -> None:
    """空结果集:首批即空 → 立刻返回空列表,不死循环。"""
    cur = _FakeCursor([])
    got = await mysql_conn._drain_cursor(cur, batch_size=10)
    assert got == []
    assert cur.calls == 1


@pytest.mark.asyncio
async def test_drain_cursor_exact_multiple_of_batch() -> None:
    """行数恰为批大小整数倍:需额外一次空批才知道读完(不能少读也不能漏终止)。"""
    rows = [(i,) for i in range(10)]
    cur = _FakeCursor(rows)
    got = await mysql_conn._drain_cursor(cur, batch_size=5)
    assert got == rows
    assert cur.calls == 3  # 5 + 5 + 空


def test_statement_timeout_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """未配环境变量 → 默认 600s = 600000ms。"""
    monkeypatch.delenv("INGEST_DB_STATEMENT_TIMEOUT_SECONDS", raising=False)
    assert pg_conn._statement_timeout_ms() == 600_000


def test_statement_timeout_override(monkeypatch: pytest.MonkeyPatch) -> None:
    """配置有效整数秒 → 换算为毫秒。"""
    monkeypatch.setenv("INGEST_DB_STATEMENT_TIMEOUT_SECONDS", "30")
    assert pg_conn._statement_timeout_ms() == 30_000


def test_statement_timeout_zero_means_unlimited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """配 0 → 0ms,连接器据此不下发 SET(与 PG「0=不限制」语义一致)。"""
    monkeypatch.setenv("INGEST_DB_STATEMENT_TIMEOUT_SECONDS", "0")
    assert pg_conn._statement_timeout_ms() == 0


def test_statement_timeout_invalid_falls_back(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """配非法值 → 安全回落默认 600s,而非崩溃或 0(误配不应关闭超时保护)。"""
    monkeypatch.setenv("INGEST_DB_STATEMENT_TIMEOUT_SECONDS", "abc")
    assert pg_conn._statement_timeout_ms() == 600_000


def test_statement_timeout_negative_clamped_to_zero(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """负值被夹到 0(不下发负超时),避免向 PG 发出非法 statement_timeout。"""
    monkeypatch.setenv("INGEST_DB_STATEMENT_TIMEOUT_SECONDS", "-5")
    assert pg_conn._statement_timeout_ms() == 0
