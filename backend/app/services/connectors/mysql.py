"""MySQL 族连接器(仅 goldendb,asyncmy 驱动)。

§4.5:MysqlConnector 镜像 PgConnector 结构。asyncmy **懒 import**:
仅在方法内部 import,模块顶层不依赖 asyncmy,注册表 import 不受驱动缺失影响。

驱动未装时:
- probe  → 返回 (False, 0, not-ready 文案),不崩、不 500
- list_tables / run_ingest → 抛 ConnectorNotReady

真连时:
- list_tables  → information_schema.tables(用户表,排除系统 schema)
- run_ingest   → _build_queries(extract) → SELECT → land_records(
                    data_type="sql", semantic_type="structured")
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from app.services.connectors.base import (
    ConnectorNotReady,
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

_DRIVER_PKG = "asyncmy>=0.2.9"
_NOT_READY_MSG = (
    "MySQL(goldendb)连接器结构就绪,本环境未装驱动 asyncmy,暂不可真连"
)

# information_schema 查询:仅用户表,排除 MySQL 自带系统库
_LIST_TABLES_SQL = (
    "SELECT table_schema, table_name "
    "FROM information_schema.tables "
    "WHERE table_type = 'BASE TABLE' "
    "AND table_schema NOT IN "
    "('mysql','information_schema','performance_schema','sys') "
    "ORDER BY table_schema, table_name"
)


def _import_asyncmy():  # noqa: ANN202
    """懒 import asyncmy;未装则抛 ConnectorNotReady。"""
    try:
        import asyncmy  # noqa: PLC0415

        return asyncmy
    except ImportError as exc:
        raise ConnectorNotReady(_NOT_READY_MSG) from exc


async def _connect(cfg: dict[str, Any]):  # noqa: ANN202
    """按数据源 config 建 asyncmy 连接(懒 import,未装则抛 ConnectorNotReady)。"""
    asyncmy = _import_asyncmy()
    return await asyncmy.connect(
        host=cfg.get("host"),
        port=int(cfg.get("port") or 3306),
        db=cfg.get("database"),
        user=cfg.get("username"),
        password=cfg.get("password") or "",
        connect_timeout=10,
    )


async def fetch_records(datasource: Any, task: Any) -> list[dict[str, Any]]:
    """拉取 MySQL(goldendb)记录(不落地):跑 extract 全部查询 → 合并 → 过滤算子。

    供「生成 CSV 数据集」复用。驱动未装抛 ConnectorNotReady;查询失败抛 IngestError。
    """
    _import_asyncmy()  # 未装则抛 ConnectorNotReady
    queries = _build_queries(task.extract)
    cfg = datasource.config or {}
    out: list[dict[str, Any]] = []
    try:
        conn = await _connect(cfg)
        try:
            for _suffix, query in queries:
                async with conn.cursor() as cur:
                    await cur.execute(query)
                    columns = [desc[0] for desc in cur.description]
                    raw_rows = await cur.fetchall()
                records = [
                    dict(zip(columns, row, strict=False)) for row in raw_rows
                ]
                out.extend(await apply_filter_operators(task, records))
        finally:
            conn.close()
    except (ConnectorNotReady, IngestError):
        raise
    except Exception as exc:  # noqa: BLE001 连接/查询失败统一上报
        raise IngestError(str(exc)) from exc
    return out


class MysqlConnector:
    """MySQL 族连接器(goldendb)。

    实现 base.Connector 协议(§4.2),结构与 PgConnector 镜像对称。
    """

    # ------------------------------------------------------------------ probe
    async def probe(self, config: dict) -> tuple[bool, int, str]:
        """测连接。返回 (是否成功, 耗时毫秒, 文案)。

        驱动未装 → (False, 0, not-ready 文案),不崩、不 500。
        """
        try:
            _import_asyncmy()
        except ConnectorNotReady:
            return (False, 0, _NOT_READY_MSG)

        t0 = time.monotonic()
        try:
            conn = await _connect(config)
            try:
                await conn.ping()
            finally:
                conn.close()
            elapsed = int((time.monotonic() - t0) * 1000)
            return (True, elapsed, "连接成功")
        except ConnectorNotReady:
            return (False, 0, _NOT_READY_MSG)
        except Exception as exc:  # noqa: BLE001
            elapsed = int((time.monotonic() - t0) * 1000)
            return (False, elapsed, str(exc))

    # --------------------------------------------------------------- list_tables
    async def list_tables(self, config: dict) -> list:
        """列出库内用户表(schema.table 限定名)。

        驱动未装 → 抛 ConnectorNotReady。
        连接/查询失败 → 抛 IngestError。
        """
        _import_asyncmy()  # 未装则抛 ConnectorNotReady
        try:
            conn = await _connect(config)
            try:
                async with conn.cursor() as cur:
                    await cur.execute(_LIST_TABLES_SQL)
                    rows = await cur.fetchall()
            finally:
                conn.close()
        except ConnectorNotReady:
            raise
        except Exception as exc:  # noqa: BLE001
            raise IngestError(str(exc)) from exc
        return [f"{row[0]}.{row[1]}" for row in rows]

    # --------------------------------------------------------------- run_ingest
    async def run_ingest(
        self,
        session: AsyncSession,
        task: IngestTask,
        datasource: DataSource,
        *,
        job_id: str,
    ) -> list[tuple[Dataset, DatasetVersion]]:
        """真实拉取 MySQL → 每张表/查询经 land_records 各落地一个受管版本。

        - data_type="sql"        : SQL 接入栏功能键,与 PgConnector 保持一致
        - semantic_type="structured" : 语义维度(§4.5)

        遇到某条查询失败即中止;已落地数据集保留,原因上抛。
        驱动未装 → 抛 ConnectorNotReady。
        """
        from app.services.landing import land_records  # noqa: PLC0415

        _import_asyncmy()  # 未装则抛 ConnectorNotReady

        queries = _build_queries(task.extract)
        cfg = datasource.config or {}
        results: list[tuple[Dataset, DatasetVersion]] = []

        try:
            conn = await _connect(cfg)
            try:
                for suffix, query in queries:
                    async with conn.cursor() as cur:
                        await cur.execute(query)
                        columns = [desc[0] for desc in cur.description]
                        raw_rows = await cur.fetchall()
                    records = [
                        dict(zip(columns, row, strict=False))
                        for row in raw_rows
                    ]
                    # 落地前算子过滤:extract.operators 配了则跑 DJ 流水线筛/清洗
                    records = await apply_filter_operators(task, records)
                    name = f"{task.name} - {suffix}" if suffix else task.name
                    ds, ver = await land_records(
                        session,
                        records,
                        dataset_name=name,
                        # 采集落地统一归到 SQL 接入栏(data_type 是功能键,不改)
                        data_type="sql",
                        # 语义维度:结构化(§4.5)
                        semantic_type="structured",
                        # 三轴:来源=数据库;数据库直连无文件载体,格式留空
                        source_kind="database",
                        note=f"采集落地:{task.name}(来源 {datasource.name})",
                        produced_by_job_id=job_id,
                        storage_format="parquet",
                    )
                    results.append((ds, ver))
            finally:
                conn.close()
        except (ConnectorNotReady, IngestError):
            raise
        except Exception as exc:  # noqa: BLE001
            raise IngestError(str(exc)) from exc

        return results
