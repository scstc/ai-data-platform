"""采集执行的薄 facade(数据接入重构 §4.4 / §12.5)。

实现已搬到 ``app.services.connectors`` 包:PG 族在 ``connectors/pg.py``,共享
查询编排工具(``IngestError`` / ``_build_queries`` / ``_quote_ident``)在
``connectors/base.py``。本模块仅做 re-export,保证历史 import 不断:

    from app.services.ingest_runner import IngestError, list_tables   # datasources.py
    from app.services.ingest_runner import IngestError, run_pg_ingest  # ingest_tasks.py

新代码应直接从 ``app.services.connectors`` / ``connectors.pg`` / ``connectors.base``
import;本 facade 保留以兼容存量调用方,行为与签名字节级不变。
"""

from __future__ import annotations

from app.services.connectors.base import (
    IngestError,
    _build_queries,
    _quote_ident,
)
from app.services.connectors.pg import (
    _connect,
    list_tables,
    run_pg_ingest,
)

__all__ = [
    "IngestError",
    "_build_queries",
    "_connect",
    "_quote_ident",
    "list_tables",
    "run_pg_ingest",
]
