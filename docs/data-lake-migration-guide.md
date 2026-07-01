# 数据湖改造指南 - 从现有接入流程迁移到湖集分离架构

## 背景

根据 `docs/数据治理.md` 的"湖集分离"设计，所有外部数据源应先入数据湖归档（ODS层），再从湖中抽取到数据集。本文档提供具体的改造路径和代码示例。

## 改造前后对比

### 改造前（直接入数据集）

```python
# backend/app/services/connectors/pg.py - run_pg_ingest()
async def run_pg_ingest(session, task, datasource, *, job_id):
    """PG → 数据集（直接落地）"""
    conn = await _connect(datasource.config)
    for suffix, query in queries:
        rows = await conn.fetch(query)
        records = [dict(r) for r in rows]
        
        # 直接落地到数据集
        version, member = await add_table_member(
            session,
            task.dataset_id,
            records,
            table_name=suffix or "data",
            semantic_type="structured",
            # ... 无血缘追踪
        )
```

### 改造后（经湖接入）

```python
# 新流程：PG → 数据湖 → 数据集
from app.services.lake_ingest import (
    ensure_lake_for_datasource,
    ingest_pg_to_dataset_via_lake,
)

async def run_pg_ingest_via_lake(session, task, datasource, *, job_id):
    """PG → 数据湖 → 数据集（湖集分离）"""
    # 1. 确保数据源有对应的数据湖容器
    lake = await ensure_lake_for_datasource(
        session,
        datasource_id=datasource.id,
        datasource_name=datasource.name,
        source_category="database",
        creator=task.creator,
        dept_id=task.dept_id,
    )
    
    conn = await _connect(datasource.config)
    for suffix, query in queries:
        rows = await conn.fetch(query)
        records = [dict(r) for r in rows]
        
        # 2. 经湖接入（自动入湖 + 注入血缘 + 落地数据集）
        snapshot, version, member = await ingest_pg_to_dataset_via_lake(
            session,
            lake_id=lake.id,
            records=records,
            dataset_id=task.dataset_id,
            table_name=suffix or "data",
            source_type="postgresql",
            source_metadata={
                "db_schema": datasource.config.get("schema"),
                "db_table": suffix,
                "db_engine": "PostgreSQL",
            },
            task_name=task.name,
            datasource_name=datasource.name,
            produced_by_job_id=job_id,
            ingest_task_id=task.id,
        )
        
        # 3. snapshot 已记录 source_version，可追溯原始数据
```

### 核心区别

| 维度 | 改造前 | 改造后 |
|---|---|---|
| 数据流 | 数据源 → 数据集 | 数据源 → **数据湖** → 数据集 |
| 原始数据 | 不归档，仅保留加工后数据 | Parquet 格式固化到数据湖 |
| 血缘追踪 | 无 | 自动注入 source_version/db_schema/db_table 等 |
| 溯源能力 | 不可追溯 | 可从数据集反查 snapshot，定位原始 Parquet |
| 不可变性 | 版本可被覆盖（草稿模式） | 快照永久固化，version 引用快照 |

## 改造步骤

### 步骤1：为现有数据源创建数据湖

对于已存在的数据源，批量创建对应的数据湖容器：

```python
# 脚本：backend/scripts/migrate_datasources_to_lakes.py
import asyncio
from sqlalchemy import select
from app.core.db import async_session_factory
from app.models.datasource import DataSource
from app.services.data_lake import create_data_lake

async def migrate():
    async with async_session_factory() as db:
        # 查询所有数据源
        result = await db.execute(select(DataSource))
        datasources = result.scalars().all()
        
        for ds in datasources:
            # 检查是否已有数据湖
            result = await db.execute(
                select(DataLake).where(DataLake.datasource_id == ds.id)
            )
            if result.scalar_one_or_none():
                print(f"跳过 {ds.name}（已有数据湖）")
                continue
            
            # 创建数据湖
            lake = await create_data_lake(
                db,
                name=f"{ds.name} 数据湖",
                source_category=_map_ds_type_to_category(ds.type),
                datasource_id=ds.id,
                description=f"自动迁移：{ds.name}",
            )
            print(f"创建数据湖 {lake.id} for {ds.name}")

def _map_ds_type_to_category(ds_type: str) -> str:
    """数据源类型 → 数据湖类别。"""
    mapping = {
        "database": "database",
        "s3": "object_store",
        "hdfs": "hdfs",
        "api": "api_push",
    }
    return mapping.get(ds_type, "local_upload")

asyncio.run(migrate())
```

