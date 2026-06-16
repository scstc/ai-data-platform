"""数据集相关 schema(读模型),对照领域模型 docs/plan/03-架构设计.md §1。"""

from __future__ import annotations

from datetime import datetime

from app.schemas.common import CamelModel


class DatasetVersionRead(CamelModel):
    """数据集版本读模型(不可变快照)。"""

    id: str
    dataset_id: str
    version_no: int
    storage_uri: str
    stats_uri: str | None = None
    format: str
    rows: int | None = None
    size: int | None = None
    origin: str
    # hosted 版本指向的数据源 id(S3 凭证来源);受管版本为空(#18)
    source_datasource_id: str | None = None
    produced_by_job_id: str | None = None
    note: str | None = None
    created_at: datetime


class DatasetRead(CamelModel):
    """数据集读模型(元信息)。"""

    id: str
    name: str
    description: str | None = None
    data_type: str | None = None
    sensitivity_level: str | None = None
    # 分类(#15):受控分类库引用 id + 回填名(category_name 由路由批量取名填充)
    category_id: str | None = None
    category_name: str | None = None
    owner: str
    creator: str
    last_modifier: str | None = None
    valid_until: datetime | None = None
    created_at: datetime
    updated_at: datetime
    # 数据集是否含 hosted 版本(供前端「S3 托管」徽标/删除门控,#18)。
    # 由路由按版本聚合填充,非 ORM 字段,默认 False。
    hosted: bool = False


class HostS3Request(CamelModel):
    """外部 S3 数据托管登记入参(#18):把若干 S3 对象登记为受管数据集版本(不下载)。"""

    datasource_id: str
    bucket: str
    keys: list[str]
    name: str | None = None
    data_type: str | None = None
    # 分类(#15):受控分类库引用 id,可空,挂到创建的数据集上
    category_id: str | None = None


class PlatformHostRequest(CamelModel):
    """文件管理零拷贝接入入参:把平台 MinIO 对象登记为受管数据集版本(不下载)。"""

    bucket: str
    keys: list[str]
    name: str | None = None
    data_type: str | None = None
    category_id: str | None = None


class DatasetMemberRead(CamelModel):
    """数据集成员文件(manifest 数据集的一个媒体对象)读模型。"""

    name: str
    key: str
    bucket: str
    format: str
    size: int | None = None


class DatasetDetailRead(DatasetRead):
    """数据集详情:元信息 + 版本列表。"""

    versions: list[DatasetVersionRead] = []


class DatasetUpdate(CamelModel):
    """数据集元数据编辑入参:全部可选,只更新传入字段。"""

    name: str | None = None
    description: str | None = None
    data_type: str | None = None
    sensitivity_level: str | None = None
    # 分类(#15):受控分类库引用 id;显式传 null 清空分类
    category_id: str | None = None
    valid_until: datetime | None = None
