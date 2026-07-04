"""数据湖相关 schema（ODS 原始数据层）。

数据湖职责：
- 原样接入：所有外部数据源数据原样存储，不加工
- 版本固化：每次接入产生不可变的 source_v 快照
- 血缘追踪：记录完整的数据源元信息
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import model_validator

from app.schemas.common import CamelModel, UtcDateTime


class DataLakeRead(CamelModel):
    """数据湖读模型（多源汇聚容器）。"""

    id: str
    name: str
    description: str | None = None
    owner: str
    creator: str
    dept_id: str | None = None
    created_at: UtcDateTime
    updated_at: UtcDateTime


class DataLakeSnapshotRead(CamelModel):
    """数据湖快照读模型（不可变版本归档，快照层承载类型/来源语义）。"""

    id: str
    lake_id: str
    source_version: str  # source_v年月日_批次_类型（如 source_v20260701_01_mysql）
    storage_uri: str  # s3://bucket/data-lake/lake-xxx/source_vXXX/data.parquet
    storage_format: str  # parquet | pdf | docx | png | mp4 等
    data_category: str  # database | document | image | audio | video | text
    upload_channel: str  # oss | obs | minio | api | local | database
    datasource_id: str | None = None  # 本次接入的数据源（本地/API 为空）
    source_metadata: dict[str, Any] | None = None
    rows: int | None = None
    size: int | None = None
    ingest_task_id: str | None = None
    created_at: UtcDateTime


class DataLakeCreate(CamelModel):
    """创建数据湖入参（湖=纯容器，不绑定类型）。"""

    name: str
    description: str | None = None


class DataLakeUpdate(CamelModel):
    """更新数据湖元数据入参（全部可选）。"""

    name: str | None = None
    description: str | None = None


class IngestToLakeParquetRequest(CamelModel):
    """结构化数据入湖请求（Parquet 格式）。"""

    lake_id: str
    data: list[dict[str, Any]]  # 结构化数据（list of dict）
    source_type: str  # mysql | pg | oceanbase 等
    source_metadata: dict[str, Any] | None = None
    upload_channel: str = "database"
    ingest_task_id: str | None = None


class IngestToLakeRawRequest(CamelModel):
    """文档/多媒体文件入湖请求（原格式）。"""

    lake_id: str
    original_filename: str
    data_category: Literal["document", "image", "audio", "video", "text"]
    upload_channel: Literal["oss", "obs", "minio", "api", "local"] = "local"
    source_metadata: dict[str, Any] | None = None
    ingest_task_id: str | None = None


class SnapshotRenameRequest(CamelModel):
    """快照改名入参(仅改展示文件名,写 source_metadata.original_filename)。"""

    filename: str


class DataLakeDetailRead(DataLakeRead):
    """数据湖详情：元信息 + 快照列表。"""

    snapshots: list[DataLakeSnapshotRead] = []


class ExtractToDatasetRequest(CamelModel):
    """从湖抽取生成数据集入参(治理改造契约地基)。

    目标数据集二选一(model_validator 拒绝混合或全空):
    - 新建:``dataset_name`` 填,``dataset_id`` 空 → 建新数据集
    - 追加到已有:``dataset_id`` 填,``dataset_name`` 空 → 落进该数据集当前 draft 版本
    """

    snapshot_ids: list[str]
    dataset_id: str | None = None
    dataset_name: str | None = None
    description: str | None = None
    # 字段映射模板(可选):key=快照ID, value=模板字符串
    # 如 {"snap-123": "用户提问：{question}，客服回答：{answer}"}
    # 适用于表格类快照(database/tabular:数据库表、CSV、Excel等)。
    field_mapping: dict[str, str] | None = None

    @model_validator(mode="after")
    def _check_target_one_of(self) -> ExtractToDatasetRequest:
        if bool(self.dataset_id) == bool(self.dataset_name):
            raise ValueError("dataset_id 与 dataset_name 必须二选一")
        return self
