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


def _stamp_task_watermark(task: Any, value: Any) -> None:
    """以 running-max 方式把 max(增量列) 写回 ``task.watermark``。

    在每张表 land_records **成功之后**调用,取 ``max(当前水位, 本表增量列 max)``,
    保证水位不超过「实际已落地」范畴。中途某表失败 → 水位只反映此前已成功落地的
    表,失败表在重试时仍被采(防 DATA LOSS,C5 评审 Finding 1)。

    - ``value`` 为 None(空批)→ 不动既有水位(空批不推进)。
    - datetime → ISO 字符串(JSONB 不直接接受 datetime);int 原样。
    """
    if value is None:
        return
    serializable: Any = (
        value.isoformat() if isinstance(value, datetime) else value
    )
    current_wm = getattr(task, "watermark", None) or {}
    current_value = current_wm.get("value")
    merged: Any = (
        max(current_value, serializable)
        if current_value is not None
        else serializable
    )
    task.watermark = {
        "value": merged,
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

    切片 C / Task 5 + C5 评审 Finding 1 修复:增量采集水位推进(running-max-after-success)。
    - 表模式 + ``task.incremental={column,type}`` + ``task.watermark={value}`` →
      ``_build_queries`` 自动拼 ``WHERE "column" > <字面值>``。
    - 每张表 ``land_records`` **成功之后**推进 ``task.watermark`` 到
      ``max(当前水位, 本表增量列 max)``;中途某表失败 → 水位只反映此前已落地的表,
      失败表重试时仍被采(防 DATA LOSS)。空批 / 无 incremental / 无 column → 不推进。
    """
    from app.models.dataset import Dataset
    from app.services.landing import add_table_member

    incremental = getattr(task, "incremental", None) or {}
    inc_column: str | None = incremental.get("column") if incremental else None
    queries = _build_queries(
        task.extract,
        incremental=incremental,
        watermark=getattr(task, "watermark", None),
    )
    cfg = datasource.config or {}
    version: DatasetVersion | None = None
    try:
        conn = await _connect(cfg)
        try:
            for suffix, query in queries:
                rows = await conn.fetch(query)
                records = [dict(r) for r in rows]
                # 落地前算子过滤:extract.operators 配了则跑 DJ 流水线筛/清洗
                records = await apply_filter_operators(task, records)

                table_name = suffix or "data"
                if task.lake_id:
                    # 治理改造:采集入湖归档(source_v 快照),数据集经「湖抽取」
                    # 单独产生;不再直落数据集。
                    from app.services.data_lake import (  # noqa: PLC0415
                        ingest_to_lake_parquet,
                    )

                    engine = datasource.db_kind or "postgresql"
                    snapshot = await ingest_to_lake_parquet(
                        session,
                        lake_id=task.lake_id,
                        data=records,
                        source_type=engine,
                        source_metadata={
                            "db_table": table_name,
                            "db_engine": engine,
                        },
                        datasource_id=datasource.id,
                        ingest_task_id=task.id,
                    )
                    task.logs = [
                        *task.logs,
                        f"[INFO] 已入湖:{snapshot.source_version}"
                        f"(表 {table_name},{len(records)} 行)",
                    ]
                else:
                    # 存量数据集任务:每表作成员落进 task.dataset_id 的 draft 版本
                    # (多表 = 一版本多成员);单查询无 suffix → 成员名 "data"。
                    version, _member = await add_table_member(
                        session,
                        task.dataset_id,
                        records,
                        table_name=table_name,
                        # 语义维度:PG 表结构化数据(data_type 归数据集级,连接器不改)
                        semantic_type="structured",
                        source_format="db",
                        note=f"采集落地:{task.name}(来源 {datasource.name})",
                        produced_by_job_id=job_id,
                        storage_format="jsonl",
                    )

                # C5 评审 Finding 1 修复:水位推进改为每表成功落地**之后**
                # running-max(当前水位, 本表增量列 max)。中途某表失败 → 水位
                # 只反映此前已成功落地的表,失败表重试时仍被采(防 DATA LOSS)。
                if inc_column:
                    _stamp_task_watermark(
                        task, compute_db_watermark(records, inc_column)
                    )
        finally:
            await conn.close()
    except IngestError:
        raise
    except Exception as exc:  # noqa: BLE001 连接/查询失败统一上报
        raise IngestError(str(exc)) from exc
    if version is None:
        return []
    dataset = await session.get(Dataset, task.dataset_id)
    return [(dataset, version)]


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
