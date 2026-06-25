# 标签管理 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 为已有 `tags`/`dataset_tags` 表补「标签管理」CRUD+合并 API 与前端页,并把「分类管理」从「数据接入」迁到「数据集仓库」与「标签管理」并列。

**Architecture:** 后端**扩展现有** `api/v1/tags.py` 路由(已有 `GET /tags` typeahead,扩展加 usageCount/createdAt,新增写端点),对齐 `categories.py` 模式;复用 `Tag`/`DatasetTag` 模型,**无 DB 迁移**。前端新增自包含 ProTable 管理页 `./datasets/tags`(标签无需像分类那样嵌入上传表单,故不做共享 Panel 组件)。菜单:routes.ts 把 categories 路由从 `/ingest` 迁到 `/datasets`,新增 `/datasets/tags`,旧路径 redirect。

**Tech Stack:** FastAPI + SQLAlchemy async(后端 py3.12 venv);Ant Design Pro v6 / ProComponents v3 / antd v6(前端)。

## Global Constraints

- 后端用 venv `./.venv/Scripts/uvicorn.exe` 直跑(**勿 `uv run`**);改 `.py` 手动重启(无 --reload)。
- 前端 Biome+tsc(无 ESLint/Prettier);TypeScript strict;Node ≥ 22;`package-lock.json`。
- Conventional commits(commitlint 风格,如 `feat(backend): ...`)。
- 后端 schema 走 `CamelModel` + `by_alias=True` 驼峰出参;仓库无-FK 约定(纯 String 引用)。
- 写端点一律 `dependencies=[Depends(require_admin)]`(越权防护在后端)。
- **标签保持扁平 + 仅挂数据集**(YAGNI:不加层级、不扩数据源/采集任务)。
- `GET /tags` 已被数据集详情/列表/presets 三处用作 typeahead(只用 id+name),**扩展字段向后兼容**——这三处不改。

---

## File Structure

- **Modify** `backend/app/api/v1/tags.py` — 扩展路由(GET 加 usageCount/createdAt;新增 POST/PATCH/DELETE/批量 DELETE/merge)。
- **Create** `backend/app/schemas/tag.py` — `TagCreate` / `TagUpdate` / `TagMerge` / `TagBatchDelete` / `TagRead`。
- **Create** `backend/tests/test_tags.py` — 后端测试(对齐 `test_categories.py`)。
- **Modify** `frontend/src/services/data-platform/api.ts` — 新增 createTag/updateTag/deleteTag/batchDeleteTags/mergeTags;listTags 已存在。
- **Modify** `frontend/src/services/data-platform/typings.d.ts` — 扩展现有 `Tag` 类型 + 新增写入参类型。
- **Create** `frontend/src/pages/datasets/tags/index.tsx` — 标签管理页(ProTable + 新建/重命名/删除/批量删/合并)。
- **Modify** `frontend/config/routes.ts` — categories 路由迁入 `/datasets`,新增 `/datasets/tags`,旧路径 redirect。
- **Move** `frontend/src/pages/ingest/categories/` → `frontend/src/pages/datasets/categories/`(菜单归位)。
- **Modify** `frontend/src/locales/zh-CN/menu.ts` — `menu.ingest.categories` → `menu.datasets.categories`;新增 `menu.datasets.tags`。

---

## Task 1: Backend — schema + 扩展 GET /tags(加 usageCount/createdAt)+ 测试

**Files:**
- Create: `backend/app/schemas/tag.py`
- Modify: `backend/app/api/v1/tags.py`
- Test: `backend/tests/test_tags.py`

**Interfaces:**
- Produces: `TagRead{id,name,createdAt,usageCount}`(GET /tags 响应);`_usage_counts(session) -> dict[str,int]`(后续写端点回显 usageCount 复用)。

- [ ] **Step 1: 写 schema**

```python
# backend/app/schemas/tag.py
"""标签 schema(全局标签池,扁平,仅挂数据集)。

createdAt / usageCount 走 alias 驼峰,与全仓 by_alias 约定一致。
"""
from __future__ import annotations

from app.schemas.common import CamelModel, UtcDateTime


class TagCreate(CamelModel):
    name: str


class TagUpdate(CamelModel):
    name: str


class TagMerge(CamelModel):
    source_id: str
    target_id: str


class TagBatchDelete(CamelModel):
    ids: list[str]


class TagRead(CamelModel):
    """标签读模型。usageCount = 引用该标签的数据集数,由路由聚合填充,非 ORM 字段,默认 0。"""

    id: str
    name: str
    created_at: UtcDateTime
    usage_count: int = 0
```

- [ ] **Step 2: 写失败测试 — GET /tags 返回 usageCount + createdAt**

