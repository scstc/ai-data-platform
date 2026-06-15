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
    business_category: str | None = None
    owner: str
    creator: str
    last_modifier: str | None = None
    valid_until: datetime | None = None
    created_at: datetime
    updated_at: datetime


class DatasetDetailRead(DatasetRead):
    """数据集详情:元信息 + 版本列表。"""

    versions: list[DatasetVersionRead] = []


class DatasetUpdate(CamelModel):
    """数据集元数据编辑入参:全部可选,只更新传入字段。"""

    name: str | None = None
    description: str | None = None
    data_type: str | None = None
    sensitivity_level: str | None = None
    business_category: str | None = None
    valid_until: datetime | None = None
