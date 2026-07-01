# 数据湖实现说明

## 概述

根据 `docs/数据治理.md` 的要求，实现了**数据湖（ODS 原始数据层）**的核心逻辑。数据湖是所有外部异构数据源的统一入口，负责原样接入、版本固化、血缘追踪。

## 架构设计

### 核心概念

- **湖集分离**：数据湖存储原始数据（不可用于训练），数据集是加工后的成品（可直接训练）
- **外湖内集**：数据湖对接所有外部数据源，成品数据集仅对内
- **双版本机制**：
  - `source_v`（源头快照版本）：格式 `source_v年月日_批次_类型`，如 `source_v20260701_01_mysql`
  - `dataset_v`（加工成品版本）：数据集的版本号，如 `v1`, `v2`
- **全程溯源**：所有数据携带血缘字段，全链路透传

### 数据模型

#### DataLake（数据湖容器）
- 职责：记录数据源的元信息和接入配置
- 关键字段：
  - `source_category`：数据源类型（database/object_store/hdfs/local_upload/api_push）
  - `datasource_id`：关联的数据源 ID（可选）
  - `ingest_config`：接入配置（JSON）

#### DataLakeSnapshot（数据湖快照）
- 职责：一次数据源接入的不可变版本归档
- 关键字段：
  - `source_version`：源头快照版本号（如 `source_v20260701_01_mysql`）
  - `storage_uri`：原始数据存储位置（MinIO/S3 路径）
  - `storage_format`：存储格式（parquet/pdf/docx/png/mp4 等）
  - `data_category`：数据类型（database/document/image/audio/video/text）
  - `upload_channel`：上传渠道（oss/obs/minio/api/local/database）
  - `source_metadata`：差异化溯源字段（JSONB）

### 存储规范

1. **结构化数据**：统一导出为 **Parquet** 列式文件入湖，保留原始表结构
2. **文档/多媒体**：Word、PDF、Excel、PPT、jpg、mp3、mp4 等**原格式入湖**
3. **存储路径**：`data-lake/{lake_id}/{source_version}/data.parquet` 或 `data-lake/{lake_id}/{source_version}/{filename}`
4. **存储位置**：平台 MinIO 的 `uploads` 桶（配置项 `storage_minio_upload_bucket`）

## 已实现功能

### 1. 数据模型（ORM）
- `backend/app/models/data_lake.py`
  - `DataLake`：数据湖容器
  - `DataLakeSnapshot`：数据湖快照

### 2. 数据库迁移
- `backend/alembic/versions/0036_add_data_lake_tables.py`
  - 创建 `data_lakes` 表
  - 创建 `data_lake_snapshots` 表
  - 索引：`ix_dls_lake_id`, `ix_dls_ingest_task`
  - 唯一约束：`uq_lake_source_version`（lake_id + source_version）

### 3. 服务层
- `backend/app/services/data_lake.py`
  - `generate_source_version()`：生成源头快照版本号
  - `create_data_lake()`：创建数据湖容器
  - `ingest_to_lake_parquet()`：结构化数据入湖（Parquet 格式）
  - `ingest_to_lake_raw()`：文档/多媒体文件入湖（原格式）
  - `get_lake_by_id()`：根据 ID 获取数据湖
  - `list_lake_snapshots()`：列出数据湖的所有快照
  - `get_snapshot_by_version()`：根据版本号获取快照

### 4. Schema 层
- `backend/app/schemas/data_lake.py`
  - `DataLakeRead`：数据湖读模型
  - `DataLakeSnapshotRead`：快照读模型
  - `DataLakeCreate`：创建数据湖入参
  - `DataLakeUpdate`：更新数据湖入参
  - `DataLakeDetailRead`：数据湖详情（含快照列表）

### 5. API 路由
- `backend/app/api/v1/data_lakes.py`
  - `POST /data-lakes`：创建数据湖
  - `GET /data-lakes`：分页列出数据湖（支持名称模糊查询、数据源类型筛选）
  - `GET /data-lakes/{lake_id}`：获取数据湖详情（含快照列表）
  - `PATCH /data-lakes/{lake_id}`：更新数据湖元数据
  - `DELETE /data-lakes/{lake_id}`：删除数据湖
  - `GET /data-lakes/{lake_id}/snapshots`：分页列出快照
  - `GET /data-lake-snapshots/{snapshot_id}`：获取单个快照详情

### 6. 主应用注册
- `backend/app/main.py`：已注册 `data_lakes.router`

## 使用示例

### 1. 创建数据湖

```python
from app.services.data_lake import create_data_lake

# 创建数据库类型的数据湖
lake = await create_data_lake(
    db,
    name="财务系统数据湖",
    source_category="database",
    datasource_id="ds-abc123",  # 关联的数据源 ID
    description="财务系统 MySQL 数据湖",
)
```

### 2. 结构化数据入湖

