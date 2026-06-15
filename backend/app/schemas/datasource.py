"""数据源相关 schema，对照 frontend typings.d.ts 的 DataSource 系列类型。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from app.schemas.common import CamelModel

DataSourceType = Literal["s3", "hdfs", "database", "api"]
DbKind = Literal[
    "postgresql",
    "dameng",
    "goldendb",
    "kingbase",
    "gaussdb",
    "hologres",
    "sequoiadb",
    "hive",
    "doris",
]
DataSourceStatus = Literal["connected", "failed", "pending"]


class DataSourceRead(CamelModel):
    """数据源读模型（响应）。"""

    id: str
    name: str
    type: DataSourceType
    db_kind: DbKind | None = None
    status: DataSourceStatus
    config: dict[str, Any]
    description: str | None = None
    # 分类(#15):受控分类库引用 id + 回填名(category_name 由路由批量取名填充)
    category_id: str | None = None
    category_name: str | None = None
    creator: str
    created_at: datetime
    updated_at: datetime


class DataSourceCreate(CamelModel):
    """新建数据源入参。"""

    name: str
    type: DataSourceType
    db_kind: DbKind | None = None
    config: dict[str, Any]
    description: str | None = None
    # 分类(#15):受控分类库引用 id,可空
    category_id: str | None = None


class DataSourceUpdate(CamelModel):
    """更新数据源入参：全部可选，可显式改状态。"""

    name: str | None = None
    type: DataSourceType | None = None
    db_kind: DbKind | None = None
    config: dict[str, Any] | None = None
    description: str | None = None
    status: DataSourceStatus | None = None
    # 分类(#15):受控分类库引用 id;显式传 null 清空分类
    category_id: str | None = None


class TestConnectionParams(CamelModel):
    """测试连接入参。"""

    type: DataSourceType
    db_kind: DbKind | None = None
    config: dict[str, Any]


class TestConnectionResult(CamelModel):
    """测试连接结果（作为 {data, success} 的 data 部分）。"""

    success: bool
    latency_ms: int
    message: str
