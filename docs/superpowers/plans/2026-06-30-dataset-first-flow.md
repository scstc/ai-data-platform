# 数据集优先流程改造 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把平台从「上传/采集自动建数据集」倒置为「先建数据集 → 上传/采集选数据集落入」,并让一个版本承载多个表成员(多 parquet)。

**Architecture:** 新增无 FK 子表 `dataset_version_tables`(仿 `job_inputs`)承载多表成员;拆 `land_records` 为 `create_dataset` + `add_table_member`(draft 可变 / published 冻结后开新版本);四个入口(本地上传/采集/托管/推送)从建集改为必选已有数据集;读路径成员化。

**Tech Stack:** Python 3.12 / FastAPI / SQLAlchemy async / Alembic / PostgreSQL(adp_gov)/ MinIO(s3://)/ pyarrow;前端 Umi Max v4 + antd v6 + ProComponents。

## Global Constraints

- **目标库 = adp_gov**(dbx 连接 `PostgreSQL_adp_gov`,`10.60.1.60:55433`);迁移 `down_revision` 接 `0033_add_reproducibility_columns`(当前 head);**不动 dev adp**。
- **无 DB 级外键**:所有跨表链接用普通 `String` 列(本仓库弱关联约定)。
- **版本不可变**:`dataset_versions` 无 `updated_at`;只有 `publish_status='draft'` 的最新版本可被改成员;`published` 版本只读,再落自动开 v+1。
- **后端无热重载**:改 `.py` 后手动重启 `uvicorn`(用 venv `uvicorn.exe`,不用 `uv run`,见 CLAUDE.md)。
- **迁移可回退**:每个 `upgrade()` 必须有对称 `downgrade()`;新列 nullable + server_default。
- **提交规范**:Conventional Commits;后端 `uv run ruff check .`(E,F,I,UP,B;line-length 88)必过。
- **前端契约勿手改**:改后端后跑 `npm run openapi` 重生 `src/services/ant-design-pro/`;`api.ts`(`src/services/data-platform/`)可手写。
- **ID 前缀约定**:dataset=`dset-`、version=`dsv-`、新成员表=`dvt-`,均 `前缀 + 6 hex`(复用 landing 里的 `_new_*_id` 风格)。
- **测试**:`cd backend && uv run pytest <file> -q`;改 landing/路由/权限后跑 CLAUDE.md 冒烟集。
- **测试 session fixture**:Task 1 在 `tests/conftest.py` 新增 `db_session` fixture(基于现有 `session_factory`);后端非 client 级单测统一用它。现有 fixtures:`session_factory`(工厂)、`client`(httpx)、`engine`(函数级,`Base.metadata.create_all` 建表,**非 alembic**)、`seed_users`。
- **ACL 写权校验**:用现有 `app.services.dataset_acl.can_access(session, user, dataset_id, "edit")`(无 `can_write` 函数;`can_access` 匿名放行、级别 ≥ required 通过)。
- **测试库 ≠ 迁移**:测试库 schema 由 `Base.metadata.create_all` 从模型直建,**不跑 alembic**。故迁移(0034)的结构/回填验证须在真实库(adp_gov 或一次性 scratch 库)上跑 `alembic upgrade/downgrade`,不能靠 pytest 测试库断言。

---

## Phase 1 — 数据模型 + 迁移 0034

### Task 1: `DatasetVersionTable` ORM 模型

**Files:**
- Create: `backend/app/models/dataset_version_table.py`
- Modify: `backend/app/models/__init__.py`(导出新模型)
- Test: `backend/tests/unit/test_dataset_version_table_model.py`

**Interfaces:**
- Produces: `DatasetVersionTable` ORM 类,列 `id, dataset_version_id, table_name, storage_uri, format, rows, size, schema_snapshot, schema_variant, created_at`;约束 `uq_dvt_version_table(dataset_version_id, table_name)` + `Index ix_dvt_version`。

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/unit/test_dataset_version_table_model.py
from app.models import DatasetVersionTable


def test_dvt_table_and_constraints():
    t = DatasetVersionTable.__table__
    assert t.name == "dataset_version_tables"
    cols = set(t.columns.keys())
    assert {
        "id", "dataset_version_id", "table_name", "storage_uri",
        "format", "rows", "size", "schema_snapshot", "schema_variant",
        "created_at",
    } <= cols
    cons = {c.name for c in t.constraints if c.name}
    assert "uq_dvt_version_table" in cons
    idx = {i.name for i in t.indexes}
    assert "ix_dvt_version" in idx
    # 弱关联约定:无外键
    assert not t.foreign_keys
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/unit/test_dataset_version_table_model.py -q`
Expected: FAIL — `ImportError: cannot import name 'DatasetVersionTable'`

- [ ] **Step 3: Write the model**

```python
# backend/app/models/dataset_version_table.py
"""数据集版本-表成员 ORM 模型(一个版本承载多个表/parquet 成员)。

仿 job_inputs 的无 FK 弱关联子表:一行 = 一张表 = 一个 parquet 文件。
版本级 storage_uri/format/rows/size/schema_snapshot 退化为跨成员 rollup;
单表数据集 = 恰好一个成员,旧字段语义对单成员仍成立。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, Index, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class DatasetVersionTable(Base):
    """版本内的一个表成员(parquet/jsonl 文件)。"""

    __tablename__ = "dataset_version_tables"

    __table_args__ = (
        UniqueConstraint(
            "dataset_version_id", "table_name", name="uq_dvt_version_table"
        ),
        Index("ix_dvt_version", "dataset_version_id"),
    )

    # 主键形如 "dvt-" + 6 位 hex
    id: Mapped[str] = mapped_column(String, primary_key=True)
    # 所属版本(纯引用,无 FK)
    dataset_version_id: Mapped[str] = mapped_column(String, nullable=False)
    # 表名/成员名,版本内唯一
    table_name: Mapped[str] = mapped_column(String, nullable=False)
    # 单成员文件位置 s3://<bucket>/<dataset_id>/v<n>/<table>.parquet
    storage_uri: Mapped[str] = mapped_column(String, nullable=False)
    format: Mapped[str] = mapped_column(String, nullable=False, default="parquet")
    rows: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    size: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    schema_snapshot: Mapped[list[dict[str, Any]] | None] = mapped_column(
        JSONB, nullable=True
    )
    # 成员级 schema 变体(默认继承版本级)
    schema_variant: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )
```

在 `backend/app/models/__init__.py` 现有导出列表里加入(对齐既有风格):

```python
from app.models.dataset_version_table import DatasetVersionTable  # noqa: F401
```
并把 `"DatasetVersionTable"` 加进该文件的 `__all__`(若存在)。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/unit/test_dataset_version_table_model.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/models/dataset_version_table.py backend/app/models/__init__.py backend/tests/unit/test_dataset_version_table_model.py
git commit -m "feat(models): dataset_version_tables 多表成员模型"
```

- [ ] **Step 6: Add `db_session` fixture to conftest**

Phase 2+ 的 landing/服务级单测需要一个 async session fixture。`tests/conftest.py` 现有 `session_factory` 但无直接 `db_session`。追加(放在 `session_factory` 之后):

```python
# backend/tests/conftest.py
@pytest_asyncio.fixture
async def db_session(session_factory):
    """函数级 async session(基于测试 engine);服务层单测用。"""
    async with session_factory() as session:
        yield session
```

- [ ] **Step 7: Commit fixture**

```bash
git add backend/tests/conftest.py
git commit -m "test: 新增 db_session fixture(服务层单测)"
```

<!-- ANCHOR_AFTER_TASK1 -->

### Task 2: 迁移 0034 — 建表 + `ingest_tasks.dataset_id` NOT NULL + `upload_records.dataset_id` + 回填

**Files:**
- Create: `backend/alembic/versions/0034_dataset_first_multitable.py`
- Verify: 真实库 `alembic upgrade/downgrade`(**不**用 pytest 测试库——测试库走 `create_all` 不跑 alembic,见 Global Constraints)

**Interfaces:**
- Consumes: `DatasetVersionTable`(Task 1)。
- Produces: 物理表 `dataset_version_tables`;`ingest_tasks.dataset_id` 改 NOT NULL;`upload_records.dataset_id`(nullable);每个现存 `dataset_versions` 回填一行成员(`table_name='data'`)。

**注意**:迁移面向 adp_gov,`down_revision='0033_add_reproducibility_columns'`。回填 SQL 用 `op.execute`,不 import ORM(迁移须自洽)。`ingest_tasks` 现存行 `dataset_id` 可能为 NULL;改 NOT NULL 前先给 NULL 行回填一个占位数据集 id —— 但 adp_gov 现存采集任务的产物数据集无直接列可查(经 Job 间接关联),**回填策略**:对 `dataset_id IS NULL` 的 `ingest_tasks`,用其最近一次 ingest Job 产出版本的 `dataset_id`;查不到的填空串占位并记日志(adp_gov 克隆库可接受)。

- [ ] **Step 1: 验证脚本(先建,确认会失败)**

迁移结构与回填验证**不能用 pytest 测试库**(`Base.metadata.create_all` 直建,不跑 alembic,既测不到迁移也测不到回填)。改用一次性 scratch 库脚本:

```bash
# backend/scripts/verify_0034.sh —— 在一次性 scratch 库上跑真实迁移
set -euo pipefail
SCRATCH_URL="${SCRATCH_DATABASE_URL:?需指向一次性 scratch 库,勿用 dev adp}"
export DATABASE_URL="$SCRATCH_URL"
.venv/Scripts/alembic.exe upgrade head
# 断言:表存在 + ingest_tasks.dataset_id NOT NULL + 无版本缺成员
.venv/Scripts/python.exe - <<'PY'
import os, asyncio
from sqlalchemy import inspect, text, create_engine
e = create_engine(os.environ["DATABASE_URL"].replace("+asyncpg", ""))
with e.connect() as c:
    insp = inspect(c)
    assert "dataset_version_tables" in insp.get_table_names()
    it = {col["name"]: col for col in insp.get_columns("ingest_tasks")}
    assert it["dataset_id"]["nullable"] is False, "dataset_id 应为 NOT NULL"
    assert "dataset_id" in {col["name"] for col in insp.get_columns("upload_records")}
    miss = c.execute(text(
        "SELECT count(*) FROM dataset_versions dv WHERE NOT EXISTS "
        "(SELECT 1 FROM dataset_version_tables t WHERE t.dataset_version_id=dv.id)"
    )).scalar()
    assert miss == 0, f"{miss} 个版本未回填成员"
print("0034 upgrade OK")
PY
# 回退验证
.venv/Scripts/alembic.exe downgrade -1
.venv/Scripts/alembic.exe upgrade head
echo "0034 downgrade/upgrade roundtrip OK"
```

- [ ] **Step 2: Run script to verify it fails**

Run: `cd backend && SCRATCH_DATABASE_URL=<scratch> bash scripts/verify_0034.sh`
Expected: FAIL — `alembic upgrade head` 找不到 0034(迁移还没建)。
> scratch 库:可在 `10.60.1.60:55433` 上 `CREATE DATABASE adp_scratch_0034`(克隆少量 dev 数据或空库均可),用完 `DROP`。**绝不指向 dev adp**。

- [ ] **Step 3: Write the migration**

```python
# backend/alembic/versions/0034_dataset_first_multitable.py
"""dataset-first inversion: multitable members + authoritative task dataset_id

Revision ID: 0034_dataset_first_multitable
Revises: 0033_add_reproducibility_columns
Create Date: 2026-06-30 23:00:00.000000

数据集优先流程改造 阶段1:
- 新增 dataset_version_tables(版本多表成员,无 FK,仿 job_inputs)
- ingest_tasks.dataset_id 转权威(NOT NULL,先回填)
- upload_records.dataset_id(nullable,可追溯)
- 回填:每个现存 dataset_versions 生成一行成员(table_name='data')
面向 adp_gov 克隆库,可回退。
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "0034_dataset_first_multitable"
down_revision: Union[str, None] = "0033_add_reproducibility_columns"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "dataset_version_tables",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("dataset_version_id", sa.String(), nullable=False),
        sa.Column("table_name", sa.String(), nullable=False),
        sa.Column("storage_uri", sa.String(), nullable=False),
        sa.Column("format", sa.String(), nullable=False, server_default="parquet"),
        sa.Column("rows", sa.BigInteger(), nullable=True),
        sa.Column("size", sa.BigInteger(), nullable=True),
        sa.Column("schema_snapshot", postgresql.JSONB(), nullable=True),
        sa.Column("schema_variant", sa.String(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
        sa.UniqueConstraint(
            "dataset_version_id", "table_name", name="uq_dvt_version_table"
        ),
    )
    op.create_index("ix_dvt_version", "dataset_version_tables", ["dataset_version_id"])

    # upload_records 可追溯列
    op.add_column(
        "upload_records", sa.Column("dataset_id", sa.String(), nullable=True)
    )

    # 回填成员:每个现存版本一行(table_name='data',继承版本现值)
    op.execute(
        """
        INSERT INTO dataset_version_tables
            (id, dataset_version_id, table_name, storage_uri, format,
             rows, size, schema_snapshot, schema_variant, created_at)
        SELECT
            'dvt-' || substr(md5(random()::text || dv.id), 1, 6),
            dv.id, 'data', dv.storage_uri, dv.format,
            dv.rows, dv.size, dv.schema_snapshot, dv.schema_variant, now()
        FROM dataset_versions dv
        WHERE NOT EXISTS (
            SELECT 1 FROM dataset_version_tables t
            WHERE t.dataset_version_id = dv.id
        )
        """
    )

    # ingest_tasks.dataset_id 转权威:先给 NULL 行回填(经 Job 间接定位),再 NOT NULL
    op.execute(
        """
        UPDATE ingest_tasks t SET dataset_id = sub.dataset_id
        FROM (
            SELECT j.ingest_task_id AS task_id, dv.dataset_id AS dataset_id
            FROM jobs j
            JOIN dataset_versions dv ON dv.produced_by_job_id = j.id
            WHERE j.type = 'ingest' AND j.ingest_task_id IS NOT NULL
        ) sub
        WHERE t.id = sub.task_id AND t.dataset_id IS NULL
        """
    )
    # 仍为 NULL 的(无产出历史)填空串占位,保证可加 NOT NULL
    op.execute("UPDATE ingest_tasks SET dataset_id = '' WHERE dataset_id IS NULL")
    op.alter_column("ingest_tasks", "dataset_id", nullable=False)


def downgrade() -> None:
    op.alter_column("ingest_tasks", "dataset_id", nullable=True)
    op.drop_column("upload_records", "dataset_id")
    op.drop_index("ix_dvt_version", table_name="dataset_version_tables")
    op.drop_table("dataset_version_tables")
```

- [ ] **Step 4: Run script to verify pass**

Run: `cd backend && SCRATCH_DATABASE_URL=<scratch> bash scripts/verify_0034.sh`
Expected: 打印 `0034 upgrade OK` 和 `0034 downgrade/upgrade roundtrip OK`(结构、回填、回退全过)。

- [ ] **Step 5: Commit**

```bash
git add backend/alembic/versions/0034_dataset_first_multitable.py backend/scripts/verify_0034.sh
git commit -m "feat(migration): 0034 多表成员表 + 采集任务 dataset_id 权威化 + 回填"
```

## Phase 2 — landing 拆分 + 读路径成员化

### Task 3: `external_store` 成员级上传 helper（带 table_name）

**Files:**
- Modify: `backend/app/services/external_store.py:698-728`(加 table_name 形参)
- Test: `backend/tests/test_external_store_member_key.py`

**Interfaces:**
- Produces: `upload_parquet_member(dataset_id, version_no, table_name, parquet_bytes) -> str`(key=`<id>/v<n>/<table>.parquet`)、`upload_jsonl_member(dataset_id, version_no, table_name, jsonl_bytes) -> str`(key=`<id>/v<n>/<table>.jsonl`)。保留旧 `upload_jsonl_to_uploads`/`upload_parquet_to_uploads` 作 `table_name='data'` 的薄封装(向后兼容)。

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_external_store_member_key.py
import app.services.external_store as es


def test_member_key_scheme(monkeypatch):
    captured = {}

    async def fake_upload_object(cfg, bucket, key, *a, **k):
        captured["bucket"] = bucket
        captured["key"] = key

    monkeypatch.setattr(es, "upload_object", fake_upload_object)
    monkeypatch.setattr(es, "platform_config", lambda: object())

    import asyncio
    uri = asyncio.run(es.upload_parquet_member("dset-abc", 2, "orders", b"x"))
    assert captured["key"] == "dset-abc/v2/orders.parquet"
    assert uri == f"s3://{es.settings.storage_minio_upload_bucket}/dset-abc/v2/orders.parquet"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_external_store_member_key.py -q`
Expected: FAIL — `AttributeError: module ... has no attribute 'upload_parquet_member'`

- [ ] **Step 3: Add member-aware uploaders**

在 `external_store.py` 现有 `upload_parquet_to_uploads` 之后追加(沿用其 `platform_config()` + `settings.storage_minio_upload_bucket` 写法):

```python
async def upload_parquet_member(
    dataset_id: str, version_no: int, table_name: str, parquet_bytes: bytes
) -> str:
    """上传一个表成员 parquet,键 = ``<dataset_id>/v<n>/<table>.parquet``。"""
    cfg = platform_config()
    bucket = settings.storage_minio_upload_bucket
    key = f"{dataset_id}/v{version_no}/{table_name}.parquet"
    await upload_object(
        cfg, bucket, key, io.BytesIO(parquet_bytes), len(parquet_bytes)
    )
    return f"s3://{bucket}/{key}"


async def upload_jsonl_member(
    dataset_id: str, version_no: int, table_name: str, jsonl_bytes: bytes
) -> str:
    """上传一个表成员 jsonl,键 = ``<dataset_id>/v<n>/<table>.jsonl``。"""
    cfg = platform_config()
    bucket = settings.storage_minio_upload_bucket
    key = f"{dataset_id}/v{version_no}/{table_name}.jsonl"
    await upload_object(
        cfg, bucket, key, io.BytesIO(jsonl_bytes), len(jsonl_bytes),
        content_type="application/x-ndjson",
    )
    return f"s3://{bucket}/{key}"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_external_store_member_key.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/external_store.py backend/tests/test_external_store_member_key.py
git commit -m "feat(external-store): 成员级 parquet/jsonl 上传 helper"
```

### Task 4: `create_dataset` + `add_table_member`（landing 核心拆分）

**Files:**
- Modify: `backend/app/services/landing.py`(在 `land_records` 旁新增两函数;`land_records` 改为薄封装:`create_dataset` 后 `add_table_member('data', ...)`)
- Test: `backend/tests/test_landing_multitable.py`

**Interfaces:**
- Consumes: `upload_parquet_member`/`upload_jsonl_member`(Task 3);`DatasetVersionTable`(Task 1);现有 `compute_quality_stats`/`schema_snapshot`/`infer_train_type`/`default_schema_variant`/`_new_dataset_id`/`_new_version_id`。
- Produces:
  - `create_dataset(session, *, name, data_type=None, semantic_type=None, source_kind=None, source_format=None, description=None, creator='admin', train_type=None, schema_variant=None) -> Dataset`(只建空数据集,不建版本)。
  - `add_table_member(session, dataset_id, records, *, table_name, storage_format='parquet', semantic_type=None, source_format=None, produced_by_job_id=None, strict_semantic=False, train_type=None, schema_variant=None, note=None) -> tuple[DatasetVersion, DatasetVersionTable]`。
  - 新增模块级 helper `_new_member_id() -> str`(`'dvt-'+6hex`)和 `async def _target_draft_version(session, dataset_id) -> DatasetVersion`(无版本→建 v1 draft;最新 draft→复用;最新 published→建 v+1 draft 并克隆上一版成员行)。
  - `_recompute_version_rollup(session, version)`:把版本的 `rows`/`size` 设为成员之和,`format` 单一→该格式、混合→`'multi'`,`storage_uri` 设为 `s3://<bucket>/<dataset_id>/v<n>/`(前缀)。

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_landing_multitable.py
import pytest

from app.models import DatasetVersion, DatasetVersionTable
from app.services.landing import add_table_member, create_dataset
from sqlalchemy import select


@pytest.mark.asyncio
async def test_create_empty_then_add_two_members(db_session):
    ds = await create_dataset(db_session, name="多表集", data_type="sql")
    # 空数据集:无任何版本
    vers = (await db_session.execute(
        select(DatasetVersion).where(DatasetVersion.dataset_id == ds.id)
    )).scalars().all()
    assert vers == []

    ver1, m1 = await add_table_member(
        db_session, ds.id, [{"a": 1}], table_name="users"
    )
    ver2, m2 = await add_table_member(
        db_session, ds.id, [{"b": 2}, {"b": 3}], table_name="orders"
    )
    # 两个成员同属一个 draft 版本 v1
    assert ver1.id == ver2.id
    assert ver2.version_no == 1
    assert ver2.publish_status == "draft"
    members = (await db_session.execute(
        select(DatasetVersionTable).where(
            DatasetVersionTable.dataset_version_id == ver2.id
        )
    )).scalars().all()
    assert {m.table_name for m in members} == {"users", "orders"}
    # rollup:总行数 = 1 + 2
    await db_session.refresh(ver2)
    assert ver2.rows == 3


@pytest.mark.asyncio
async def test_overwrite_same_table_in_draft(db_session):
    ds = await create_dataset(db_session, name="覆盖集")
    await add_table_member(db_session, ds.id, [{"a": 1}], table_name="t")
    ver, m = await add_table_member(db_session, ds.id, [{"a": 9}, {"a": 8}], table_name="t")
    members = (await db_session.execute(
        select(DatasetVersionTable).where(
            DatasetVersionTable.dataset_version_id == ver.id,
            DatasetVersionTable.table_name == "t",
        )
    )).scalars().all()
    assert len(members) == 1          # 覆盖,不新增
    assert members[0].rows == 2       # 取最新一次


@pytest.mark.asyncio
async def test_published_version_triggers_new_draft(db_session):
    ds = await create_dataset(db_session, name="发布集")
    v1, _ = await add_table_member(db_session, ds.id, [{"a": 1}], table_name="t")
    v1.publish_status = "published"
    await db_session.commit()
    v2, _ = await add_table_member(db_session, ds.id, [{"a": 2}], table_name="t2")
    assert v2.version_no == 2
    assert v2.publish_status == "draft"
    # 克隆上一版成员:v2 应含 t(克隆)+ t2(新增)
    members = (await db_session.execute(
        select(DatasetVersionTable.table_name).where(
            DatasetVersionTable.dataset_version_id == v2.id
        )
    )).scalars().all()
    assert set(members) == {"t", "t2"}
```

> `db_session` fixture:Task 1 Step 6 已在 `tests/conftest.py` 创建。

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_landing_multitable.py -q`
Expected: FAIL — `ImportError: cannot import name 'add_table_member'`

- [ ] **Step 3: Implement create_dataset + add_table_member**

在 `landing.py` 顶部 import 区加 `from app.models import DatasetVersionTable`、`from sqlalchemy import func, select`(若未导入)。新增 `_new_member_id`(仿 `_new_dataset_id`)。实现见下(放在 `land_records` 之前):

```python
async def create_dataset(
    session: AsyncSession,
    *,
    name: str,
    data_type: str | None = None,
    semantic_type: str | None = None,
    source_kind: str | None = None,
    source_format: str | None = None,
    description: str | None = None,
    creator: str = "admin",
    train_type: str | None = None,
    schema_variant: str | None = None,
) -> Dataset:
    """建一个空数据集(不建任何版本)。train_type/schema_variant 作落成员时的默认模板。"""
    explicit = coerce_semantic_type(semantic_type)
    effective_semantic = (
        explicit.value if explicit is not None
        else (infer_semantic_from_data_type(data_type).value
              if infer_semantic_from_data_type(data_type) else None)
    )
    dataset = Dataset(
        id=_new_dataset_id(),
        name=name or "未命名数据集",
        description=description,
        data_type=data_type,
        semantic_type=effective_semantic,
        source_kind=source_kind,
        source_format=source_format,
        owner=creator,
        creator=creator,
    )
    session.add(dataset)
    await session.commit()
    await session.refresh(dataset)
    return dataset


async def _target_draft_version(
    session: AsyncSession, dataset_id: str
) -> DatasetVersion:
    """定位可写 draft 版本:无→建 v1;最新 draft→复用;最新 published→建 v+1 并克隆成员。"""
    latest = (await session.execute(
        select(DatasetVersion)
        .where(DatasetVersion.dataset_id == dataset_id)
        .order_by(DatasetVersion.version_no.desc())
        .limit(1)
    )).scalar_one_or_none()

    if latest is not None and latest.publish_status == "draft":
        return latest

    next_no = (latest.version_no + 1) if latest is not None else 1
    ver = DatasetVersion(
        id=_new_version_id(),
        dataset_id=dataset_id,
        version_no=next_no,
        storage_uri=f"pending://{dataset_id}/v{next_no}/",
        format="jsonl",
        rows=0,
        size=0,
        origin="managed",
        publish_status="draft",
    )
    session.add(ver)
    await session.flush()
    # 发布后开新版本:克隆上一版成员(指向同一旧文件,续接语义)
    if latest is not None:
        prev_members = (await session.execute(
            select(DatasetVersionTable).where(
                DatasetVersionTable.dataset_version_id == latest.id
            )
        )).scalars().all()
        for pm in prev_members:
            session.add(DatasetVersionTable(
                id=_new_member_id(),
                dataset_version_id=ver.id,
                table_name=pm.table_name,
                storage_uri=pm.storage_uri,
                format=pm.format,
                rows=pm.rows,
                size=pm.size,
                schema_snapshot=pm.schema_snapshot,
                schema_variant=pm.schema_variant,
            ))
    await session.commit()
    await session.refresh(ver)
    return ver
```

- [ ] **Step 4: Implement member write + rollup (continue Task 4)**

```python
async def _recompute_version_rollup(
    session: AsyncSession, version: DatasetVersion
) -> None:
    members = (await session.execute(
        select(DatasetVersionTable).where(
            DatasetVersionTable.dataset_version_id == version.id
        )
    )).scalars().all()
    version.rows = sum((m.rows or 0) for m in members)
    version.size = sum((m.size or 0) for m in members)
    formats = {m.format for m in members}
    version.format = formats.pop() if len(formats) == 1 else "multi"
    bucket = settings.storage_minio_upload_bucket
    version.storage_uri = f"s3://{bucket}/{version.dataset_id}/v{version.version_no}/"
    await session.commit()


async def add_table_member(
    session: AsyncSession,
    dataset_id: str,
    records: list[dict],
    *,
    table_name: str,
    storage_format: str = "parquet",
    semantic_type: str | None = None,
    source_format: str | None = None,
    produced_by_job_id: str | None = None,
    strict_semantic: bool = False,
    train_type: str | None = None,
    schema_variant: str | None = None,
    note: str | None = None,
) -> tuple[DatasetVersion, DatasetVersionTable]:
    """把一张表的记录落成当前 draft 版本的一个成员(同名覆盖)。"""
    from app.services.external_store import (
        ExternalStoreError, upload_jsonl_member, upload_parquet_member,
    )
    from app.services.ingest_quality import compute_quality_stats, schema_snapshot

    # 语义归一(沿用 land_records 逻辑)
    explicit = coerce_semantic_type(semantic_type)
    if explicit is not None:
        records, _ = apply_semantic_spec(records, explicit, strict=strict_semantic)

    version = await _target_draft_version(session, dataset_id)

    # 写成员文件(parquet 失败回退 jsonl)
    fmt = "parquet"
    try:
        if storage_format == "parquet":
            data = records_to_parquet_bytes(records)
            uri = await upload_parquet_member(
                dataset_id, version.version_no, table_name, data
            )
            size = len(data)
        else:
            raise ParquetCodecError("forced jsonl")
    except ParquetCodecError:
        data = records_to_jsonl_bytes(records)
        uri = await upload_jsonl_member(
            dataset_id, version.version_no, table_name, data
        )
        fmt = "jsonl"
        size = len(data)
    except ExternalStoreError:
        await session.rollback()
        raise

    stats = compute_quality_stats(records)
    snap = schema_snapshot(stats)
    eff_train = train_type or infer_train_type(version.semantic_type)
    eff_variant = schema_variant or default_schema_variant(eff_train)

    # upsert 成员(同名覆盖)
    existing = (await session.execute(
        select(DatasetVersionTable).where(
            DatasetVersionTable.dataset_version_id == version.id,
            DatasetVersionTable.table_name == table_name,
        )
    )).scalar_one_or_none()
    if existing is not None:
        existing.storage_uri = uri
        existing.format = fmt
        existing.rows = len(records)
        existing.size = size
        existing.schema_snapshot = snap
        existing.schema_variant = eff_variant
        member = existing
    else:
        member = DatasetVersionTable(
            id=_new_member_id(),
            dataset_version_id=version.id,
            table_name=table_name,
            storage_uri=uri,
            format=fmt,
            rows=len(records),
            size=size,
            schema_snapshot=snap,
            schema_variant=eff_variant,
        )
        session.add(member)

    # 版本级训练元数据:首个成员定调
    if version.train_type is None:
        version.train_type = eff_train
        version.schema_variant = eff_variant
    if produced_by_job_id and version.produced_by_job_id is None:
        version.produced_by_job_id = produced_by_job_id
    if note:
        version.note = note
    await session.commit()
    await session.refresh(member)
    await _recompute_version_rollup(session, version)
    await session.refresh(version)
    return version, member
```

并把 `land_records` 改为薄封装(保留签名,内部 `ds = await create_dataset(...)` 然后 `await add_table_member(session, ds.id, records, table_name='data', storage_format=storage_format, ...)`,返回 `(ds, version)`)。**保留** `land_records` 现有的 `version_modalities`/`modalities` 处理:落成员后把 `version.modalities` 写回(多模态扫描结果)。

- [ ] **Step 5: Run tests to verify pass**

Run: `cd backend && uv run pytest tests/test_landing_multitable.py -q`
Expected: PASS(3 个用例)

- [ ] **Step 6: Run regression on existing landing tests**

Run: `cd backend && uv run pytest tests/test_landing_parquet.py tests/test_uploads.py -q`
Expected: PASS(`land_records` 薄封装保持旧契约;单文件 = 单成员 `data`)

- [ ] **Step 7: Commit**

```bash
git add backend/app/services/landing.py backend/tests/test_landing_multitable.py
git commit -m "feat(landing): 拆 create_dataset + add_table_member(draft 可变/published 冻结)"
```

### Task 5: 读路径成员化 — `_members_of` + `materialized_version` 表选择器

**Files:**
- Modify: `backend/app/services/external_store.py`(`materialized_version` 加 `table_name` 可选参数,缺省取第一个成员)
- Modify: `backend/app/api/v1/datasets.py:881`(`_members_of` 枚举 `dataset_version_tables` 成员)
- Test: `backend/tests/test_version_members_read.py`

**Interfaces:**
- Consumes: `add_table_member`(Task 4)。
- Produces: `_members_of(session, version)` 对多表版本返回每个 `DatasetVersionTable` 一项(name=table_name、key、format、rows);`materialized_version(session, version, table_name=None)` 缺省物化第一个成员。

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_version_members_read.py
import pytest
from app.services.landing import add_table_member, create_dataset
from app.api.v1.datasets import _members_of


@pytest.mark.asyncio
async def test_members_lists_each_table(db_session):
    ds = await create_dataset(db_session, name="多表读")
    ver, _ = await add_table_member(db_session, ds.id, [{"a": 1}], table_name="users")
    await add_table_member(db_session, ds.id, [{"b": 2}], table_name="orders")
    await db_session.refresh(ver)
    members = await _members_of(db_session, ver)
    names = {m["name"] if isinstance(m, dict) else m.name for m in members}
    assert {"users", "orders"} <= names
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_version_members_read.py -q`
Expected: FAIL — `_members_of` 仍只识别 manifest/单文件,不返回表成员

- [ ] **Step 3: Extend `_members_of` and `materialized_version`**

在 `datasets.py` 的 `_members_of` 开头加分支:先查 `dataset_version_tables`,若有成员则返回成员列表(每项 `{name, key, bucket, format, size}`,key 从 `storage_uri` 的 `s3://bucket/` 之后截取);无成员再走原 manifest/originals/单文件逻辑(向后兼容回填后的单成员 `data`)。`materialized_version(session, version, table_name=None)`:查成员表,选中 `table_name`(None→首个),用其 `storage_uri` 物化;无成员表行时退回原 `storage_uri` 逻辑。

> 实现需读 `datasets.py:881` 现有 `_members_of` 与 `external_store.py:501` 现有 `materialized_version` 主体后就地扩展;保持其余分支不动(Rule 3 外科手术)。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_version_members_read.py -q`
Expected: PASS

- [ ] **Step 5: Run preview/download regression**

Run: `cd backend && uv run pytest tests/test_datasets_preview.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/app/services/external_store.py backend/app/api/v1/datasets.py backend/tests/test_version_members_read.py
git commit -m "feat(datasets): 读路径成员化(_members_of/materialized_version 表选择器)"
```

## Phase 3 — 后端 API(建集端点 + 各入口选集 + 读 schema 成员数组)

### Task 6: `POST /api/v1/datasets` 建空数据集端点

**Files:**
- Modify: `backend/app/api/v1/datasets.py`(新增路由 + `DatasetCreate` schema)
- Modify: `backend/app/schemas/dataset.py`(加 `DatasetCreate`)
- Test: `backend/tests/test_dataset_create_endpoint.py`

**Interfaces:**
- Consumes: `create_dataset`(Task 4)。
- Produces: `POST /api/v1/datasets` 接 `DatasetCreate { name, categoryId?, dataType?, semanticType?, trainType?, schemaVariant?, tags?[] }`,返回 `DatasetResult{data: DatasetDetailRead(versions=[])}`。

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_dataset_create_endpoint.py
import pytest


@pytest.mark.asyncio
async def test_create_empty_dataset(client):
    resp = await client.post("/api/v1/datasets", json={
        "name": "我的训练集", "dataType": "sql", "semanticType": "structured",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["name"] == "我的训练集"
    assert body["data"]["versions"] == []   # 空数据集无版本
    assert body["data"]["id"].startswith("dset-")
```

> `client` fixture:用 `tests/conftest.py` 现有 async httpx client fixture。

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_dataset_create_endpoint.py -q`
Expected: FAIL — 404(路由不存在)

- [ ] **Step 3: Add schema + route**

`schemas/dataset.py` 加:

```python
class DatasetCreate(CamelModel):
    """新建空数据集入参(数据集优先流程)。"""

    name: str
    category_id: str | None = None
    data_type: str | None = None
    semantic_type: SemanticType | None = None
    train_type: str | None = None
    schema_variant: str | None = None
    tags: list[str] = []
```

`datasets.py` 加路由(放在 upload 路由附近,复用 `current_user`/`_to_detail`/`DatasetResult`、标签写入辅助;权限沿用 upload 路由的 `actor` 取值方式):

```python
@router.post("/datasets")
async def create_dataset_endpoint(
    payload: DatasetCreate,
    session: SessionDep,
    user: Annotated[User | None, Depends(current_user)] = None,
) -> JSONResponse:
    """建空数据集(不含任何版本);上传/采集随后往里加表成员。"""
    actor = user.id if user else "admin"
    dataset = await create_dataset(
        session,
        name=payload.name,
        data_type=payload.data_type,
        semantic_type=payload.semantic_type.value if payload.semantic_type else None,
        train_type=payload.train_type,
        schema_variant=payload.schema_variant,
        creator=actor,
    )
    if payload.category_id:
        dataset.category_id = payload.category_id
        await session.commit()
        await session.refresh(dataset)
    if payload.tags:
        await _replace_dataset_tags(session, dataset.id, payload.tags)  # 复用现有标签写入
    detail = _to_detail(dataset, [])
    return JSONResponse(
        content=DatasetResult(data=detail).model_dump(by_alias=True, mode="json")
    )
```

> 标签写入函数名按 `datasets.py` 实际暴露的为准(读 PATCH 路由里写 tags 的现有 helper 名;若内联则照其写法内联)。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_dataset_create_endpoint.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/v1/datasets.py backend/app/schemas/dataset.py backend/tests/test_dataset_create_endpoint.py
git commit -m "feat(api): POST /datasets 建空数据集端点"
```

### Task 7: 三个上传端点改为必选 datasetId

**Files:**
- Modify: `backend/app/api/v1/datasets.py`(`upload_as_dataset:203`、`upload-batch:526`、`upload-media:365` 加必填 `dataset_id` Form,落成员而非建集 + 写权校验)
- Test: `backend/tests/test_uploads_select_dataset.py`

**Interfaces:**
- Consumes: `add_table_member`(Task 4)、`dataset_acl.can_access(session, user, dataset_id, "edit")`(现有 ACL helper)。
- Produces: 三端点新增必填 `datasetId` Form;缺失 → 422;无写权 → 403;落入该数据集 draft 版本。`table_name` 取上传文件名去扩展名(冲突时加序号)。

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_uploads_select_dataset.py
import io
import pytest


@pytest.mark.asyncio
async def test_upload_requires_dataset_id(client):
    files = {"file": ("a.jsonl", io.BytesIO(b'{"x":1}\n'), "application/x-ndjson")}
    resp = await client.post("/api/v1/datasets/upload", files=files)  # 缺 datasetId
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_upload_lands_into_existing_dataset(client):
    created = (await client.post("/api/v1/datasets", json={"name": "落入集"})).json()
    dset_id = created["data"]["id"]
    files = {"file": ("orders.jsonl", io.BytesIO(b'{"x":1}\n{"x":2}\n'), "application/x-ndjson")}
    resp = await client.post(
        "/api/v1/datasets/upload", files=files, data={"datasetId": dset_id}
    )
    assert resp.status_code == 200
    detail = (await client.get(f"/api/v1/datasets/{dset_id}")).json()["data"]
    assert len(detail["versions"]) == 1          # 落进同一数据集,未新建
    assert detail["versions"][0]["versionNo"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_uploads_select_dataset.py -q`
Expected: FAIL — 当前 upload 无 `datasetId` 概念,会新建数据集(第二个用例 versions 断言失败 / 第一个不报 422)

- [ ] **Step 3: Rework upload_as_dataset (and batch/media)**

`upload_as_dataset` 改造要点:
- 加 `dataset_id: Annotated[str, Form(alias="datasetId")]`(必填,无默认 → 缺失自动 422)。
- 删除 `name`/`data_type`/`category_id` 建集语义(保留为兼容可空,但不再用于建集)。
- 取代 `land_upload`/`land_upload_raw`:先解析文件为 records(复用现有解析,从 `land_upload` 内部解析步骤抽出或调用解析函数),再 `await add_table_member(session, dataset_id, records, table_name=_safe_table_name(filename), source_format=fmt, strict_semantic=strict, ...)`。二进制类走 raw 成员(format 标 raw / 存原字节,沿用 land_upload_raw 的物化但写成成员)。
- 落前校验:`ds = await session.get(Dataset, dataset_id)`;None → 404;`not await can_access(session, user, dataset_id, "edit")` → 403(`can_access` 匿名放行,与现有路由一致)。

`_safe_table_name(filename)`:去路径与扩展名,非法字符替 `_`,空则 `data`。`upload-batch`(每文件一个成员或合并为一个成员 `data` —— 按现状语义保留「合并为一个成员」)、`upload-media`(media → 一个 manifest 成员,保持 manifest 形态但挂到选定数据集)同样加 `datasetId`。

> 媒体/批量分支的 records 解析与原 `land_upload`/manifest 写法对齐;此 Task 只改「建集 → 选集落成员」,不改解析与 manifest 编码(Rule 3)。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_uploads_select_dataset.py tests/test_uploads.py -q`
Expected: PASS(新用例 + 改造后的旧用例;旧 `test_uploads.py` 中「上传建集」断言需同步更新为「先建后传」——在本 Task 内更新)

- [ ] **Step 5: Commit**

```bash
git add backend/app/api/v1/datasets.py backend/tests/test_uploads_select_dataset.py backend/tests/test_uploads.py
git commit -m "feat(api): 上传端点改为必选 datasetId(落成员而非建集)"
```

### Task 8: 采集任务创建必填 datasetId

**Files:**
- Modify: `backend/app/schemas/ingest_task.py:214`(`IngestTaskCreate` 加 `dataset_id`)
- Modify: `backend/app/api/v1/ingest_tasks.py:462-504`(写入 `task.dataset_id` + 存在/写权校验)
- Test: `backend/tests/test_ingest_task_dataset_select.py`

**Interfaces:**
- Consumes: `Dataset`、`dataset_acl.can_access(session, user, dataset_id, "edit")`。
- Produces: `IngestTaskCreate.dataset_id: str`(必填);`create_ingest_task` 校验数据集存在(404)/可写(403)并持久化。

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_ingest_task_dataset_select.py
import pytest


@pytest.mark.asyncio
async def test_ingest_task_requires_existing_dataset(client, seed_datasource):
    # 缺 datasetId → 422
    bad = await client.post("/api/v1/ingest-tasks", json={
        "name": "t", "datasourceId": seed_datasource,
        "schedule": {"mode": "once"},
        "extract": {"mode": "table", "tables": ["public.users"]},
    })
    assert bad.status_code == 422

    ds = (await client.post("/api/v1/datasets", json={"name": "采集目标"})).json()["data"]["id"]
    ok = await client.post("/api/v1/ingest-tasks", json={
        "name": "t", "datasourceId": seed_datasource, "datasetId": ds,
        "schedule": {"mode": "once"},
        "extract": {"mode": "table", "tables": ["public.users"]},
    })
    assert ok.status_code == 200
```

> `seed_datasource` fixture:复用现有采集测试里建 DataSource 的 fixture(按实际命名)。

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_ingest_task_dataset_select.py -q`
Expected: FAIL — 422 不触发(`datasetId` 非必填),OK 用例缺 `task.dataset_id` 写入

- [ ] **Step 3: Add dataset_id to schema + create route**

`ingest_task.py` schema 在 `IngestTaskCreate` 加 `dataset_id: str`(放 `datasource_id` 之后)。`create_ingest_task` 在校验 datasource 后、构造 `IngestTask` 前增(`user` 经 `Depends(current_user)` 注入,与 datasets 路由一致):

```python
    dataset = await session.get(Dataset, payload.dataset_id)
    if dataset is None:
        return _not_found()
    if not await can_access(session, user, payload.dataset_id, "edit"):
        return JSONResponse(
            status_code=403,
            content={"success": False, "message": "无该数据集写入权限"},
        )
    task = IngestTask(
        id=_new_task_id(),
        name=payload.name,
        datasource_id=payload.datasource_id,
        datasource_name=datasource.name,
        dataset_id=payload.dataset_id,
        schedule=payload.schedule.model_dump(),
        # ... 其余字段同现有 create_ingest_task
    )
```

import:`from app.services.dataset_acl import can_access`、`from app.models import Dataset`、`current_user` 依赖(若 `create_ingest_task` 当前无 `user` 形参,加 `user: Annotated[User | None, Depends(current_user)] = None`)。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_ingest_task_dataset_select.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/schemas/ingest_task.py backend/app/api/v1/ingest_tasks.py backend/tests/test_ingest_task_dataset_select.py
git commit -m "feat(api): 采集任务创建必填 datasetId"
```

### Task 9: 读 schema 暴露成员数组 + host 端点选集

**Files:**
- Modify: `backend/app/schemas/dataset.py`(`DatasetVersionRead` 加 `tables: list[DatasetTableRead]`)
- Modify: `backend/app/api/v1/datasets.py`(`_to_detail`/版本读填充成员;`host-s3`/`host-platform` 加 `datasetId`)
- Test: `backend/tests/test_version_tables_in_read.py`

**Interfaces:**
- Consumes: Task 4/5。
- Produces: `DatasetTableRead { tableName, storageUri, format, rows, size, schemaVariant }`;`DatasetVersionRead.tables` 数组;`HostS3Request`/`PlatformHostRequest` 加 `dataset_id`,多 key 落同一数据集多成员。

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_version_tables_in_read.py
import io
import pytest


@pytest.mark.asyncio
async def test_detail_exposes_table_members(client):
    ds = (await client.post("/api/v1/datasets", json={"name": "成员读"})).json()["data"]["id"]
    for tbl in ("users", "orders"):
        files = {"file": (f"{tbl}.jsonl", io.BytesIO(b'{"x":1}\n'), "application/x-ndjson")}
        await client.post("/api/v1/datasets/upload", files=files, data={"datasetId": ds})
    detail = (await client.get(f"/api/v1/datasets/{ds}")).json()["data"]
    tables = detail["versions"][0]["tables"]
    assert {t["tableName"] for t in tables} == {"users", "orders"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_version_tables_in_read.py -q`
Expected: FAIL — `KeyError: 'tables'`

- [ ] **Step 3: Add DatasetTableRead + populate + host datasetId**

`schemas/dataset.py`:

```python
class DatasetTableRead(CamelModel):
    """版本内的一个表成员读模型。"""

    table_name: str
    storage_uri: str
    format: str
    rows: int | None = None
    size: int | None = None
    schema_variant: str | None = None
```

`DatasetVersionRead` 加 `tables: list[DatasetTableRead] = []`。`_to_detail`(或版本序列化处)按 `dataset_version_id` 批量查 `dataset_version_tables` 填充 `tables`。`HostS3Request`/`PlatformHostRequest` 加 `dataset_id: str`;host 路由的多 key 循环从「每 key 建集」改为「每 key 调 `add_table_member(dataset_id, ..., table_name=<key 派生>)`」,返回单 `DatasetDetailRead`。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_version_tables_in_read.py -q`
Expected: PASS

- [ ] **Step 5: Regenerate OpenAPI + commit**

```bash
git add backend/app/schemas/dataset.py backend/app/api/v1/datasets.py backend/tests/test_version_tables_in_read.py
git commit -m "feat(api): 版本读模型暴露表成员数组 + host 端点选集"
```

## Phase 4 — 连接器 / host 收口（多表落同一数据集）

### Task 10: 连接器落地改为 add_table_member(task.dataset_id)

**Files:**
- Modify: `backend/app/services/connectors/pg.py:142-213`、`mysql.py`、`hdfs.py`、`objectstore.py`、`proprietary.py`(把每表 `land_records(...)` 换成 `add_table_member(session, task.dataset_id, records, table_name=suffix, ...)`)
- Modify: `backend/app/services/connectors/base.py`(若 `run_ingest` 返回 `list[tuple[Dataset, DatasetVersion]]`,改为返回落入的 `(DatasetVersion, list[DatasetVersionTable])` 或保持兼容签名 — 见下)
- Test: `backend/tests/test_connector_multitable_funnel.py`

**Interfaces:**
- Consumes: `add_table_member`(Task 4)、`task.dataset_id`(Task 2/8 权威)。
- Produces: 多表采集 → **一个数据集一个 draft 版本多成员**(回归原「扇出 N 数据集」)。`_execute_ingest`(ingest_tasks.py:592)消费连接器返回值的方式同步调整。

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_connector_multitable_funnel.py
"""用 fake 连接器验证多表落进同一数据集(不连真实 PG)。"""
import pytest
from sqlalchemy import select
from app.models import DatasetVersion, DatasetVersionTable
from app.services.landing import create_dataset, add_table_member


@pytest.mark.asyncio
async def test_two_tables_one_dataset_one_version(db_session):
    ds = await create_dataset(db_session, name="采集多表", data_type="sql")
    # 模拟连接器对两张表各调一次
    await add_table_member(db_session, ds.id, [{"id": 1}], table_name="public.users",
                           source_format="db", produced_by_job_id="job-x")
    await add_table_member(db_session, ds.id, [{"id": 2}], table_name="public.orders",
                           source_format="db", produced_by_job_id="job-x")
    vers = (await db_session.execute(
        select(DatasetVersion).where(DatasetVersion.dataset_id == ds.id)
    )).scalars().all()
    assert len(vers) == 1                       # 一个数据集一个版本(不扇出)
    members = (await db_session.execute(
        select(DatasetVersionTable).where(
            DatasetVersionTable.dataset_version_id == vers[0].id
        )
    )).scalars().all()
    assert {m.table_name for m in members} == {"public.users", "public.orders"}
```

- [ ] **Step 2: Run test to verify it fails (or passes by Task 4)**

Run: `cd backend && uv run pytest tests/test_connector_multitable_funnel.py -q`
Expected: PASS 实际验证的是 landing 行为(Task 4 已实现)。本 Task 真正改动是连接器源码——其回归靠现有连接器测试。若该测试已 PASS,直接进 Step 3 改连接器并跑连接器测试。

- [ ] **Step 3: Rewire each connector's land step**

以 `pg.py:run_pg_ingest` 为模板:`_build_queries` 仍产 `[(suffix, query)]`;循环内把
```python
dataset, version = await land_records(session, records, dataset_name=..., source_kind="database", storage_format="parquet", produced_by_job_id=job_id, ...)
results.append((dataset, version))
```
改为
```python
version, member = await add_table_member(
    session, task.dataset_id, records,
    table_name=suffix, storage_format="parquet",
    source_format="db", produced_by_job_id=job_id,
)
```
循环结束返回该 `version`(单个)与其成员集。`mysql.py`/`hdfs.py`/`objectstore.py`/`proprietary.py` 同构改造。`_execute_ingest`(ingest_tasks.py:592)把 `for dataset, ver in results` 的质量门评估改为「对最终版本评估一次」(每版本一次 `evaluate_policy`,作用于 rollup);`drift` 现在可对比上一发布版本的 `schema_snapshot`(LIVE,不再恒 None)——但 schema drift 的启用留到后续 Task,本 Task 仅保持 `drift=None` 不回归。

- [ ] **Step 4: Run connector regression**

Run: `cd backend && uv run pytest tests/ -k "ingest or connector or pg" -q`
Expected: PASS(连接器测试若断言「N 数据集」需更新为「1 数据集 N 成员」——在本 Task 内更新)

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/connectors/ backend/app/api/v1/ingest_tasks.py backend/tests/test_connector_multitable_funnel.py
git commit -m "feat(connectors): 多表采集落进同一数据集(收口扇出)"
```

### Task 11: API push 纳入统一约束(校验 boundDatasetId 存在)

**Files:**
- Modify: `backend/app/services/connectors/push.py:91-232`(首推绑定时校验目标数据集存在且可写)
- Test: `backend/tests/test_push_bound_dataset.py`

**Interfaces:**
- Consumes: `add_table_member`(Task 4,push 改为落 `data` 成员到 draft 版本)。
- Produces: push 绑定的 `datasource.config.boundDatasetId` 必须指向已存在数据集;不存在 → 400。

- [ ] **Step 1: Write the failing test**

```python
# backend/tests/test_push_bound_dataset.py
import pytest
from app.services.connectors.push import land_push_records


@pytest.mark.asyncio
async def test_push_rejects_unknown_bound_dataset(db_session, make_push_datasource):
    ds_src = await make_push_datasource(bound_dataset_id="dset-nope")
    with pytest.raises(Exception):  # 目标数据集不存在 → 拒绝
        await land_push_records(db_session, ds_src, [{"x": 1}])
```

> `make_push_datasource` fixture:按现有 push 测试的 DataSource 构造方式;若无则在本 Task 内于 `conftest` 或测试文件内建最小 fixture。

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/test_push_bound_dataset.py -q`
Expected: FAIL — 当前 push 对未知 boundDatasetId 不校验(会新建/报别的错)

- [ ] **Step 3: Add existence check in push bind path**

`push.py` 在解析 `boundDatasetId` 后:若 config 指定了 id 但 `session.get(Dataset, id)` 为 None → raise(400 语义);未指定时维持「首推绑定」但绑定到一个**已存在**数据集(不再隐式建集)。落地用 `add_table_member(session, dataset.id, records, table_name="data", storage_format="jsonl")` 替换原 `version_no=max+1` 手写逻辑。

- [ ] **Step 4: Run test to verify it passes**

Run: `cd backend && uv run pytest tests/test_push_bound_dataset.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/connectors/push.py backend/tests/test_push_bound_dataset.py
git commit -m "feat(push): API 推送纳入选集约束(校验绑定数据集存在)"
```

## Phase 5 — 前端（建集、选集、多表 UI）

### Task 12: api.ts 新增 createDataset + 类型

**Files:**
- Modify: `frontend/src/services/data-platform/api.ts`(加 `createDataset`)
- Modify: `frontend/src/services/data-platform/typings.d.ts`(`DatasetCreate`、`DatasetVersion.tables`、`DatasetTable`)
- Test: 手动 `npm run tsc`(前端无单测约定;类型即契约)

**Interfaces:**
- Produces: `createDataset(body: API.DatasetCreate): Promise<API.DatasetResult>` → `POST /api/v1/datasets`。

- [ ] **Step 1: Regenerate backend OpenAPI first**

Run: `cd frontend && npm run openapi`(后端契约已含 `/datasets` 与 `tables` 字段,重生 `src/services/ant-design-pro/`,勿手改)

- [ ] **Step 2: Add hand-written api.ts wrapper**

```typescript
// frontend/src/services/data-platform/api.ts —— 与现有 upload* 包装同风格
export async function createDataset(body: API.DatasetCreate) {
  return request<API.DatasetResult>('/api/v1/datasets', {
    method: 'POST',
    data: body,
  });
}
```

`typings.d.ts` 加 `DatasetCreate { name; categoryId?; dataType?; semanticType?; trainType?; schemaVariant?; tags?: string[] }`、`DatasetTable { tableName; storageUri; format; rows?; size?; schemaVariant? }`,并在 `DatasetVersion` 上加 `tables?: DatasetTable[]`。

- [ ] **Step 3: Type check**

Run: `cd frontend && npm run tsc`
Expected: 无类型错误

- [ ] **Step 4: Commit**

```bash
git add frontend/src/services/data-platform/ frontend/src/services/ant-design-pro/
git commit -m "feat(fe): createDataset API + 多表成员类型"
```

### Task 13: 数据集列表「新建数据集」ModalForm

**Files:**
- Modify: `frontend/src/pages/datasets/list/index.tsx`(toolbar 加「新建数据集」`ModalForm`)
- Test: 手动 `npm run tsc` + `npm run lint`

**Interfaces:**
- Consumes: `createDataset`(Task 12)。

- [ ] **Step 1: Add ModalForm to toolbar**

在列表页 `toolBarRender` 加一个 `ModalForm`(antd v6 / ProComponents),字段:`name`(必填)、`dataType`(下拉,复用现有 data_type 选项常量)、`semanticType`(下拉)、`categoryId`(分类选择,复用现有分类选择组件)、`trainType`/`schemaVariant`(下拉,可空)、`tags`。`onFinish` 调 `createDataset`,成功后 `actionRef.current?.reload()` 并提示「数据集已创建,去详情页添加数据」。

- [ ] **Step 2: Type check + lint**

Run: `cd frontend && npm run tsc && npm run lint`
Expected: 通过(lint 仅 scope 到改动文件,见 Global Constraints / [[biome-write-whole-repo-footgun]])

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/datasets/list/index.tsx
git commit -m "feat(fe): 数据集列表新建数据集弹窗"
```

### Task 14: 上传表单从「填名字」改为「选数据集」

**Files:**
- Modify: `frontend/src/pages/ingest/local-upload/single.tsx`(name 字段 → 数据集选择器,提交带 `datasetId`)
- Modify: `frontend/src/pages/ingest/access/UploadModal.tsx`(同上;media 分支也带 `datasetId`)
- Test: 手动 `npm run tsc` + `npm run lint`

**Interfaces:**
- Consumes: 现有 `listDatasets`(数据集下拉数据源)、改造后的 upload 端点(必带 `datasetId`)。

- [ ] **Step 1: Replace name field with dataset selector**

把「数据集名称」输入框换成 `ProFormSelect`(`request` 拉 `listDatasets`,`showSearch`,选项 `label=name value=id`),附「+ 新建数据集」快捷入口(可复用 Task 13 的 ModalForm 组件,建完自动选中)。FormData 提交键由 `name/data_type/categoryId` 改为 `datasetId`。更新文案:删除「每张表各产一个数据集 / 每个对象各产一个数据集」表述,改为「上传的文件将作为表成员加入所选数据集」。

- [ ] **Step 2: Type check + lint**

Run: `cd frontend && npm run tsc && npm run lint`
Expected: 通过

- [ ] **Step 3: Commit**

```bash
git add frontend/src/pages/ingest/local-upload/single.tsx frontend/src/pages/ingest/access/UploadModal.tsx
git commit -m "feat(fe): 本地上传改为选择已有数据集"
```

### Task 15: 采集向导增「目标数据集」步 + 多表成员展示

**Files:**
- Modify: `frontend/src/pages/ingest/tasks/index.tsx`(StepsForm 增目标数据集选择;`IngestTaskCreate` 带 `datasetId`)
- Modify: `frontend/src/pages/datasets/detail/index.tsx`(版本详情按 `version.tables` 列出各表成员 + 切换预览)
- Test: 手动 `npm run tsc` + `npm run lint`

**Interfaces:**
- Consumes: `listDatasets`、`version.tables`(Task 9/12)、现有 `previewDatasetVersion`(已接受 key 参数,切成员)。

- [ ] **Step 1: Add target-dataset step to wizard**

在 `StepsForm` 第一步(或数据源之后)加「目标数据集」`ProFormSelect`(同 Task 14 数据源),`IngestTaskCreate` payload 带 `datasetId`。改文案「每张表各产一个数据集」→「所选表将作为成员落入目标数据集的一个版本」。

- [ ] **Step 2: Render table members in detail**

详情页版本区:若 `version.tables?.length > 1`,渲染成员表格(列:表名 / 格式 / 行数 / 大小),点成员调 `previewDatasetVersion(versionId, { key })` 切换预览;单成员维持现状。

- [ ] **Step 3: Type check + lint**

Run: `cd frontend && npm run tsc && npm run lint`
Expected: 通过

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/ingest/tasks/index.tsx frontend/src/pages/datasets/detail/index.tsx
git commit -m "feat(fe): 采集向导选目标数据集 + 详情多表成员展示"
```

### Task 16: 端到端冒烟回归

**Files:**
- Test: 后端冒烟集 + 前端构建

- [ ] **Step 1: Backend smoke**

Run:
```bash
cd backend && uv run pytest tests/test_files.py tests/test_datasets_preview.py \
  tests/test_uploads.py tests/test_dataset_acl.py tests/test_rbac.py -q
```
Expected: PASS

- [ ] **Step 2: Lint**

Run: `cd backend && uv run ruff check .`
Expected: 无错误

- [ ] **Step 3: Frontend build**

Run: `cd frontend && npm run tsc && npm run build`
Expected: 构建成功

- [ ] **Step 4: Manual flow check（连真实后端,可选）**

`/adp-start` 起平台 → 新建数据集 → 上传一个文件落入 → 建多表采集任务落入同一数据集 → 详情页见多成员。

- [ ] **Step 5: Commit any test fixups**

```bash
git add backend/tests/ && git commit -m "test: 数据集优先流程端到端冒烟回归"
```

## 任务依赖

```
Task 1 ─┬─ Task 2 ── (Phase 1 完成)
        └─ Task 4
Task 3 ─── Task 4 ─┬─ Task 5 ─┬─ Task 9
                   ├─ Task 6   │
                   ├─ Task 7 ──┤
                   ├─ Task 8   │
                   ├─ Task 10  │
                   └─ Task 11  │
Task 9/6 ── Task 12 ── Task 13 ── Task 14 ── Task 15 ── Task 16
```
Phase 1→2→3→4 后端可独立验证;Phase 5 依赖 Phase 3 的契约(`/datasets` + `tables` 字段)定稿后跑 `npm run openapi`。





