"""连接器层(数据接入重构 §4):各来源 → 字节/规范记录 → land_records。

本包是接入方式 5 项(S3/HDFS/手动上传/API 推送/采集任务)的统一落点。
契约见 ``base.py``(Connector Protocol + ConnectorNotReady + StructuralStub)。

``REGISTRY`` 按 ``(type, db_kind)`` 把数据源派发到具体连接器实例;``resolve`` 是
唯一查询入口,供 3 个分发点(datasources test/list、ingest_tasks rerun)替换原来的
if/elif 硬判(§4.8)。未命中返回 None,由调用方给诚实的 not-ready 文案。

注:连接器实例是无状态的(连接参数都从 config 入参取),全局单例复用即可。
"""

from __future__ import annotations

from app.services.connectors.base import Connector
from app.services.connectors.hdfs import HdfsConnector
from app.services.connectors.mysql import MysqlConnector
from app.services.connectors.objectstore import S3Connector
from app.services.connectors.pg import PgConnector
from app.services.connectors.proprietary import (
    DamengConnector,
    DorisConnector,
    HiveConnector,
    SequoiaConnector,
)
from app.services.connectors.push import PushConnector

# (type, db_kind) → Connector 实例。database 类按 db_kind 细分;
# s3/hdfs/api 无 db_kind,键的第二元用 None。
REGISTRY: dict[tuple[str, str | None], Connector] = {
    ("database", "postgresql"): PgConnector(),
    ("database", "hologres"): PgConnector(),  # PG 线协议,复用 asyncpg(品牌承诺级)
    ("database", "kingbase"): PgConnector(),  # 同上
    ("database", "gaussdb"): PgConnector(),  # 同上(端口由 config 提供)
    ("database", "goldendb"): MysqlConnector(),  # MySQL 协议(有本地 MySQL 可测)
    ("database", "doris"): DorisConnector(),  # 降档:结构就绪/承诺级
    ("database", "dameng"): DamengConnector(),
    ("database", "sequoiadb"): SequoiaConnector(),
    ("database", "hive"): HiveConnector(),
    ("s3", None): S3Connector(),
    ("hdfs", None): HdfsConnector(),
    ("api", None): PushConnector(),
}


def resolve(type_: str, db_kind: str | None) -> Connector | None:
    """按 (type, db_kind) 查连接器实例;未命中返回 None(由调用方诚实降级)。

    - database 类:按 db_kind 命中具体品牌连接器;db_kind 为 None / 未知 → None。
    - s3/hdfs/api 类:db_kind 入参被忽略,统一用 (type, None) 命中。
    """
    if type_ in ("s3", "hdfs", "api"):
        return REGISTRY.get((type_, None))
    return REGISTRY.get((type_, db_kind))


__all__ = ["REGISTRY", "Connector", "resolve"]
