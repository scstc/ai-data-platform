"""PG 族连接器:postgresql / hologres / kingbase / gaussdb。

全部走 asyncpg(PG 线协议);方言差异极小(端口由 config 指定),代码路径等价。
品牌兼容性:PG 自引用可真测;hologres/kingbase/gaussdb 真品牌属承诺级,须现场验证。

对外导出符号(供 ingest_runner.py 薄 re-export,§4.4):
    list_tables, run_pg_ingest, IngestError, PgConnector
"""

from __future__ import annotations

import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

import asyncpg

from app.services.connectors.base import (
    IngestError,
    _build_queries,
    apply_filter_operators,
    compute_db_watermark,
)


async def fetch_records(datasource: Any, task: Any) -> list[dict[str, Any]]:
    """拉取 PG 族记录(不落地):跑 extract 全部查询 → 合并 → 过滤算子。

    供「生成 CSV 数据集」复用:只取记录,落地(CSV→MinIO)由调用方负责,
    不写本地受管 jsonl。连接/查询失败抛 IngestError。

    切片 C / Task 5:增量任务(``task.incremental`` + ``task.watermark`` 均就绪)
    → ``_build_queries`` 自动在表查询后拼 ``WHERE "增量列" > <水位>``。
    本函数**只过滤、不推进水位**——推进由 ``run_pg_ingest`` 在 land_records 同事务
    做;本函数供 generate-dataset 路径复用,其自身事务边界由路由层管理。
    """
    queries = _build_queries(
        task.extract,
        incremental=getattr(task, "incremental", None),
        watermark=getattr(task, "watermark", None),
    )
    cfg = datasource.config or {}
    out: list[dict[str, Any]] = []
    try:
        conn = await _connect(cfg)
        try:
            for _suffix, query in queries:
                rows = await conn.fetch(query)
                records = [dict(r) for r in rows]
                out.extend(await apply_filter_operators(task, records))
        finally:
            await conn.close()
    except IngestError:
        raise
    except Exception as exc:  # noqa: BLE001 连接/查询失败统一上报
        raise IngestError(str(exc)) from exc
    return out

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
# 增量水位推进辅助(切片 C / Task 5)
# ---------------------------------------------------------------------------


def _merge_max(current: Any, candidate: Any) -> Any:
    """跨表合并本批增量列的最大值(任一为 None → 取对方;同类型 → max)。"""
    if candidate is None:
        return current
    if current is None:
        return candidate
    return max(current, candidate)


def _stamp_task_watermark(task: Any, value: Any) -> None:
    """把本批 max(增量列) 写回 ``task.watermark``(同事务持久化由 land_records commit)。

    - ``value`` 为 None(空批)→ **不**覆盖既有水位(空批不推进)。
    - ``value`` 非 None → 写 ``{value: <可 JSON 化的值>, updatedAt: <UTC ISO>}``。
      datetime 转 ISO 字符串(JSONB 不直接接受 datetime);int 原样。
    """
    if value is None:
        return
    serializable: Any = (
        value.isoformat() if isinstance(value, datetime) else value
    )
    task.watermark = {
        "value": serializable,
        "updatedAt": datetime.now(UTC).isoformat(),
    }


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

    切片 C / Task 5:增量采集水位推进。
    - 表模式 + ``task.incremental={column,type}`` + ``task.watermark={value}`` →
      ``_build_queries`` 自动拼 ``WHERE "column" > <字面值>``。
    - 落地前算 ``max(增量列)``(本批所有表合并)→ 写回 ``task.watermark``。
      在 ``land_records`` **之前**赋值,确保 SQLAlchemy UnitOfWork 把 watermark
      与新建 Dataset/Version 一并 commit(同事务,无 duplicate-ingest 窗口)。
    - 空批 / 无 incremental / 无 column → 不推进(不覆盖既有水位)。
    """
    from app.services.landing import land_records

    incremental = getattr(task, "incremental", None) or {}
    inc_column: str | None = incremental.get("column") if incremental else None
    queries = _build_queries(
        task.extract,
        incremental=incremental,
        watermark=getattr(task, "watermark", None),
    )
    cfg = datasource.config or {}
    results: list[tuple[Dataset, DatasetVersion]] = []
    batch_max: Any = None  # 本批增量列的最大值(跨表合并)
    try:
        conn = await _connect(cfg)
        try:
            for suffix, query in queries:
                rows = await conn.fetch(query)
                records = [dict(r) for r in rows]
                # 落地前算子过滤:extract.operators 配了则跑 DJ 流水线筛/清洗
                records = await apply_filter_operators(task, records)

                # 增量水位推进:本批合并取 max(增量列),写回 task.watermark。
                # 必须在 land_records 之前赋值——land_records 内部会 commit,
                # SQLAlchemy UnitOfWork 会把 task.watermark 与新版本一并落盘
                # (同事务保证:land 成功 ⇔ 水位推进,无重复采的窗口)。
                if inc_column:
                    batch_max = _merge_max(
                        batch_max, compute_db_watermark(records, inc_column)
                    )
                    _stamp_task_watermark(task, batch_max)

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
                    # 三轴:来源=数据库;数据库直连无文件载体,格式留空
                    source_kind="database",
                    note=f"采集落地:{task.name}(来源 {datasource.name})",
                    produced_by_job_id=job_id,
                    storage_format="parquet",
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