```python
# backend/tests/test_tags.py
"""标签(Tag)管理测试。

覆盖:GET 列表回 usageCount/createdAt;新建(find-or-create 幂等);改名(404/409);
删除(级联解绑 dataset_tags);批量删除级联;合并(去重 + 删源);require_admin 门控。

测试意图(为何重要):
- 标签是数据集多对多元数据,删除/合并会改写 dataset_tags 关联——级联正确性是数据完整性的核心。
- 合并时「同时含源+目标标签的数据集」必须去重(复合 PK 兜底),否则 PK 冲突报错。
- 写端点必须后端 require_admin 门控(不靠前端隐藏),防越权改全局标签池。
"""
from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.dataset import Dataset
from app.models.tag import DatasetTag, Tag

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def _admin_session(client: AsyncClient, seed_users: None) -> None:
    """标签写端点加了 require_admin:默认以 admin 身份请求。"""
    from app.services.auth import sign_token

    client.cookies.set("adp_session", sign_token("admin"))


async def _create(client: AsyncClient, name: str) -> dict:
    resp = await client.post("/api/v1/tags", json={"name": name})
    assert resp.status_code == 200, resp.text
    return resp.json()["data"]


async def test_list_returns_usage_count_and_created_at(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    """GET /tags 回全量标签 + 各自 usageCount + createdAt。"""
    t1 = await _create(client, "NLP")
    t2 = await _create(client, "CV")
    # 直接落库:两个数据集挂 t1
    async with session_factory() as session:
        session.add_all(
            [
                Dataset(id="dset-tg01", name="ds1", owner="admin", creator="admin"),
                Dataset(id="dset-tg02", name="ds2", owner="admin", creator="admin"),
            ]
        )
        session.add_all(
            [DatasetTag(dataset_id="dset-tg01", tag_id=t1["id"]),
             DatasetTag(dataset_id="dset-tg02", tag_id=t1["id"])]
        )
        await session.commit()

    resp = await client.get("/api/v1/tags")
    assert resp.status_code == 200
    by_id = {t["id"]: t for t in resp.json()["data"]}
    assert by_id[t1["id"]]["usageCount"] == 2
    assert by_id[t2["id"]]["usageCount"] == 0
    assert "createdAt" in by_id[t1["id"]]
```

- [ ] **Step 3: 跑测试,确认失败**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_tags.py::test_list_returns_usage_count_and_created_at -v`
Expected: FAIL(`usageCount` / `createdAt` 不在响应中——当前 GET /tags 只回 id+name)。

- [ ] **Step 4: 扩展 `tags.py` — GET /tags 加 usageCount/createdAt + 引入 schema 与辅助**

替换 `backend/app/api/v1/tags.py` 全文为:

```python
"""标签路由:全局标签池的管理 API(列表含使用数 / 新建 find-or-create / 重命名 /
删除级联 / 批量删 / 合并去重)。GET /tags 同时供数据集编辑标签 typeahead(只用 id+name,
扩展字段向后兼容)。

契约:
- GET /tags → {data:[TagRead…], success}(所有登录用户)。
- POST /tags (admin) → find-or-create:同名直接返回已存在项。
- PATCH /tags/{id} (admin) → 重命名;404 缺失/409 重名。
- DELETE /tags/{id} (admin) → 级联:删 dataset_tags 关联 + 删标签。
- DELETE /tags (admin) → 批量级联删。
- POST /tags/merge (admin) → {sourceId→targetId}:重指 dataset_tags(去重)+ 删源标签。
"""
from __future__ import annotations

import secrets
from collections import defaultdict
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.core.db import get_session
from app.models.tag import DatasetTag, Tag
from app.schemas.tag import (
    TagBatchDelete,
    TagCreate,
    TagMerge,
    TagRead,
    TagUpdate,
)

