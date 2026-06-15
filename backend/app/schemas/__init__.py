"""Pydantic schema 汇总导出。"""

from app.schemas.ai import (
    GeneratedTaskConfig,
    GenerateTaskRequest,
    InferredSchema,
    InferSchemaRequest,
    QaAnswer,
    QaRequest,
    SchemaField,
)
from app.schemas.category import CategoryCreate, CategoryRead, CategoryUpdate
from app.schemas.common import CamelModel, PageResponse
from app.schemas.datasource import (
    DataSourceCreate,
    DataSourceRead,
    DataSourceStatus,
    DataSourceType,
    DataSourceUpdate,
    DbKind,
    TestConnectionParams,
    TestConnectionResult,
)
from app.schemas.ingest_task import (
    IngestSchedule,
    IngestTaskCreate,
    IngestTaskRead,
    IngestTaskStatus,
)
from app.schemas.upload import UploadRecordRead

__all__ = [
    # common
    "CamelModel",
    "PageResponse",
    # category
    "CategoryCreate",
    "CategoryRead",
    "CategoryUpdate",
    # datasource
    "DataSourceCreate",
    "DataSourceRead",
    "DataSourceStatus",
    "DataSourceType",
    "DataSourceUpdate",
    "DbKind",
    "TestConnectionParams",
    "TestConnectionResult",
    # ingest task
    "IngestSchedule",
    "IngestTaskCreate",
    "IngestTaskRead",
    "IngestTaskStatus",
    # upload
    "UploadRecordRead",
    # ai
    "GeneratedTaskConfig",
    "GenerateTaskRequest",
    "InferredSchema",
    "InferSchemaRequest",
    "QaAnswer",
    "QaRequest",
    "SchemaField",
]
