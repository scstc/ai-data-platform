"""PG 族连接器:postgresql / hologres / kingbase / gaussdb。

全部走 asyncpg(PG 线协议);方言差异极小(端口由 config 指定),代码路径等价。
品牌兼容性:PG 自引用可真测;hologres/kingbase/gaussdb 真品牌属承诺级,须现场验证。

对外导出符号(供 ingest_runner.py 薄 re-export,§4.4):
    list_tables, run_pg_ingest, IngestError, PgConnector
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

import asyncpg

from app.services.connectors.base import (
    IngestError,
    _build_queries,
    apply_filter_operators,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.dataset import Dataset
    from app.models.dataset_version import DatasetVersion
    from app.models.datasource import DataSource
    from app.models.ingest_task import IngestTask


# ---------------------------------------------------------------------------
# 内部连接工具(从 ingest_runner 搬入,行为不变)
# ---------------------------------------------------------------------------


async def _connect(cfg: dict[str, Any]) -> asyncpg.Connection:
    """按数据源 config 建 asyncpg 连接。"""
    return await asyncpg.connect(
        host=cfg.get("host"),
        port=int(cfg.get("port") or 5432),
        database=cfg.get("database"),
        user=cfg.get("username"),
        password=cfg.get("password"),
        timeout=10,
    )


# ---------------------------------------------------------------------------
# 公开函数(供 ingest_runner 薄 re-export,§4.4)
# ---------------------------------------------------------------------------


async def list_tables(cfg: dict[str, Any]) -> list[str]:
    """列出库内用户表(schema.table 限定名,排除系统 schema)。"""
    sql = (
        "SELECT table_schema, table_name FROM information_schema.tables "
        "WHERE table_type='BASE TABLE' "
        "AND table_schema NOT IN ('pg_catalog','information_schema') "
        "ORDER BY table_schema, table_name"
    )
    try:
        conn = await _connect(cfg)
        try:
            rows = await conn.fetch(sql)
        finally:
            await conn.close()
    except Exception as exc:  # noqa: BLE001 连接/查询失败统一上报
        raise IngestError(str(exc)) from exc
    return [f"{r['table_schema']}.{r['table_name']}" for r in rows]


async def run_pg_ingest(
    session: AsyncSession,
    task: IngestTask,
    datasource: DataSource,
    *,
    job_id: str,
) -> list[tuple[Dataset, DatasetVersion]]:
    """真实拉取 PostgreSQL → 每张表/查询经 land_records 各落地一个受管版本。

    - data_type 继续写 "sql"(SQL 接入栏功能键,test_ingest_tasks:314 断言保留)。
    - semantic_type 写 "structured"(版本级语义快照,新断言 §9)。
    遇到某条查询失败即中止(更早成功的已落地数据集保留),原因上抛。
    """
    from app.services.landing import land_records

    queries = _build_queries(task.extract)
    cfg = datasource.config or {}
    results: list[tuple[Dataset, DatasetVersion]] = []
    try:
        conn = await _connect(cfg)
        try:
            for suffix, query in queries:
                rows = await conn.fetch(query)
                records = [dict(r) for r in rows]
                # 落地前算子过滤:extract.operators 配了则跑 DJ 流水线筛/清洗
                records = await apply_filter_operators(task, records)
                name = f"{task.name} - {suffix}" if suffix else task.name
                ds, ver = await land_records(
                    session,
                    records,
                    dataset_name=name,
                    # 采集落地统一归到 SQL 接入栏
                    # (否则 data_type=NULL,数据接入页任何分栏都看不到)
                    data_type="sql",
                    # 语义维度:PG 表结构化数据
                    semantic_type="structured",
                    note=f"采集落地:{task.name}(来源 {datasource.name})",
                    produced_by_job_id=job_id,
                )
                results.append((ds, ver))
        finally:
            await conn.close()
    except IngestError:
        raise
    except Exception as exc:  # noqa: BLE001 连接/查询失败统一上报
        raise IngestError(str(exc)) from exc
    return results


# ---------------------------------------------------------------------------
# PgConnector — 实现 base.Connector 协议
# ---------------------------------------------------------------------------


class PgConnector:
    """PG 族连接器(postgresql/hologres/kingbase/gaussdb)。

    probe:真实 asyncpg 连接 + 计时(ms);
    list_tables:查 information_schema;
    run_ingest:复用 run_pg_ingest。
    """

    async def probe(self, config: dict) -> tuple[bool, int, str]:
        """真实连接探测,返回 (成功, 耗时ms, 文案)。"""
        t0 = time.monotonic()
        try:
            conn = await _connect(config)
            try:
                await conn.fetchval("SELECT 1")
            finally:
                await conn.close()
        except Exception as exc:  # noqa: BLE001
            elapsed = int((time.monotonic() - t0) * 1000)
            return (False, elapsed, f"连接失败:{exc}")
        elapsed = int((time.monotonic() - t0) * 1000)
        host = config.get("host", "")
        port = config.get("port", 5432)
        db = config.get("database", "")
        return (True, elapsed, f"连接成功:{host}:{port}/{db}({elapsed} ms)")

    async def list_tables(self, config: dict) -> list[str]:
        """列出库内用户表(schema.table 限定名)。"""
        return await list_tables(config)

    async def run_ingest(
        self,
        session: AsyncSession,
        task: IngestTask,
        datasource: DataSource,
        *,
        job_id: str,
    ) -> list[tuple[Dataset, DatasetVersion]]:
        """真实拉取,委托给 run_pg_ingest。"""
        return await run_pg_ingest(session, task, datasource, job_id=job_id)