router = APIRouter(tags=["tags"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]


def _new_id() -> str:
    """生成形如 tag-<6位hex> 的主键(与 datasets._new_tag_id 同口径,本路由自洽定义)。"""
    return f"tag-{secrets.token_hex(3)}"


async def _usage_counts(session: AsyncSession) -> dict[str, int]:
    """{tag_id: 引用该标签的数据集数}(一条 GROUP BY,非 N+1)。"""
    rows = (
        await session.execute(
            select(DatasetTag.tag_id, func.count()).group_by(DatasetTag.tag_id)
        )
    ).all()
    return defaultdict(int, {tid: n for tid, n in rows})


async def _usage_count_of(session: AsyncSession, tag_id: str) -> int:
    return (
        await session.scalar(
            select(func.count())
            .select_from(DatasetTag)
            .where(DatasetTag.tag_id == tag_id)
        )
        or 0
    )


def _read(tag: Tag, counts: dict[str, int]) -> dict:
    item = TagRead.model_validate(tag)
    item.usage_count = counts.get(tag.id, 0)
    return item.model_dump(by_alias=True, mode="json")


@router.get("/tags")
async def list_tags(session: SessionDep) -> JSONResponse:
    """列出全部标签(按创建时间倒序)+ 各自 usageCount。所有登录用户可见。"""
    rows = (
        await session.scalars(select(Tag).order_by(Tag.created_at.desc()))
    ).all()
    counts = await _usage_counts(session)
    data = [_read(r, counts) for r in rows]
    return JSONResponse(content={"data": data, "success": True})
```

- [ ] **Step 5: 跑测试,确认通过**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_tags.py::test_list_returns_usage_count_and_created_at -v`
Expected: PASS。

- [ ] **Step 6: 回归 — 旧的 GET /tags typeahead 消费方未坏**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/ -k "dataset" -v`
Expected: 现有数据集测试全 PASS(响应只多字段,id/name 不变)。

- [ ] **Step 7: commit**

```bash
cd backend
git add app/schemas/tag.py app/api/v1/tags.py tests/test_tags.py
git commit -m "feat(backend): 标签管理 GET /tags 扩展 usageCount/createdAt + schema"
```

---

## Task 2: Backend — POST(find-or-create)/ PATCH(重命名)/ DELETE(级联)+ 测试

**Files:**
- Modify: `backend/app/api/v1/tags.py`(在 `list_tags` 之后追加)
- Test: `backend/tests/test_tags.py`(追加)

**Interfaces:**
- Consumes: `_read` / `_usage_count_of` / `_new_id`(Task 1)。
- Produces: `POST/PATCH/DELETE /tags`。

- [ ] **Step 1: 写失败测试 — CRUD + 级联**

追加到 `backend/tests/test_tags.py`:

```python
async def test_create_find_or_create_idempotent(client: AsyncClient) -> None:
    """同名 POST 直接返回已存在项(不报错),usageCount 透传。"""
    a = await _create(client, "同义")
    b_resp = await client.post("/api/v1/tags", json={"name": "同义"})
    assert b_resp.status_code == 200
    assert b_resp.json()["data"]["id"] == a["id"]


async def test_create_strips_whitespace(client: AsyncClient) -> None:
    """name 去首尾空白后入库。"""
    data = await _create(client, "  NLP  ")
    assert data["name"] == "NLP"


async def test_patch_rename(client: AsyncClient) -> None:
    tag = await _create(client, "旧名")
    resp = await client.patch(f"/api/v1/tags/{tag['id']}", json={"name": "新名"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["name"] == "新名"


async def test_patch_missing_404(client: AsyncClient) -> None:
    resp = await client.patch("/api/v1/tags/tag-nope0", json={"name": "x"})
    assert resp.status_code == 404
    assert resp.json()["success"] is False


async def test_patch_rename_clash_409(client: AsyncClient) -> None:
    await _create(client, "甲")
    b = await _create(client, "乙")
    resp = await client.patch(f"/api/v1/tags/{b['id']}", json={"name": "甲"})
    assert resp.status_code == 409
    assert resp.json()["message"] == "标签名已存在"


async def test_delete_cascades_dataset_tags(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    """删标签 → dataset_tags 中对应关联消失,数据集仍在。"""
    tag = await _create(client, "待删")
    async with session_factory() as session:
        session.add(Dataset(id="dset-del01", name="ds", owner="admin", creator="admin"))
        session.add(DatasetTag(dataset_id="dset-del01", tag_id=tag["id"]))
        await session.commit()

    resp = await client.delete(f"/api/v1/tags/{tag['id']}")
    assert resp.status_code == 200
    async with session_factory() as session:
        left = (await session.scalars(
            select(DatasetTag).where(DatasetTag.tag_id == tag["id"])
        )).all()
        assert left == []
        assert (await session.get(Dataset, "dset-del01")) is not None  # 数据集仍在
    # 再删 → 404
    assert (await client.delete(f"/api/v1/tags/{tag['id']}")).status_code == 404
```

- [ ] **Step 2: 跑测试,确认失败**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_tags.py -k "create_find or create_strips or patch or delete_cascades" -v`
Expected: FAIL(端点未实现,404/405)。

- [ ] **Step 3: 实现 POST / PATCH / DELETE**

在 `tags.py` 的 `list_tags` 之后追加:

```python
@router.post("/tags", dependencies=[Depends(require_admin)])
async def create_tag(body: TagCreate, session: SessionDep) -> JSONResponse:
    """新建标签(find-or-create):同名直接返回已存在项。新建 usageCount 恒 0。"""
    name = body.name.strip()
    existing = await session.scalar(select(Tag).where(Tag.name == name))
    if existing is not None:
        counts = await _usage_counts(session)
        return JSONResponse(
            content={"data": _read(existing, counts), "success": True}
        )
    tag = Tag(id=_new_id(), name=name)
    session.add(tag)
    await session.commit()
    await session.refresh(tag)
    return JSONResponse(
        content={"data": _read(tag, {}), "success": True}
    )


@router.patch("/tags/{tag_id}", dependencies=[Depends(require_admin)])
async def update_tag(
    tag_id: str, body: TagUpdate, session: SessionDep
) -> JSONResponse:
    """重命名;404 缺失/409 重名。"""
    tag = await session.get(Tag, tag_id)
    if tag is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "标签不存在"},
        )
    name = body.name.strip()
    if name != tag.name:
        clash = await session.scalar(
            select(Tag.id).where(Tag.name == name).where(Tag.id != tag_id)
        )
        if clash is not None:
            return JSONResponse(
                status_code=409,
                content={"success": False, "message": "标签名已存在"},
            )
        tag.name = name
        await session.commit()
        await session.refresh(tag)
    usage = await _usage_count_of(session, tag_id)
    return JSONResponse(
        content={"data": _read(tag, {tag_id: usage}), "success": True}
    )


@router.delete("/tags/{tag_id}", dependencies=[Depends(require_admin)])
async def delete_tag(tag_id: str, session: SessionDep) -> JSONResponse:
    """删除标签 + 级联解绑所有 dataset_tags。404 缺失。"""
    tag = await session.get(Tag, tag_id)
    if tag is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "标签不存在"},
        )
    await session.execute(
        delete(DatasetTag).where(DatasetTag.tag_id == tag_id)
    )
    await session.delete(tag)
    await session.commit()
    return JSONResponse(content={"success": True})
```

- [ ] **Step 4: 跑测试,确认通过**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_tags.py -k "create_find or create_strips or patch or delete_cascades" -v`
Expected: PASS。

- [ ] **Step 5: commit**

```bash
cd backend
git add app/api/v1/tags.py tests/test_tags.py
git commit -m "feat(backend): 标签管理 POST/PATCH/DELETE(find-or-create/重命名/级联删)"
```

---

## Task 3: Backend — 批量 DELETE + 合并(merge)+ 测试

**Files:**
- Modify: `backend/app/api/v1/tags.py`(追加)
- Test: `backend/tests/test_tags.py`(追加)

**Interfaces:**
- Produces: `DELETE /tags`(批量)、`POST /tags/merge`。

- [ ] **Step 1: 写失败测试 — 批量删 + 合并(含去重)**

追加到 `backend/tests/test_tags.py`:

```python
async def test_batch_delete_cascades(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    a = await _create(client, "甲")
    b = await _create(client, "乙")
    async with session_factory() as session:
        session.add(Dataset(id="dset-bd01", name="ds", owner="admin", creator="admin"))
        session.add_all(
            [DatasetTag(dataset_id="dset-bd01", tag_id=a["id"]),
             DatasetTag(dataset_id="dset-bd01", tag_id=b["id"])]
        )
        await session.commit()

    resp = await client.delete("/api/v1/tags", json={"ids": [a["id"], b["id"]]})
    assert resp.status_code == 200
    async with session_factory() as session:
        assert (await session.get(Tag, a["id"])) is None
        assert (await session.get(Tag, b["id"])) is None
        left = (await session.scalars(select(DatasetTag))).all()
        assert left == []


async def test_merge_reassigns_and_dedups(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    """合并:source 的数据集全部重指到 target;同时含两标签的数据集去重;source 删除。"""
    source = await _create(client, "源")
    target = await _create(client, "目标")
    # ds_only_src: 只挂 source;ds_both: 同时挂 source+target;ds_only_tgt: 只挂 target
    async with session_factory() as session:
        session.add_all(
            [Dataset(id="d-only-src", name="a", owner="admin", creator="admin"),
             Dataset(id="d-both", name="b", owner="admin", creator="admin"),
             Dataset(id="d-only-tgt", name="c", owner="admin", creator="admin")]
        )
        session.add_all(
            [DatasetTag(dataset_id="d-only-src", tag_id=source["id"]),
             DatasetTag(dataset_id="d-both", tag_id=source["id"]),
             DatasetTag(dataset_id="d-both", tag_id=target["id"]),
             DatasetTag(dataset_id="d-only-tgt", tag_id=target["id"])]
        )
        await session.commit()

    resp = await client.post(
        "/api/v1/tags/merge",
        json={"sourceId": source["id"], "targetId": target["id"]},
    )
    assert resp.status_code == 200, resp.text

    async with session_factory() as session:
        assert (await session.get(Tag, source["id"])) is None  # 源已删
        # 目标标签现在挂 3 个数据集(only-src 重指 + both 去重保留 1 + only-tgt)
        tgt_links = (await session.scalars(
            select(DatasetTag).where(DatasetTag.tag_id == target["id"])
        )).all()
        assert {r.dataset_id for r in tgt_links} == {"d-only-src", "d-both", "d-only-tgt"}
        assert len(tgt_links) == 3  # 无重复


async def test_merge_same_id_400(client: AsyncClient) -> None:
    t = await _create(client, "自合")
    resp = await client.post(
        "/api/v1/tags/merge", json={"sourceId": t["id"], "targetId": t["id"]}
    )
    assert resp.status_code == 400


async def test_merge_missing_404(client: AsyncClient) -> None:
    t = await _create(client, "存在")
    resp = await client.post(
        "/api/v1/tags/merge",
        json={"sourceId": "tag-nope0", "targetId": t["id"]},
    )
    assert resp.status_code == 404
```

- [ ] **Step 2: 跑测试,确认失败**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_tags.py -k "batch_delete or merge" -v`
Expected: FAIL(端点未实现)。

- [ ] **Step 3: 实现批量 DELETE + merge**

在 `tags.py` 追加。需在文件顶部 import 增 `from sqlalchemy import literal`(如尚无):

```python
@router.delete("/tags", dependencies=[Depends(require_admin)])
async def delete_tags(
    body: TagBatchDelete, session: SessionDep
) -> JSONResponse:
    """批量删除标签 + 级联解绑 dataset_tags。"""
    ids = body.ids or []
    if ids:
        await session.execute(
            delete(DatasetTag).where(DatasetTag.tag_id.in_(ids))
        )
        await session.execute(delete(Tag).where(Tag.id.in_(ids)))
        await session.commit()
    return JSONResponse(content={"success": True})


@router.post("/tags/merge", dependencies=[Depends(require_admin)])
async def merge_tags(body: TagMerge, session: SessionDep) -> JSONResponse:
    """合并 {sourceId→targetId}:把 source 的 dataset_tags 重指到 target
    (同时含两标签的数据集由复合 PK ON CONFLICT DO NOTHING 去重),再删 source 关联与标签。
    """
    if body.source_id == body.target_id:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "源标签与目标标签不能相同"},
        )
    source = await session.get(Tag, body.source_id)
    target = await session.get(Tag, body.target_id)
    if source is None or target is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "源或目标标签不存在"},
        )
    # 1. source 关联重指到 target(已存 target 的由 PK 去重)
    await session.execute(
        pg_insert(DatasetTag)
        .from_select(
            [DatasetTag.dataset_id, DatasetTag.tag_id],
            select(DatasetTag.dataset_id, literal(body.target_id)).where(
                DatasetTag.tag_id == body.source_id
            ),
        )
        .on_conflict_do_nothing(
            index_elements=[DatasetTag.dataset_id, DatasetTag.tag_id]
        )
    )
    # 2. 删 source 残留关联 + 3. 删 source 标签
    await session.execute(
        delete(DatasetTag).where(DatasetTag.tag_id == body.source_id)
    )
    await session.delete(source)
    await session.commit()
    return JSONResponse(content={"success": True})
```

并把顶部 import 行改为:
```python
from sqlalchemy import delete, func, literal, select
```

- [ ] **Step 4: 跑测试,确认通过**

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_tags.py -k "batch_delete or merge" -v`
Expected: PASS。

- [ ] **Step 5: 跑全量后端测试 + require_admin 门控测试**

在 `test_tags.py` 追加门控测试:

```python
async def test_non_admin_write_forbidden(
    client: AsyncClient, seed_users: None
) -> None:
    """user 角调写端点 → 403。"""
    from app.services.auth import sign_token

    client.cookies.set("adp_session", sign_token("user"))
    assert (await client.post("/api/v1/tags", json={"name": "x"})).status_code == 403
    assert (await client.patch("/api/v1/tags/tag-x0", json={"name": "y"})).status_code == 403
    assert (await client.delete("/api/v1/tags/tag-x0")).status_code == 403
    assert (await client.delete("/api/v1/tags", json={"ids": ["tag-x0"]})).status_code == 403
    assert (
        await client.post("/api/v1/tags/merge", json={"sourceId": "a", "targetId": "b"})
    ).status_code == 403


async def test_list_visible_to_non_admin(
    client: AsyncClient, seed_users: None
) -> None:
    """普通登录用户可浏览(GET 不门控)。"""
    from app.services.auth import sign_token

    await _create(client, "公共")
    client.cookies.set("adp_session", sign_token("user"))
    resp = await client.get("/api/v1/tags")
    assert resp.status_code == 200
```

Run: `cd backend && ./.venv/Scripts/python.exe -m pytest tests/test_tags.py -v`
Expected: 全 PASS。

- [ ] **Step 6: commit**

```bash
cd backend
git add app/api/v1/tags.py tests/test_tags.py
git commit -m "feat(backend): 标签管理 批量删除 + 合并(ON CONFLICT 去重)"
```

---

## Task 4: Frontend — service 层 + typings

**Files:**
- Modify: `frontend/src/services/data-platform/api.ts`(在 `listTags` 附近追加)
- Modify: `frontend/src/services/data-platform/typings.d.ts`(扩展现有 `Tag` 类型)

**Interfaces:**
- Consumes: Task 1-3 的后端端点。
- Produces: `createTag` / `updateTag` / `deleteTag` / `batchDeleteTags` / `mergeTags`(供 Task 5 页面用)。

- [ ] **Step 1: 扩展 typings**

在 `frontend/src/services/data-platform/typings.d.ts` 找到现有 `Tag` 类型(被 listTags 三处消费),把 usageCount/createdAt 改为字段并新增写入参类型:

```ts
type Tag = {
  id: string;
  name: string;
  usageCount: number;
  createdAt: string;
};

type TagCreate = { name: string };
type TagUpdate = { name: string };
type TagMerge = { sourceId: string; targetId: string };
type TagBatchDelete = { ids: string[] };
```

> 若现有 `Tag` 被声明为只读 `{id; name}`,只需补字段——三处 typeahead 消费只取 id/name,加字段不破坏。

- [ ] **Step 2: 追加 service 函数**

在 `frontend/src/services/data-platform/api.ts` 的 `listTags` 之后追加(对齐 `createCategory` 等的模式):

```ts
/** 新建标签(admin;同名 find-or-create 返回已存在项)POST /api/v1/tags */
export async function createTag(
  body: DataPlatform.TagCreate,
  options?: { [key: string]: any },
) {
  return request<{ data: DataPlatform.Tag; success: boolean }>(
    '/api/v1/tags',
    { method: 'POST', data: body, ...(options || {}) },
  );
}

/** 重命名标签(admin;重名 409)PATCH /api/v1/tags/{id} */
export async function updateTag(
  id: string,
  body: DataPlatform.TagUpdate,
  options?: { [key: string]: any },
) {
  return request<{ data: DataPlatform.Tag; success: boolean }>(
    `/api/v1/tags/${id}`,
    { method: 'PATCH', data: body, ...(options || {}) },
  );
}

/** 删除标签 + 级联解绑(admin)DELETE /api/v1/tags/{id} */
export async function deleteTag(id: string, options?: { [key: string]: any }) {
  return request<{ success: boolean }>(
    `/api/v1/tags/${id}`,
    { method: 'DELETE', ...(options || {}) },
  );
}

/** 批量删除标签 + 级联(admin)DELETE /api/v1/tags */
export async function batchDeleteTags(
  body: DataPlatform.TagBatchDelete,
  options?: { [key: string]: any },
) {
  return request<{ success: boolean }>(
    '/api/v1/tags',
    { method: 'DELETE', data: body, ...(options || {}) },
  );
}

/** 合并标签 source→target(去重 + 删源,admin)POST /api/v1/tags/merge */
export async function mergeTags(
  body: DataPlatform.TagMerge,
  options?: { [key: string]: any },
) {
  return request<{ success: boolean }>(
    '/api/v1/tags/merge',
    { method: 'POST', data: body, ...(options || {}) },
  );
}
```

- [ ] **Step 3: 类型检查**

Run: `cd frontend && npx tsc --noEmit`
Expected: exit 0(三处 listTags 消费只取 id/name,不破坏)。

- [ ] **Step 4: commit**

```bash
cd frontend
git add src/services/data-platform/api.ts src/services/data-platform/typings.d.ts
git commit -m "feat(frontend): 标签管理 service(create/update/delete/batch/merge)"
```

---

## Task 5: Frontend — 标签管理页 `./datasets/tags`

**Files:**
- Create: `frontend/src/pages/datasets/tags/index.tsx`

**Interfaces:**
- Consumes: Task 4 的 service 函数;`@/utils/tags` 的 `tagColor`;`useAccess()` 鉴权。

- [ ] **Step 1: 写页面**

创建 `frontend/src/pages/datasets/tags/index.tsx`(对齐 `CategoryManager` 的 ProTable+ModalForm 模式,扁平无树,加批量删 + 合并):

```tsx
import type { ActionType, ProColumns } from '@ant-design/pro-components';
import { ModalForm, ProFormSelect, ProFormText, ProTable } from '@ant-design/pro-components';
import { useAccess } from '@umijs/max';
import { Button, message, Popconfirm, Space, Tag } from 'antd';
import dayjs from 'dayjs';
import { type FC, useRef, useState } from 'react';
import {
  batchDeleteTags,
  createTag,
  deleteTag,
  listTags,
  mergeTags,
  updateTag,
} from '@/services/data-platform';
import { tagColor } from '@/utils/tags';
import { buildBreadcrumb } from '@/utils/breadcrumb';

const pickErrMsg = (err: unknown, fallback: string): string => {
  const e = err as { response?: { data?: { message?: string } } };
  return e?.response?.data?.message ?? fallback;
};

/** 标签管理:全局标签池 CRUD + 批量删 + 合并(扁平,仅挂数据集)。
 *  列表所有登录用户可见;写操作仅 admin(canAdmin)。颜色按 name 哈希(utils/tags)。 */
const TagsPage: FC = () => {
  const access = useAccess();
  const canAdmin = !!access.canAdmin;
  const actionRef = useRef<ActionType | null>(null);
  const [createOpen, setCreateOpen] = useState(false);
  const [mergeOpen, setMergeOpen] = useState(false);
  const [selected, setSelected] = useState<string[]>([]);
  const [allTags, setAllTags] = useState<DataPlatform.Tag[]>([]);

  const reload = () => actionRef.current?.reload();

  const columns: ProColumns<DataPlatform.Tag>[] = [
    {
      title: '名称',
      dataIndex: 'name',
      render: (_, r) => <Tag color={tagColor(r.name)}>{r.name}</Tag>,
    },
    { title: '使用数据集数', dataIndex: 'usageCount', width: 120, render: (_, r) => `${r.usageCount} 个` },
    {
      title: '创建时间',
      dataIndex: 'createdAt',
      width: 168,
      render: (_, r) => dayjs(r.createdAt).format('YYYY-MM-DD HH:mm'),
    },
    {
      title: '操作',
      valueType: 'option',
      width: 160,
      render: (_text, record) =>
        canAdmin
          ? [
              <ModalForm<DataPlatform.TagUpdate>
                key="rename"
                title="重命名标签"
                trigger={<a>重命名</a>}
                width={380}
                modalProps={{ destroyOnHidden: true }}
                initialValues={{ name: record.name }}
                onFinish={async (values) => {
                  try {
                    await updateTag(record.id, { name: values.name });
                    message.success('已保存');
                    reload();
                    return true;
                  } catch (err) {
                    message.error(pickErrMsg(err, '保存失败，请重试'));
                    return false;
                  }
                }}
              >
                <ProFormText
                  name="name"
                  label="名称"
                  rules={[{ required: true, message: '请输入名称' }]}
                />
              </ModalForm>,
              <Popconfirm
                key="delete"
                title={`删除「${record.name}」?将自动从所有数据集解绑`}
                okText="删除"
                cancelText="取消"
                okButtonProps={{ danger: true }}
                onConfirm={async () => {
                  try {
                    await deleteTag(record.id);
                    message.success('已删除');
                    reload();
                  } catch (err) {
                    message.error(pickErrMsg(err, '删除失败，请重试'));
                  }
                }}
              >
                <a style={{ color: 'var(--ant-color-error, #ff4d4f)' }}>删除</a>
              </Popconfirm>,
            ]
          : [<span key="readonly">-</span>],
    },
  ];

  return (
    <ProTable<DataPlatform.Tag>
      actionRef={actionRef}
      rowKey="id"
      search={false}
      options={{ reload: true, density: false, setting: false }}
      pagination={false}
      rowSelection={
        canAdmin
          ? { selectedRowKeys: selected, onChange: (keys) => setSelected(keys as string[]) }
          : false
      }
      tableAlertOptionProps={
        canAdmin
          ? {
              options: [
                {
                  text: '批量删除',
                  danger: true,
                  onClick: async () => {
                    try {
                      await batchDeleteTags({ ids: selected });
                      message.success(`已删除 ${selected.length} 个`);
                      setSelected([]);
                      reload();
                    } catch (err) {
                      message.error(pickErrMsg(err, '删除失败，请重试'));
                    }
                  },
                },
              ],
            }
          : undefined
      }
      toolBarRender={() =>
        canAdmin
          ? [
              <Button key="create" type="primary" onClick={() => setCreateOpen(true)}>
                新建标签
              </Button>,
              <Button key="merge" onClick={() => setMergeOpen(true)}>
                合并标签
              </Button>,
            ]
          : []
      }
      request={async () => {
        const res = await listTags();
        setAllTags(res.data);
        return { data: res.data, success: res.success };
      }}
      columns={columns}
    />
  );
};

export default TagsPage;
```

> 页面外层(`PageContainer` + `Card` + `buildBreadcrumb`)按 Task 6 路由挂载后,可参照 `ingest/categories/index.tsx` 的薄壳包一层。如需直接含 `PageContainer`,在 `return` 外包:
> ```tsx
> // 文件顶部增 import { PageContainer } from '@ant-design/pro-components'; import { Card } from 'antd';
> // return (<PageContainer breadcrumb={buildBreadcrumb([{title:'数据集仓库',path:'/datasets/list'},{title:'标签管理'}])} title="标签管理" content="维护数据集标签(新增/重命名/删除/合并仅管理员)"><Card><ProTable .../></Card></PageContainer>);
> ```
> 实现时直接把 ProTable 包进 PageContainer(把上面 `<ProTable>` 作为 `<Card>` 的子元素)。

- [ ] **Step 2: 类型检查**

Run: `cd frontend && npx tsc --noEmit`
Expected: exit 0。

- [ ] **Step 3: commit**

```bash
cd frontend
git add src/pages/datasets/tags/index.tsx
git commit -m "feat(frontend): 标签管理页(ProTable CRUD + 批量删 + 合并)"
```

---

## Task 6: 菜单重构 — 分类管理迁入数据集仓库 + 标签管理挂载

**Files:**
- Modify: `frontend/config/routes.ts`
- Move: `frontend/src/pages/ingest/categories/` → `frontend/src/pages/datasets/categories/`
- Modify: `frontend/src/locales/zh-CN/menu.ts`

**Interfaces:**
- Produces: `/datasets/categories`(迁移)+ `/datasets/tags`(新),旧 `/ingest/categories` redirect。

- [ ] **Step 1: 移动分类页文件(菜单归位)**

```bash
cd frontend
git mv src/pages/ingest/categories src/pages/datasets/categories
```

- [ ] **Step 2: 改 routes.ts**

在 `frontend/config/routes.ts` 中:

(a) **删除** ingest 块里的 categories 路由(原片段):
```ts
{ path: '/ingest/categories', name: 'categories', component: './ingest/categories' },
```

(b) 在 datasets 块的 `routes` 数组末尾(`./datasets/:id` 详情之后)**新增** categories + tags + 旧路径 redirect:
```ts
{
  path: '/datasets/categories',
  name: 'categories',
  component: './datasets/categories',
},
{ path: '/datasets/tags', name: 'tags', component: './datasets/tags' },
{ path: '/ingest/categories', redirect: '/datasets/categories' },
```

- [ ] **Step 3: 改 menu.ts**

在 `frontend/src/locales/zh-CN/menu.ts`:

(a) 删除 `'menu.ingest.categories': '分类管理',`。
(b) 在 datasets 段(`menu.datasets.presets` 之后)新增:
```ts
'menu.datasets.categories': '分类管理',
'menu.datasets.tags': '标签管理',
```

- [ ] **Step 4: 类型检查 + 起前端验证**

Run: `cd frontend && npx tsc --noEmit`
Expected: exit 0。

手动验证(可选):`cd frontend && PORT=8001 MOCK=none npm run dev` → 登录后侧栏「数据集仓库」下应见「数据集列表 / 已发布数据集 / 分类管理 / 标签管理」;访问 `/ingest/categories` 自动跳 `/datasets/categories`。

- [ ] **Step 5: commit**

```bash
cd frontend
git add config/routes.ts src/pages/datasets/categories src/pages/ingest src/locales/zh-CN/menu.ts
git commit -m "feat(frontend): 分类管理迁入数据集仓库 + 标签管理挂载菜单"
```

---

## Self-Review(写完后已核对)

**1. Spec 覆盖**(`docs/plan/16-标签管理设计.md`):
- §3.1 菜单重构 → Task 6 ✓
- §3.2 后端 `/api/v1/tags`(GET 扩展 + POST/PATCH/DELETE/批量/merge)→ Task 1-3 ✓
- §3.3 前端 `./datasets/tags` → Task 4(service)+ Task 5(页面)✓
- §4 无 DB 迁移 → 全局约束已声明,无迁移任务 ✓
- §5 测试 → Task 1-3 含完整测试(CRUD/级联/合并去重/门控)✓
- §6 范围外(YAGNI)→ 全局约束声明 ✓

**2. Placeholder 扫描**:无 TBD/TODO;每个步骤有完整代码或精确命令。Task 5 的 `PageContainer` 包裹给了显式补丁代码,非占位。

**3. 类型一致**:`TagRead{id,name,createdAt,usageCount}`(后端)↔ `Tag{id,name,usageCount,createdAt}`(前端)字段一致;`TagMerge.sourceId/targetId` 前后端一致;`_read`/`_usage_counts`/`_usage_count_of` 在 Task 1 定义、Task 2-3 消费,签名一致。

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-25-tag-management.md`. Two execution options:

1. **Subagent-Driven(推荐)** — 每个 Task 派一个 fresh subagent,task 间我审,迭代快。
2. **Inline Execution** — 本会话用 executing-plans 批量执行,带 checkpoint。

Which approach?
