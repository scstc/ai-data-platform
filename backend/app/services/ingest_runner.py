"""采集执行(最小真版):对 PostgreSQL 数据源真实拉取 → 落地为 DatasetVersion。

其余数据源类型(S3/HDFS/国产库)的真实采集见 §三A(06-29),此处仅打通 PG。
采集对象 extract:{mode:'table', table} 或 {mode:'sql', sql}。
"""

from __future__ import annotations

from typing import Any

import asyncpg
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.dataset import Dataset
from app.models.dataset_version import DatasetVersion
from app.models.datasource import DataSource
from app.models.ingest_task import IngestTask
from app.services.landing import land_records


class IngestError(RuntimeError):
    """采集执行失败(配置缺失 / 连接 / 查询错误)。"""


def _quote_ident(name: str) -> str:
    """安全引用 SQL 标识符(支持 schema.table),双引号转义防注入。"""
    parts = [p for p in name.split(".") if p]
    return ".".join('"' + p.replace('"', '""') + '"' for p in parts)


def _build_queries(extract: dict[str, Any] | None) -> list[tuple[str | None, str]]:
    """由采集对象生成 [(数据集名后缀, 查询)] 列表。

    - sql 模式:单条,后缀为空。
    - table 模式:勾选的每张表一条(后缀=表名),各产一个数据集。
    """
    extract = extract or {}
    mode = extract.get("mode")
    if mode == "sql":
        sql = (extract.get("sql") or "").strip()
        if not sql:
            raise IngestError("采集对象为 SQL,但 SQL 为空")
        return [(None, sql)]
    if mode == "table":
        tables = [t.strip() for t in (extract.get("tables") or []) if t.strip()]
        if not tables:
            raise IngestError("采集对象为表,但未选择任何表")
        return [(t, f"SELECT * FROM {_quote_ident(t)}") for t in tables]
    raise IngestError("未配置采集对象(请选择表或填写 SQL)")


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

    产物版本经 produced_by_job_id 关联到本次运行的 job(type=ingest)。
    遇到某条查询失败即中止(更早成功的已落地数据集保留),原因上抛。
    """
    queries = _build_queries(task.extract)
    cfg = datasource.config or {}
    results: list[tuple[Dataset, DatasetVersion]] = []
    try:
        conn = await _connect(cfg)
        try:
            for suffix, query in queries:
                rows = await conn.fetch(query)
                records = [dict(r) for r in rows]
                name = f"{task.name} - {suffix}" if suffix else task.name
                ds, ver = await land_records(
                    session,
                    records,
                    dataset_name=name,
                    # 采集落地统一归到 SQL 接入栏(否则 data_type=NULL,数据接入页任何分栏都看不到)
                    data_type="sql",
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