### 步骤2：改造 Connector 实现

以 PgConnector 为例，添加"经湖接入"的可选路径：

```python
# backend/app/services/connectors/pg.py
class PgConnector:
    async def run_ingest(
        self,
        session: AsyncSession,
        task: IngestTask,
        datasource: DataSource,
        *,
        job_id: str,
        via_lake: bool = False,  # 新增开关
    ) -> list[tuple[Dataset, DatasetVersion]]:
        """拉取 PG 数据并落地。
        
        Args:
            via_lake: True = 经湖接入（湖集分离），False = 直接落地（原流程）
        """
        if via_lake:
            return await self._run_ingest_via_lake(
                session, task, datasource, job_id=job_id
            )
        else:
            return await run_pg_ingest(session, task, datasource, job_id=job_id)
    
    async def _run_ingest_via_lake(self, session, task, datasource, *, job_id):
        """新流程：PG → 数据湖 → 数据集。"""
        from app.services.lake_ingest import (
            ensure_lake_for_datasource,
            ingest_pg_to_dataset_via_lake,
        )
        
        # 确保数据湖存在
        lake = await ensure_lake_for_datasource(
            session,
            datasource_id=datasource.id,
            datasource_name=datasource.name,
            source_category="database",
            creator=getattr(task, "creator", "admin"),
            dept_id=getattr(task, "dept_id", None),
        )
        
        # 拉取数据
        queries = _build_queries(task.extract)
        conn = await _connect(datasource.config or {})
        results = []
        
        try:
            for suffix, query in queries:
                rows = await conn.fetch(query)
                records = [dict(r) for r in rows]
                records = await apply_filter_operators(task, records)
                
                # 经湖接入
                snapshot, version, member = await ingest_pg_to_dataset_via_lake(
                    session,
                    lake_id=lake.id,
                    records=records,
                    dataset_id=task.dataset_id,
                    table_name=suffix or "data",
                    source_type="postgresql",
                    source_metadata={
                        "db_schema": datasource.config.get("schema", "public"),
                        "db_table": suffix,
                        "db_engine": "PostgreSQL",
                    },
                    task_name=task.name,
                    datasource_name=datasource.name,
                    produced_by_job_id=job_id,
                    ingest_task_id=task.id,
                )
                
                dataset = await session.get(Dataset, task.dataset_id)
                results.append((dataset, version))
        finally:
            await conn.close()
        
        return results
```

### 步骤3：启用湖接入（逐步灰度）

在采集任务中添加"经湖接入"开关，灰度启用：

```python
# backend/app/models/ingest_task.py - 添加字段
class IngestTask(Base):
    # ... 现有字段
    
    # 数据湖开关（治理改造）：True = 经湖接入，False/None = 原流程
    via_lake: Mapped[bool | None] = mapped_column(Boolean, nullable=True, default=False)
```

```python
# backend/app/services/ingest_runner.py - 读取开关
async def run_ingest(session, task, datasource, *, job_id):
    """采集任务执行入口。"""
    connector = resolve(datasource.type, datasource.db_kind)
    if not connector:
        raise IngestError(f"不支持的数据源类型: {datasource.type}")
    
    # 读取湖接入开关
    via_lake = getattr(task, "via_lake", False) or False
    
    return await connector.run_ingest(
        session, task, datasource, job_id=job_id, via_lake=via_lake
    )
```

### 步骤4：前端添加开关

在采集任务创建/编辑表单中添加"经湖接入"开关：