```python
from app.services.data_lake import ingest_to_lake_parquet

# 将数据库查询结果入湖
data = [
    {"id": 1, "name": "张三", "amount": 1000},
    {"id": 2, "name": "李四", "amount": 2000},
]

snapshot = await ingest_to_lake_parquet(
    db,
    lake_id="lake-abc123",
    data=data,
    source_type="mysql",
    source_metadata={
        "db_schema": "finance",
        "db_table": "transactions",
        "db_engine": "MySQL 8.0",
    },
    upload_channel="database",
)

# snapshot.source_version 格式：source_v20260701_01_mysql
# snapshot.storage_uri 格式：s3://uploads/data-lake/lake-abc123/source_v20260701_01_mysql/data.parquet
```

### 3. 文档/多媒体文件入湖

```python
from app.services.data_lake import ingest_to_lake_raw

# 将文档文件入湖
with open("report.pdf", "rb") as f:
    file_content = f.read()

snapshot = await ingest_to_lake_raw(
    db,
    lake_id="lake-abc123",
    file_content=file_content,
    original_filename="report.pdf",
    data_category="document",
    upload_channel="local",
    source_metadata={
        "original_filename": "report.pdf",
        "upload_user": "admin",
    },
)

# snapshot.storage_uri 格式：s3://uploads/data-lake/lake-abc123/source_v20260701_01_pdf/report.pdf
```

### 4. 查询快照

```python
from app.services.data_lake import list_lake_snapshots, get_snapshot_by_version

# 列出数据湖的所有快照
snapshots = await list_lake_snapshots(db, lake_id="lake-abc123", limit=100)

# 根据版本号获取快照
snapshot = await get_snapshot_by_version(
    db, 
    lake_id="lake-abc123", 
    source_version="source_v20260701_01_mysql"
)
```

## 下一步工作

数据湖的核心逻辑已实现，接下来需要：

### 1. 改造数据源接入流程
- 修改 `backend/app/services/connectors/` 下的连接器，先将数据入湖，再从湖中抽取到数据集
- 修改 `backend/app/services/landing.py`，添加数据湖读取逻辑

### 2. 添加血缘追踪字段
- 在数据集 JSONL 中注入源头血缘元数据：
  ```json
  {
    "source_version": "source_v20260701_01_mysql",
    "process_version": "dataset_v1",
    "source_category": "database",
    "upload_channel": "database",
    "chunk_uuid": "唯一分片ID",
    "dj_task_id": "Data-Juicer任务ID"
  }
  ```

### 3. 实现数据抽取层
- 从数据湖中按需筛选、抽取指定版本的原始数据
- 完成格式解析与初步结构化（Parquet → 文本、文档 → 文本块、图片 → 描述）
- 生成统一中间 JSONL 分片文件

### 4. 集成 Data-Juicer 加工层
- 中间 JSONL 进入 Data-Juicer 流水线
- 算子加工时保留血缘元字段，全程透传

### 5. 前端页面
- 数据湖管理页面（列表、新建、编辑、删除）
- 数据湖详情页面（显示快照列表、血缘追踪）
- 数据接入流程改造（先入湖、再抽取）

## 配置项

数据湖依赖平台 MinIO 配置（`.env` 或环境变量）：

```bash
# MinIO 端点
STORAGE_MINIO_ENDPOINT=http://10.60.1.60:9000

# MinIO 访问凭证
STORAGE_MINIO_ACCESS_KEY=minioadmin
STORAGE_MINIO_SECRET_KEY=minioadmin

# 上传桶名（数据湖使用此桶）
STORAGE_MINIO_UPLOAD_BUCKET=uploads
```

## 注意事项

1. **快照不可变**：一经写入永不修改，任何"更新"都是产出新快照
2. **原始数据只读归档**：数据湖只读取外部数据，永不回写或删除
3. **版本固化**：`source_version` 格式固定，确保全局唯一
4. **MinIO 依赖**：数据湖依赖平台 MinIO，确保配置正确
5. **物理文件保留**：删除数据湖/快照记录时，MinIO 中的物理文件保留（需手动清理）

## 文件清单

```
backend/
├── alembic/versions/
│   └── 0036_add_data_lake_tables.py       # 数据库迁移
├── app/
│   ├── models/
│   │   ├── data_lake.py                    # ORM 模型
│   │   └── __init__.py                     # 导出 DataLake/DataLakeSnapshot
│   ├── schemas/
│   │   └── data_lake.py                    # Pydantic Schema
│   ├── services/
│   │   └── data_lake.py                    # 服务层
│   ├── api/v1/
│   │   └── data_lakes.py                   # API 路由
│   └── main.py                              # 注册路由
└── app/core/ids.py                          # UUID7 生成（已存在）
```

## 测试

TODO：编写单元测试和集成测试

```bash
# 运行测试
cd backend
uv run pytest tests/test_data_lake.py -v
```
