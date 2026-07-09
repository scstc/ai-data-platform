"""专有库连接器:达梦 / 巨杉 / Hive / Doris(数据接入重构 §4.6)。

所有连接器均为**结构就绪 / 承诺级**:
- 类已注册、Connector Protocol 实现完整;
- 驱动懒 import:驱动未安装时 ``probe`` 返回诚实 not-ready 文案,
  ``list_tables`` / ``run_ingest`` 抛 ``ConnectorNotReady``(不崩、不 500、
  绝不伪造 success,Rule 12);
- 驱动就位 + 真库现场后可直接激活,改动仅锁定在本文件内。

Doris 降档说明(§4.1 / §4.6):
  Apache Doris FE 虽宣称 MySQL 协议,但握手/认证/information_schema 行为
  与原生 MySQL 存在差异,asyncmy 常无法直连。故 Doris **不并入 mysql.py
  「可实现」档**,移至本文件与达梦/巨杉/Hive 同档(结构就绪/承诺级)。

可测性:
  ``unit/test_connectors.py`` 断言「未装驱动 → 明确 not-ready,不崩、
  不伪成功」;真连需各品牌驱动 + 真库现场验证。
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING, Any

from app.services.connectors.base import (
    ConnectorNotReady,
    IngestError,
    StructuralStub,
    _build_queries,
)

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.dataset import Dataset
    from app.models.dataset_version import DatasetVersion
    from app.models.datasource import DataSource
    from app.models.ingest_task import IngestTask


async def _land_rows(
    session: AsyncSession,
    task: IngestTask,
    datasource: DataSource,
    rows: list[dict],
    *,
    table_name: str,
    engine: str,
    note: str,
    job_id: str | None,
) -> DatasetVersion | None:
    """一批行落地(四库共用):任务绑湖(lake_id)→ 入湖为 source_v 快照,
    数据集经「湖抽取」单独产生;存量数据集任务 → 落 draft 版本表成员。
    入湖路径返回 None(无 DatasetVersion 产出)。"""
    if task.lake_id:
        from app.services.data_lake import (  # noqa: PLC0415
            ingest_to_lake_parquet,
        )

        snapshot = await ingest_to_lake_parquet(
            session,
            lake_id=task.lake_id,
            data=rows,
            source_type=engine,
            source_metadata={"db_table": table_name, "db_engine": engine},
            datasource_id=datasource.id,
            ingest_task_id=task.id,
            job_id=job_id,
        )
        task.logs = [
            *task.logs,
            f"[INFO] 已入湖:{snapshot.source_version}"
            f"(表 {table_name},{len(rows)} 行)",
        ]
        return None
    from app.services.landing import add_table_member  # noqa: PLC0415

    version, _member = await add_table_member(
        session,
        task.dataset_id,
        rows,
        table_name=table_name,
        semantic_type="structured",
        source_format="db",
        note=note,
        produced_by_job_id=job_id,
        storage_format="jsonl",
        source_kind="db_ingest",
    )
    return version


# ---------------------------------------------------------------------------
# 达梦(DM8)— 驱动 dmPython
# ---------------------------------------------------------------------------


class DamengConnector(StructuralStub):
    """达梦数据库连接器(DM8,dmPython 驱动)。

    结构就绪 / 承诺级:驱动 ``dmPython`` 未安装时诚实 not-ready;
    安装驱动 + 真库后本文件内激活即可。
    """

    def __init__(self) -> None:
        super().__init__(brand="达梦(DM8)", driver_pkg="dmPython")

    async def probe(self, config: dict) -> tuple[bool, int, str]:
        try:
            import dmPython  # type: ignore[import-untyped]
        except ImportError:
            return (False, 0, self._not_ready_msg)

        t0 = time.monotonic()
        conn = None
        try:
            conn = dmPython.connect(
                user=config.get("username", ""),
                password=config.get("password", ""),
                server=config.get("host", ""),
                port=int(config.get("port") or 5236),
            )
            conn.cursor().execute("SELECT 1 FROM DUAL")
            elapsed = int((time.monotonic() - t0) * 1000)
            return (True, elapsed, "达梦连接成功")
        except Exception as exc:  # pragma: no cover
            return (False, 0, f"达梦连接失败:{exc}")
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass

    async def list_tables(self, config: dict) -> list:
        try:
            import dmPython  # type: ignore[import-untyped]
        except ImportError:
            raise ConnectorNotReady(self._not_ready_msg) from None

        conn = None
        try:
            conn = dmPython.connect(
                user=config.get("username", ""),
                password=config.get("password", ""),
                server=config.get("host", ""),
                port=int(config.get("port") or 5236),
            )
            cur = conn.cursor()
            schema = config.get("database") or config.get("schema") or ""
            if schema:
                cur.execute(
                    "SELECT TABLE_NAME FROM ALL_TABLES WHERE OWNER = :1",
                    (schema.upper(),),
                )
            else:
                cur.execute(
                    "SELECT TABLE_NAME FROM USER_TABLES ORDER BY TABLE_NAME"
                )
            return [row[0] for row in cur.fetchall()]
        except Exception as exc:  # pragma: no cover
            raise IngestError(f"达梦列表失败:{exc}") from exc
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass

    async def run_ingest(
        self,
        session: AsyncSession,
        task: IngestTask,
        datasource: DataSource,
        *,
        job_id: str,
    ) -> list[tuple[Dataset, DatasetVersion]]:
        try:
            import dmPython  # type: ignore[import-untyped]
        except ImportError:
            raise ConnectorNotReady(self._not_ready_msg) from None

        from app.models.dataset import Dataset

        config: dict[str, Any] = datasource.config or {}
        queries = _build_queries(task.extract)
        version: DatasetVersion | None = None
        conn = None
        try:
            conn = dmPython.connect(
                user=config.get("username", ""),
                password=config.get("password", ""),
                server=config.get("host", ""),
                port=int(config.get("port") or 5236),
            )
            for suffix, sql in queries:
                cur = conn.cursor()
                cur.execute(sql)
                cols = [d[0] for d in cur.description]
                rows = [
                    dict(zip(cols, row, strict=False))
                    for row in cur.fetchall()
                ]
                host = config.get("host", "")
                port = config.get("port", 5236)
                target = suffix or "query"
                table_name = suffix or "data"
                landed = await _land_rows(
                    session,
                    task,
                    datasource,
                    rows,
                    table_name=table_name,
                    engine="dameng",
                    note=f"dameng://{host}:{port}/{target}",
                    job_id=job_id,
                )
                if landed is not None:
                    version = landed
        except (ConnectorNotReady, IngestError):
            raise
        except Exception as exc:  # pragma: no cover
            raise IngestError(f"达梦采集失败:{exc}") from exc
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
        if version is None:
            return []
        dataset = await session.get(Dataset, task.dataset_id)
        return [(dataset, version)]


# ---------------------------------------------------------------------------
# 巨杉(SequoiaDB)— 驱动 pysequoiadb
# ---------------------------------------------------------------------------


class SequoiaConnector(StructuralStub):
    """巨杉数据库连接器(SequoiaDB,pysequoiadb 驱动)。

    结构就绪 / 承诺级:驱动 ``pysequoiadb`` 未安装时诚实 not-ready。
    SequoiaDB 是 NoSQL 文档数据库,list_tables 对应列集合(collection),
    run_ingest 扫描集合记录 → land_records。
    """

    def __init__(self) -> None:
        super().__init__(brand="巨杉(SequoiaDB)", driver_pkg="pysequoiadb")

    async def probe(self, config: dict) -> tuple[bool, int, str]:
        try:
            from pysequoiadb import client as sdb_client  # type: ignore[import-untyped]
        except ImportError:
            return (False, 0, self._not_ready_msg)

        t0 = time.monotonic()
        conn = None
        try:
            host = config.get("host", "localhost")
            port = int(config.get("port") or 11810)
            username = config.get("username", "")
            password = config.get("password", "")
            conn = sdb_client(host, port)
            conn.connect(username, password)
            elapsed = int((time.monotonic() - t0) * 1000)
            return (True, elapsed, "巨杉连接成功")
        except Exception as exc:  # pragma: no cover
            return (False, 0, f"巨杉连接失败:{exc}")
        finally:
            if conn is not None:
                try:
                    conn.disconnect()
                except Exception:  # noqa: BLE001
                    pass

    async def list_tables(self, config: dict) -> list:
        try:
            from pysequoiadb import client as sdb_client  # type: ignore[import-untyped]
        except ImportError:
            raise ConnectorNotReady(self._not_ready_msg) from None

        conn = None
        try:
            host = config.get("host", "localhost")
            port = int(config.get("port") or 11810)
            conn = sdb_client(host, port)
            conn.connect(
                config.get("username", ""), config.get("password", "")
            )
            cs_name = config.get("database") or ""
            if cs_name:
                cs = conn.get_collection_space(cs_name)
                names = [f"{cs_name}.{c}" for c in cs.list_collections()]
            else:
                # 列全部集合空间下的集合
                cursor = conn.list_collection_spaces()
                names = []
                record = cursor.next()
                while record is not None:
                    cs_n = record.get("Name", "")
                    try:
                        cs = conn.get_collection_space(cs_n)
                        for coll in cs.list_collections():
                            names.append(f"{cs_n}.{coll}")
                    except Exception:  # noqa: BLE001
                        pass
                    record = cursor.next()
            return names
        except ConnectorNotReady:
            raise
        except Exception as exc:  # pragma: no cover
            raise IngestError(f"巨杉列集合失败:{exc}") from exc
        finally:
            if conn is not None:
                try:
                    conn.disconnect()
                except Exception:  # noqa: BLE001
                    pass

    async def run_ingest(
        self,
        session: AsyncSession,
        task: IngestTask,
        datasource: DataSource,
        *,
        job_id: str,
    ) -> list[tuple[Dataset, DatasetVersion]]:
        try:
            from pysequoiadb import client as sdb_client  # type: ignore[import-untyped]
        except ImportError:
            raise ConnectorNotReady(self._not_ready_msg) from None

        from app.models.dataset import Dataset

        config: dict[str, Any] = datasource.config or {}
        extract = task.extract or {}
        tables: list[str] = [
            t.strip()
            for t in (extract.get("tables") or [])
            if str(t).strip()
        ]
        if not tables:
            raise IngestError(
                "巨杉采集:extract.tables 未配置集合名(格式 cs.collection)"
            )

        conn = None
        version: DatasetVersion | None = None
        try:
            host = config.get("host", "localhost")
            port = int(config.get("port") or 11810)
            conn = sdb_client(host, port)
            conn.connect(
                config.get("username", ""), config.get("password", "")
            )
            for full_name in tables:
                parts = full_name.split(".", 1)
                if len(parts) != 2:
                    raise IngestError(
                        "巨杉集合名格式错误,"
                        f"期望 'cs.collection',实际 '{full_name}'"
                    )
                cs_n, coll_n = parts
                coll = conn.get_collection_space(cs_n).get_collection(coll_n)
                cursor = coll.query()
                rows: list[dict] = []
                rec = cursor.next()
                while rec is not None:
                    # 去掉 SequoiaDB 内部 _id 字段
                    rec.pop("_id", None)
                    rows.append(rec)
                    rec = cursor.next()

                table_name = full_name or "data"
                landed = await _land_rows(
                    session,
                    task,
                    datasource,
                    rows,
                    table_name=table_name,
                    engine="sequoiadb",
                    note=f"sequoiadb://{host}:{port}/{full_name}",
                    job_id=job_id,
                )
                if landed is not None:
                    version = landed
        except (ConnectorNotReady, IngestError):
            raise
        except Exception as exc:  # pragma: no cover
            raise IngestError(f"巨杉采集失败:{exc}") from exc
        finally:
            if conn is not None:
                try:
                    conn.disconnect()
                except Exception:  # noqa: BLE001
                    pass
        if version is None:
            return []
        dataset = await session.get(Dataset, task.dataset_id)
        return [(dataset, version)]


# ---------------------------------------------------------------------------
# Hive — 驱动 pyhive
# ---------------------------------------------------------------------------


class HiveConnector(StructuralStub):
    """Apache Hive 连接器(pyhive 驱动,HiveServer2 Thrift 接口)。

    结构就绪 / 承诺级:驱动 ``pyhive`` 未安装时诚实 not-ready。
    list_tables 查 ``SHOW TABLES IN <database>``;
    run_ingest 按 _build_queries 编排 HQL → land_records。
    """

    def __init__(self) -> None:
        super().__init__(brand="Hive", driver_pkg="pyhive")

    def _hive_connect(self, config: dict) -> Any:
        from pyhive import hive  # type: ignore[import-untyped]

        return hive.Connection(
            host=config.get("host", "localhost"),
            port=int(config.get("port") or 10000),
            username=config.get("username") or None,
            password=config.get("password") or None,
            database=config.get("database") or "default",
            auth=config.get("auth") or "NONE",
        )

    async def probe(self, config: dict) -> tuple[bool, int, str]:
        try:
            from pyhive import hive  # type: ignore[import-untyped]  # noqa: F401
        except ImportError:
            return (False, 0, self._not_ready_msg)

        t0 = time.monotonic()
        conn = None
        try:
            conn = self._hive_connect(config)
            cur = conn.cursor()
            cur.execute("SELECT 1")
            elapsed = int((time.monotonic() - t0) * 1000)
            return (True, elapsed, "Hive 连接成功")
        except Exception as exc:  # pragma: no cover
            return (False, 0, f"Hive 连接失败:{exc}")
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass

    async def list_tables(self, config: dict) -> list:
        try:
            from pyhive import hive  # type: ignore[import-untyped]  # noqa: F401
        except ImportError:
            raise ConnectorNotReady(self._not_ready_msg) from None

        conn = None
        try:
            conn = self._hive_connect(config)
            cur = conn.cursor()
            database = config.get("database") or "default"
            cur.execute(f"SHOW TABLES IN `{database}`")
            return [row[0] for row in cur.fetchall()]
        except ConnectorNotReady:
            raise
        except Exception as exc:  # pragma: no cover
            raise IngestError(f"Hive 列表失败:{exc}") from exc
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass

    async def run_ingest(
        self,
        session: AsyncSession,
        task: IngestTask,
        datasource: DataSource,
        *,
        job_id: str,
    ) -> list[tuple[Dataset, DatasetVersion]]:
        try:
            from pyhive import hive  # type: ignore[import-untyped]  # noqa: F401
        except ImportError:
            raise ConnectorNotReady(self._not_ready_msg) from None

        from app.models.dataset import Dataset

        config: dict[str, Any] = datasource.config or {}
        queries = _build_queries(task.extract)
        version: DatasetVersion | None = None
        conn = None
        try:
            conn = self._hive_connect(config)
            for suffix, hql in queries:
                cur = conn.cursor()
                cur.execute(hql)
                # pyhive cursor.description 列名格式为 "table.col",取最后段
                cols = [d[0].split(".")[-1] for d in cur.description]
                rows = [
                    dict(zip(cols, row, strict=False))
                    for row in cur.fetchall()
                ]
                host = config.get("host", "localhost")
                port = config.get("port", 10000)
                db = config.get("database", "default")
                target = suffix or "query"
                table_name = suffix or "data"
                landed = await _land_rows(
                    session,
                    task,
                    datasource,
                    rows,
                    table_name=table_name,
                    engine="hive",
                    note=f"hive://{host}:{port}/{db}/{target}",
                    job_id=job_id,
                )
                if landed is not None:
                    version = landed
        except (ConnectorNotReady, IngestError):
            raise
        except Exception as exc:  # pragma: no cover
            raise IngestError(f"Hive 采集失败:{exc}") from exc
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
        if version is None:
            return []
        dataset = await session.get(Dataset, task.dataset_id)
        return [(dataset, version)]


# ---------------------------------------------------------------------------
# Doris — asyncmy 驱动(降档:结构就绪 / 承诺级)
# ---------------------------------------------------------------------------


class DorisConnector(StructuralStub):
    """Apache Doris 连接器(降档:结构就绪 / 承诺级)。

    Doris FE 虽宣称 MySQL 协议,但握手/认证/information_schema 行为与原生
    MySQL 存在差异,asyncmy 常无法直连(§4.1 降档说明)。故本连接器与达梦/
    巨杉/Hive 同档:驱动懒 import,驱动未装或连接失败时诚实 not-ready。

    可测性:``_build_queries`` 编排逻辑可纯单测(fake-driver 或直接调函数);
    真正连接 Doris FE 端到端需真实 Doris 集群验证。
    """

    # Doris MySQL 协议默认端口
    _DEFAULT_PORT = 9030

    def __init__(self) -> None:
        super().__init__(brand="Doris", driver_pkg="asyncmy")

    async def probe(self, config: dict) -> tuple[bool, int, str]:
        try:
            import asyncmy  # type: ignore[import-untyped]
        except ImportError:
            return (False, 0, self._not_ready_msg)

        t0 = time.monotonic()
        conn = None
        try:
            conn = await asyncmy.connect(
                host=config.get("host", "localhost"),
                port=int(config.get("port") or self._DEFAULT_PORT),
                user=config.get("username", ""),
                password=config.get("password", ""),
                db=config.get("database") or "",
                connect_timeout=10,
            )
            async with conn.cursor() as cur:
                await cur.execute("SELECT 1")
            elapsed = int((time.monotonic() - t0) * 1000)
            return (True, elapsed, "Doris 连接成功")
        except Exception as exc:
            # Doris 握手/方言差异可能在此处暴露,如实返回失败
            return (
                False,
                0,
                f"Doris 连接失败(方言差异或环境未就绪):{exc}",
            )
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass

    async def list_tables(self, config: dict) -> list:
        try:
            import asyncmy  # type: ignore[import-untyped]
        except ImportError:
            raise ConnectorNotReady(self._not_ready_msg) from None

        conn = None
        try:
            conn = await asyncmy.connect(
                host=config.get("host", "localhost"),
                port=int(config.get("port") or self._DEFAULT_PORT),
                user=config.get("username", ""),
                password=config.get("password", ""),
                db=config.get("database") or "",
                connect_timeout=10,
            )
            database = config.get("database") or ""
            async with conn.cursor() as cur:
                if database:
                    await cur.execute(
                        "SELECT TABLE_NAME FROM information_schema.tables "
                        "WHERE TABLE_SCHEMA = %s ORDER BY TABLE_NAME",
                        (database,),
                    )
                else:
                    await cur.execute("SHOW TABLES")
                rows = await cur.fetchall()
            return [row[0] for row in rows]
        except ConnectorNotReady:
            raise
        except Exception as exc:  # pragma: no cover
            raise IngestError(f"Doris 列表失败:{exc}") from exc
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass

    async def run_ingest(
        self,
        session: AsyncSession,
        task: IngestTask,
        datasource: DataSource,
        *,
        job_id: str,
    ) -> list[tuple[Dataset, DatasetVersion]]:
        try:
            import asyncmy  # type: ignore[import-untyped]
        except ImportError:
            raise ConnectorNotReady(self._not_ready_msg) from None

        from app.models.dataset import Dataset

        config: dict[str, Any] = datasource.config or {}
        queries = _build_queries(task.extract)
        version: DatasetVersion | None = None
        conn = None
        try:
            conn = await asyncmy.connect(
                host=config.get("host", "localhost"),
                port=int(config.get("port") or self._DEFAULT_PORT),
                user=config.get("username", ""),
                password=config.get("password", ""),
                db=config.get("database") or "",
                connect_timeout=10,
            )
            for suffix, sql in queries:
                async with conn.cursor() as cur:
                    await cur.execute(sql)
                    cols = [d[0] for d in cur.description]
                    rows = [
                        dict(zip(cols, row, strict=False))
                        for row in await cur.fetchall()
                    ]
                host = config.get("host", "localhost")
                port = config.get("port", self._DEFAULT_PORT)
                db = config.get("database", "")
                target = suffix or "query"
                table_name = suffix or "data"
                landed = await _land_rows(
                    session,
                    task,
                    datasource,
                    rows,
                    table_name=table_name,
                    engine="doris",
                    note=f"doris://{host}:{port}/{db}/{target}",
                    job_id=job_id,
                )
                if landed is not None:
                    version = landed
        except (ConnectorNotReady, IngestError):
            raise
        except Exception as exc:  # pragma: no cover
            raise IngestError(
                f"Doris 采集失败(方言差异或环境未就绪):{exc}"
            ) from exc
        finally:
            if conn is not None:
                try:
                    conn.close()
                except Exception:  # noqa: BLE001
                    pass
        if version is None:
            return []
        dataset = await session.get(Dataset, task.dataset_id)
        return [(dataset, version)]