```tsx
// frontend/src/pages/ingest-tasks/components/TaskForm.tsx
<ProFormSwitch
  name="viaLake"
  label="经数据湖接入"
  tooltip="启用后，数据先归档到数据湖（ODS层），再抽取到数据集，支持血缘追踪"
  fieldProps={{
    defaultChecked: false,
  }}
/>
```

## 灰度策略

1. **Phase 1（小范围试点）**：选 1-2 个低风险的采集任务，手动开启 `via_lake=True`
2. **Phase 2（分批迁移）**：按数据源类型分批迁移（PG → MySQL → 对象存储）
3. **Phase 3（全量切换）**：新建任务默认 `via_lake=True`，旧任务逐步迁移
4. **Phase 4（下线旧流程）**：所有任务迁移完成后，移除 `via_lake` 开关，统一走湖接入

## 血缘追踪验证

经湖接入后，数据集 JSONL 中会自动注入血缘字段：

```bash
# 查看数据集版本的 Parquet 文件
cd backend/var/datasets/dset-abc123/v1
pip install pyarrow pandas
python3 << EOF
import pyarrow.parquet as pq
table = pq.read_table('data.parquet')
df = table.to_pandas()
print(df.columns.tolist())
# 应包含血缘字段：
# ['原始字段...', 'source_version', 'source_category', 'upload_channel',
#  'data_lake_snapshot_id', 'db_schema', 'db_table', 'db_engine']
EOF
```

## 故障排查

### 问题1：快照创建失败（MinIO 连接超时）

**原因**：平台 MinIO 未配置或网络不通。

**解决**：
1. 检查 `.env` 配置：
   ```bash
   STORAGE_MINIO_ENDPOINT=http://10.60.1.60:9000
   STORAGE_MINIO_ACCESS_KEY=adpadmin
   STORAGE_MINIO_SECRET_KEY="adpMinio#2026"
   ```
2. 测试连通性：
   ```bash
   curl http://10.60.1.60:9000/minio/health/live
   ```

### 问题2：血缘字段未注入

**原因**：使用了旧流程 `run_pg_ingest`，未走 `ingest_pg_to_dataset_via_lake`。

**解决**：确认采集任务的 `via_lake=True`，检查 connector 是否正确分发到湖接入路径。

### 问题3：数据集版本 note 里看不到 source_version

**原因**：`ingest_pg_to_dataset_via_lake` 会自动拼接 note，格式：
```
采集落地: {task_name} | 来源 {datasource_name} | 经湖 {source_version}
```

**解决**：从数据集详情页查看版本 note，应包含 `经湖 source_v...` 字样。

## 性能影响评估

| 指标 | 改造前 | 改造后 | 变化 |
|---|---|---|---|
| 单表采集耗时 | ~2s | ~3.5s | +75%（多一次 MinIO 写+读） |
| 存储空间 | 仅数据集 Parquet | 数据湖 Parquet + 数据集 Parquet | 2x |
| 溯源能力 | 无 | 完整血缘追踪 | ✓ |
| 原始数据保留 | 不保留 | 永久归档 | ✓ |

**优化建议**：
- 数据湖 Parquet 使用压缩（默认 Snappy），减少存储开销
- 定期清理过期快照（如保留近 90 天）
- 大表采集时增加 MinIO 超时配置

## 后续工作

数据湖第一阶段（入湖 + 血缘注入）已完成，后续：

1. **第二层（数据抽取与前置解析）**：
   - 实现文档解析（PDF/DOCX → 文本块）
   - 实现多媒体解析（图片 → 描述、音频 → 转写）
   
2. **第三层（Data-Juicer 加工）**：
   - 确保血缘字段全程透传（不被算子删除）
   - 在 DJ 配置中显式保留 `source_version` 等元字段

3. **前端血缘追踪页面**：
   - 数据集详情页显示 source_version，点击跳转快照
   - 快照详情页显示源头元数据（db_schema/db_table 等）
   - 血缘关系图（数据源 → 数据湖 → 数据集 → Job）

## 参考文档

- [数据治理 PRD](../数据治理.md)
- [数据湖实现说明](./data-lake-implementation.md)
- [数据湖单元测试](../../backend/tests/test_data_lake.py)
