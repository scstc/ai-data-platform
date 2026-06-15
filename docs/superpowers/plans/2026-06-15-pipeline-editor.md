# 算子流水线编辑页 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 用整页流水线编辑器(`/processing/editor`)取代"新建加工任务"模态,支持从算子库添加、拖拽排序、逐步配参、实时 YAML 预览、AI 生成流水线。

**Architecture:** 后端在既有 AI 双模式(heuristic/llm)上加 `generate_pipeline` 能力 + 一个新端点 `POST /api/v1/ai/generate-pipeline`,算子白名单与参数校验是确定性代码(`operator_catalog`)。前端把全局 `opCart` 从 `string[]` 升级为有序步骤 `{name,params}[]`,新增编辑页(三栏)与子组件,拖拽用 `@dnd-kit`。创建任务仍走既有 `POST /api/v1/jobs`。

**Tech Stack:** 后端 FastAPI + pytest;前端 Ant Design Pro v6 / React 19 / Umi Max + `@dnd-kit/core` `@dnd-kit/sortable`;YAML 用 `js-yaml`(前端预览)。

**Spec:** `docs/plan/05-算子流水线编辑页设计.md`

---

## File Structure

**后端**
- `backend/app/schemas/ai.py` — 修改:加 `PipelineStep` / `GeneratePipelineRequest` / `GeneratedPipeline`
- `backend/app/services/operator_catalog.py` — 修改:加 `ready_operator_context()` 与 `sanitize_pipeline()`(确定性白名单+参数过滤)
- `backend/app/services/ai/base.py` — 修改:加抽象 `generate_pipeline`
- `backend/app/services/ai/heuristic.py` — 修改:加 `generate_pipeline_from_goal()` + 方法
- `backend/app/services/ai/llm.py` — 修改:加 `_PIPELINE_SYSTEM_PROMPT` + 方法(失败回退)
- `backend/app/api/v1/ai.py` — 修改:加 `POST /ai/generate-pipeline`
- `backend/tests/unit/test_operator_catalog_pipeline.py` — 新建:`sanitize_pipeline` 单测
- `backend/tests/test_ai_pipeline_api.py` — 新建:端点 HTTP 测试

**前端**
- `frontend/package.json` — 修改:加 `@dnd-kit/core` `@dnd-kit/sortable` `@dnd-kit/utilities` `js-yaml`
- `frontend/src/models/opCart.ts` — 修改:`string[]` → `PipelineStep[]`
- `frontend/src/services/data-platform/typings.d.ts` — 修改:加 pipeline 相关类型
- `frontend/src/services/data-platform/api.ts` — 修改:加 `generatePipeline`
- `frontend/src/pages/processing/editor/index.tsx` — 新建:编辑页容器(三栏+顶/底+提交)
- `frontend/src/pages/processing/editor/OperatorLibrary.tsx` — 新建:左栏算子库
- `frontend/src/pages/processing/editor/PipelineSteps.tsx` — 新建:中栏拖拽有序步骤
- `frontend/src/pages/processing/editor/StepParamsForm.tsx` — 新建:右栏参数表单
- `frontend/src/pages/processing/editor/yaml.ts` — 新建:步骤→YAML 纯函数
- `frontend/config/routes.ts` — 修改:加 `/processing/editor`
- `frontend/src/pages/processing/index.tsx` — 修改:删模态,「新建任务」改为跳编辑页
- `frontend/src/pages/processing/market/index.tsx` — 修改:「加入」push 步骤对象;购物车跳编辑页
- `frontend/src/locales/zh-CN/menu.ts` / `en-US/menu.ts` — 修改:加 `menu.processing.editor`

---

## 后端

### Task 1: pipeline schemas

**Files:**
- Modify: `backend/app/schemas/ai.py`(在 `# ---- 问答 ----` 段之前插入)

- [ ] **Step 1: 加 schema**

在 `backend/app/schemas/ai.py` 的 `# ---- 问答 ----` 之前插入:

```python
# ---- 生成算子流水线 ----
class GeneratePipelineRequest(CamelModel):
    """生成流水线请求:goal 为目标场景文本,datasetVersionId 可选(仅作上下文)。"""

    goal: str
    dataset_version_id: str | None = None


class PipelineStep(CamelModel):
    """流水线中的一步:算子名 + 参数(参数为空则 {})。"""

    name: str
    params: dict[str, Any] = {}


class GeneratedPipeline(CamelModel):
    """AI 生成的流水线(作为 {data, success} 的 data)。"""

    operators: list[PipelineStep]
    explanation: str
```

- [ ] **Step 2: 校验导入**

确认文件顶部已有 `from typing import Any`(已存在)。无需新增导入。

- [ ] **Step 3: Commit**

```bash
git add backend/app/schemas/ai.py
git commit -m "feat(backend): pipeline 生成的 AI schema"
```

---

### Task 2: operator_catalog 的 ready 上下文 + 确定性校验

**Files:**
- Modify: `backend/app/services/operator_catalog.py`(文件末尾追加)
- Test: `backend/tests/unit/test_operator_catalog_pipeline.py`

- [ ] **Step 1: 写失败测试**

新建 `backend/tests/unit/test_operator_catalog_pipeline.py`:

```python
"""sanitize_pipeline:只保留存在且 runnable==ready 的算子,并裁掉非法参数键。"""

from __future__ import annotations

from app.services import operator_catalog as oc


def test_sanitize_drops_unknown_and_non_ready() -> None:
    steps = [
        {"name": "__nope__", "params": {}},          # 不存在 → 丢弃
        {"name": "document_deduplicator", "params": {}},  # ready → 保留
    ]
    out = oc.sanitize_pipeline(steps)
    names = [s["name"] for s in out]
    assert "__nope__" not in names
    assert "document_deduplicator" in names


def test_sanitize_strips_invalid_param_keys() -> None:
    # text_length_filter 有 min_len/max_len;塞一个不存在的键应被裁掉
    steps = [{"name": "text_length_filter", "params": {"min_len": 10, "BOGUS": 1}}]
    out = oc.sanitize_pipeline(steps)
    assert out and out[0]["name"] == "text_length_filter"
    assert "BOGUS" not in out[0]["params"]
    assert out[0]["params"].get("min_len") == 10


def test_ready_operator_context_only_ready() -> None:
    ctx = oc.ready_operator_context()
    assert ctx and all(c["name"] for c in ctx)
    # 上下文里出现的算子必须都是 ready(用 runnable_reason 反查:ready 时为 None)
    assert oc.runnable_reason(ctx[0]["name"]) is None
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && uv run pytest tests/unit/test_operator_catalog_pipeline.py -v`
Expected: FAIL（`AttributeError: module ... has no attribute 'sanitize_pipeline'`）

- [ ] **Step 3: 实现**

在 `backend/app/services/operator_catalog.py` 末尾追加:

```python
# ---------------------------------------------------------------------------
# AI 流水线生成:ready 算子上下文 + 确定性校验(白名单 + 合法参数键)
# ---------------------------------------------------------------------------
def ready_operator_context() -> list[dict[str, Any]]:
    """供 LLM 提示的 ready 算子清单:name + 中文标签 + 场景 + 合法参数名。"""
    ctx: list[dict[str, Any]] = []
    for op in all_operators():
        if op["runnable"] != "ready":
            continue
        ctx.append(
            {
                "name": op["name"],
                "label": op.get("zh_label") or op["name"],
                "scenario": op.get("scenario_group") or "",
                "params": [p["name"] for p in op.get("params", [])],
            }
        )
    return ctx


def sanitize_pipeline(
    steps: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """裁剪到可执行流水线:丢弃未知/非 ready 算子,删除不在该算子参数表里的键。"""
    result: list[dict[str, Any]] = []
    for step in steps:
        name = step.get("name")
        op = get_operator(name) if name else None
        if op is None or op["runnable"] != "ready":
            continue
        allowed = {p["name"] for p in op.get("params", [])}
        raw = step.get("params") or {}
        params = {k: v for k, v in raw.items() if k in allowed}
        result.append({"name": name, "params": params})
    return result
```

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && uv run pytest tests/unit/test_operator_catalog_pipeline.py -v`
Expected: PASS（3 passed）

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/operator_catalog.py backend/tests/unit/test_operator_catalog_pipeline.py
git commit -m "feat(backend): ready 算子上下文与流水线确定性校验"
```

---

### Task 3: AIProvider.generate_pipeline(抽象 + 启发式 + LLM)

**Files:**
- Modify: `backend/app/services/ai/base.py`
- Modify: `backend/app/services/ai/heuristic.py`
- Modify: `backend/app/services/ai/llm.py`

- [ ] **Step 1: base.py 加抽象方法**

在 `backend/app/services/ai/base.py` 的 `qa` 抽象方法之后加:

```python
    @abstractmethod
    async def generate_pipeline(
        self, goal: str, ready_ops: list[dict[str, Any]]
    ) -> dict[str, Any]:
        """据目标场景生成算子流水线,返回 GeneratedPipeline 形状 dict
        ({"operators": [{"name","params"}], "explanation": str})。
        ready_ops 为可选算子上下文(name/label/scenario/params)。"""
        raise NotImplementedError
```

- [ ] **Step 2: heuristic.py 实现**

在 `backend/app/services/ai/heuristic.py` 顶部已有 import 区不变;在 `answer_question` 之后(类定义之前)加模块函数:

```python
# goal 关键词 → 场景分组(命中则取该场景下若干 ready 算子)
_GOAL_SCENARIO_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("去重", "重复", "dedup"), "去重"),
    (("脱敏", "隐私", "敏感"), "隐私脱敏"),
    (("繁简", "中文", "简体", "繁体"), "中文处理"),
    (("质量", "过滤", "筛"), "质量过滤"),
    (("清洗", "html", "url", "空白"), "文本清洗"),
]


def generate_pipeline_from_goal(
    goal_raw: str, ready_ops: list[dict[str, Any]]
) -> dict[str, Any]:
    """启发式:按 goal 关键词命中场景,取该场景下最多 4 个 ready 算子串成流水线。"""
    goal = (goal_raw or "").lower()
    scenarios: list[str] = [s for kws, s in _GOAL_SCENARIO_KEYWORDS if any(k in goal for k in kws)]
    if not scenarios:
        scenarios = ["文本清洗", "质量过滤"]
    picked: list[dict[str, Any]] = []
    for sc in scenarios:
        for op in ready_ops:
            if op.get("scenario") == sc and op["name"] not in {p["name"] for p in picked}:
                picked.append({"name": op["name"], "params": {}})
            if len(picked) >= 6:
                break
    explanation = f"按目标「{goal_raw}」匹配场景 {scenarios},推荐 {len(picked)} 个可运行算子(启发式,可自行调整)。"
    return {"operators": picked, "explanation": explanation}
```

并在 `HeuristicProvider` 类里(`qa` 方法之后)加:

```python
    async def generate_pipeline(
        self, goal: str, ready_ops: list[dict[str, Any]]
    ) -> dict[str, Any]:
        return generate_pipeline_from_goal(goal, ready_ops)
```

确认 heuristic.py 顶部已有 `from typing import Any`(已存在)。

- [ ] **Step 3: llm.py 实现**

在 `backend/app/services/ai/llm.py` 的 system prompt 区(约 21 行)加:

```python
_PIPELINE_SYSTEM_PROMPT = (
    "你是 data-juicer 数据加工流水线助手。用户给出加工目标,你只能从"
    "【可用算子清单】里选择算子,按合理顺序组成线性流水线。"
    "严格只输出 JSON:{\"operators\":[{\"name\":\"<算子名>\",\"params\":{}}],"
    "\"explanation\":\"<一句中文说明>\"}。"
    "name 必须是清单中的算子名;不确定参数就给 {};不要编造清单外的算子。"
)
```

在 `OpenAICompatProvider` 类里(`qa` 之后)加:

```python
    async def generate_pipeline(
        self, goal: str, ready_ops: list[dict[str, Any]]
    ) -> dict[str, Any]:
        try:
            catalog = "\n".join(
                f"- {o['name']} | {o['label']} | 场景:{o['scenario']} | 参数:{','.join(o['params']) or '无'}"
                for o in ready_ops
            )
            user = f"加工目标:{goal}\n\n【可用算子清单】\n{catalog}"
            return await self._chat_json(_PIPELINE_SYSTEM_PROMPT, user)
        except Exception:
            return await self._heuristic.generate_pipeline(goal, ready_ops)
```

确认 llm.py 顶部已有 `from typing import Any`(已存在)。

- [ ] **Step 4: 全量后端测试不破**

Run: `cd backend && uv run pytest -q`
Expected: 现有用例全过(新增 provider 方法不影响既有端点)。

- [ ] **Step 5: Commit**

```bash
git add backend/app/services/ai/base.py backend/app/services/ai/heuristic.py backend/app/services/ai/llm.py
git commit -m "feat(backend): AIProvider.generate_pipeline(启发式+LLM)"
```

---

### Task 4: 端点 POST /ai/generate-pipeline

**Files:**
- Modify: `backend/app/api/v1/ai.py`
- Test: `backend/tests/test_ai_pipeline_api.py`

- [ ] **Step 1: 写失败测试**

新建 `backend/tests/test_ai_pipeline_api.py`:

```python
"""generate-pipeline 端点(默认启发式 provider)。"""

from __future__ import annotations

import pytest
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


async def test_generate_pipeline_dedup_goal(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/ai/generate-pipeline", json={"goal": "中文语料去重"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    data = body["data"]
    assert isinstance(data["operators"], list) and data["operators"]
    # 每步含 name/params,且 name 都是 ready 算子(经 sanitize)
    from app.services import operator_catalog as oc
    for step in data["operators"]:
        assert set(step) == {"name", "params"}
        assert oc.runnable_reason(step["name"]) is None
    assert isinstance(data["explanation"], str) and data["explanation"]


async def test_generate_pipeline_empty_goal_still_ok(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/ai/generate-pipeline", json={"goal": ""})
    assert resp.status_code == 200
    assert resp.json()["data"]["operators"]  # 兜底场景仍给非空流水线
```

- [ ] **Step 2: 运行确认失败**

Run: `cd backend && uv run pytest tests/test_ai_pipeline_api.py -v`
Expected: FAIL（404 Not Found，端点未注册）

- [ ] **Step 3: 实现端点**

在 `backend/app/api/v1/ai.py`:导入处补 schema 与 catalog:

```python
from app.schemas.ai import (
    GeneratedPipeline,
    GeneratedTaskConfig,
    GeneratePipelineRequest,
    GenerateTaskRequest,
    InferredSchema,
    InferSchemaRequest,
    QaAnswer,
    QaRequest,
)
from app.services import operator_catalog as oc
```

在 `QaResponse` 之后加响应模型:

```python
class GeneratePipelineResponse(CamelModel):
    """流水线生成响应。"""

    data: GeneratedPipeline
    success: bool = True
```

在 `qa` 端点之后加:

```python
@router.post("/generate-pipeline", response_model=GeneratePipelineResponse)
async def generate_pipeline(
    body: GeneratePipelineRequest,
    provider: ProviderDep,
) -> GeneratePipelineResponse:
    """据目标场景生成算子流水线(LLM 或启发式),经确定性校验后返回。"""
    ready = oc.ready_operator_context()
    raw = await provider.generate_pipeline(body.goal, ready)
    steps = oc.sanitize_pipeline(raw.get("operators", []))
    explanation = str(raw.get("explanation", ""))
    return GeneratePipelineResponse(
        data=GeneratedPipeline.model_validate(
            {"operators": steps, "explanation": explanation}
        )
    )
```

- [ ] **Step 4: 运行确认通过**

Run: `cd backend && uv run pytest tests/test_ai_pipeline_api.py -v`
Expected: PASS（2 passed）

- [ ] **Step 5: 全量 + lint**

Run: `cd backend && uv run pytest -q && uv run ruff check .`
Expected: 全过、无 lint 错误。

- [ ] **Step 6: Commit**

```bash
git add backend/app/api/v1/ai.py backend/tests/test_ai_pipeline_api.py
git commit -m "feat(backend): POST /ai/generate-pipeline 端点"
```

---

## 前端

### Task 5: 依赖 + 类型 + API

**Files:**
- Modify: `frontend/package.json`
- Modify: `frontend/src/services/data-platform/typings.d.ts`
- Modify: `frontend/src/services/data-platform/api.ts`

- [ ] **Step 1: 装依赖**

Run:
```bash
cd frontend && npm install @dnd-kit/core @dnd-kit/sortable @dnd-kit/utilities js-yaml @types/js-yaml --save
```
Expected: 写入 package.json dependencies。

- [ ] **Step 2: 加类型**

在 `frontend/src/services/data-platform/typings.d.ts` 的 `CatalogOperator` 之后加:

```typescript
  /** 流水线步骤:算子名 + 参数 */
  type PipelineStep = {
    name: string;
    params: Record<string, unknown>;
  };

  /** AI 生成流水线入参 */
  type GeneratePipelineParams = {
    goal: string;
    datasetVersionId?: string;
  };

  /** AI 生成流水线结果 */
  type GeneratedPipeline = {
    operators: PipelineStep[];
    explanation: string;
  };
```

- [ ] **Step 3: 加 API**

在 `frontend/src/services/data-platform/api.ts` 末尾(其它 ai 函数附近)加:

```typescript
/** AI:据目标生成算子流水线 POST /api/v1/ai/generate-pipeline */
export async function generatePipeline(
  body: DataPlatform.GeneratePipelineParams,
) {
  return request<{ data: DataPlatform.GeneratedPipeline; success: boolean }>(
    '/api/v1/ai/generate-pipeline',
    { method: 'POST', data: body },
  );
}
```

- [ ] **Step 4: 类型检查**

Run: `cd frontend && npm run tsc`
Expected: 通过(忽略既有无关告警)。

- [ ] **Step 5: Commit**

```bash
git add frontend/package.json frontend/package-lock.json frontend/src/services/data-platform/typings.d.ts frontend/src/services/data-platform/api.ts
git commit -m "feat(frontend): 流水线类型/接口 + dnd-kit/js-yaml 依赖"
```

---

### Task 6: opCart 升级为有序步骤

**Files:**
- Modify: `frontend/src/models/opCart.ts`

- [ ] **Step 1: 重写 model**

`frontend/src/models/opCart.ts` 全量替换为:

```typescript
import { useCallback, useState } from 'react';

/**
 * 算子市场 → 流水线编辑页的"待编排步骤"购物车。
 * Umi Max 全局 model:市场页加入算子(空参数步骤),编辑页带出并继续增删/排序/配参。
 */
export default function useOpCart() {
  const [steps, setSteps] = useState<DataPlatform.PipelineStep[]>([]);

  const add = useCallback(
    (name: string) =>
      setSteps((prev) =>
        prev.some((s) => s.name === name)
          ? prev
          : [...prev, { name, params: {} }],
      ),
    [],
  );
  const remove = useCallback(
    (idx: number) => setSteps((prev) => prev.filter((_, i) => i !== idx)),
    [],
  );
  const reorder = useCallback(
    (from: number, to: number) =>
      setSteps((prev) => {
        const next = [...prev];
        const [moved] = next.splice(from, 1);
        next.splice(to, 0, moved);
        return next;
      }),
    [],
  );
  const updateParams = useCallback(
    (idx: number, params: Record<string, unknown>) =>
      setSteps((prev) =>
        prev.map((s, i) => (i === idx ? { ...s, params } : s)),
      ),
    [],
  );
  const replaceAll = useCallback(
    (next: DataPlatform.PipelineStep[]) => setSteps(next),
    [],
  );
  const clear = useCallback(() => setSteps([]), []);

  return { steps, add, remove, reorder, updateParams, replaceAll, clear };
}
```

- [ ] **Step 2: 找出所有旧 API 调用点**

Run: `cd frontend && grep -rn "useModel('opCart')\|useModel(\"opCart\")" src/`
Expected: 列出市场页与加工页两处(下游 Task 8/9 会改它们)。本步只确认范围。

- [ ] **Step 3: Commit**

```bash
git add frontend/src/models/opCart.ts
git commit -m "refactor(frontend): opCart 升级为有序步骤 {name,params}[]"
```

> 注:此提交后 market/processing 页会有 TS 报错,Task 7-9 修复;按顺序执行即可。

---

### Task 7: YAML 纯函数 + 三个子组件

**Files:**
- Create: `frontend/src/pages/processing/editor/yaml.ts`
- Create: `frontend/src/pages/processing/editor/StepParamsForm.tsx`
- Create: `frontend/src/pages/processing/editor/PipelineSteps.tsx`
- Create: `frontend/src/pages/processing/editor/OperatorLibrary.tsx`
- Test: `frontend/src/pages/processing/editor/yaml.test.ts`

- [ ] **Step 1: 写 YAML 纯函数的失败测试**

新建 `frontend/src/pages/processing/editor/yaml.test.ts`:

```typescript
import { describe, expect, it } from 'vitest';
import { stepsToYaml } from './yaml';

describe('stepsToYaml', () => {
  it('无参算子值为 null,有参算子展开为对象', () => {
    const y = stepsToYaml([
      { name: 'document_deduplicator', params: {} },
      { name: 'text_length_filter', params: { min_len: 10 } },
    ]);
    expect(y).toContain('process:');
    expect(y).toContain('document_deduplicator: null');
    expect(y).toContain('text_length_filter:');
    expect(y).toContain('min_len: 10');
  });

  it('空流水线给出占位 process: []', () => {
    expect(stepsToYaml([])).toContain('process: []');
  });
});
```

- [ ] **Step 2: 运行确认失败**

Run: `cd frontend && npx vitest run src/pages/processing/editor/yaml.test.ts`
Expected: FAIL（找不到 ./yaml）

- [ ] **Step 3: 实现 yaml.ts**

```typescript
import yaml from 'js-yaml';

/** 把有序步骤渲染为 data-juicer process 配置预览(与后端 build_config 同构)。 */
export function stepsToYaml(steps: DataPlatform.PipelineStep[]): string {
  const process = steps.map((s) => ({
    [s.name]: Object.keys(s.params).length ? s.params : null,
  }));
  return yaml.dump({ process }, { noRefs: true, sortKeys: false });
}
```

- [ ] **Step 4: 运行确认通过**

Run: `cd frontend && npx vitest run src/pages/processing/editor/yaml.test.ts`
Expected: PASS（2 passed）

- [ ] **Step 5: 实现 StepParamsForm.tsx**

```tsx
import { InputNumber, Switch, Input, Form, Typography, Empty } from 'antd';

const { Text } = Typography;

/** 右栏:按选中算子的参数定义渲染表单,改动回填到该步骤的 params。 */
const StepParamsForm: React.FC<{
  op?: DataPlatform.CatalogOperator;
  params: Record<string, unknown>;
  onChange: (params: Record<string, unknown>) => void;
}> = ({ op, params, onChange }) => {
  if (!op) {
    return <Empty description="从中间选择一个步骤以配置参数" />;
  }
  if (!op.params?.length) {
    return <Text type="secondary">该算子无可配参数</Text>;
  }
  const set = (k: string, v: unknown) => onChange({ ...params, [k]: v });
  return (
    <Form layout="vertical">
      {op.params.map((p) => {
        const val = params[p.name];
        const t = p.type || '';
        return (
          <Form.Item key={p.name} label={p.name} tooltip={p.desc} help={p.default ? `默认 ${p.default}` : undefined}>
            {t.includes('bool') ? (
              <Switch checked={Boolean(val)} onChange={(v) => set(p.name, v)} />
            ) : t.includes('int') || t.includes('float') ? (
              <InputNumber
                style={{ width: '100%' }}
                value={val as number}
                onChange={(v) => set(p.name, v)}
              />
            ) : (
              <Input
                value={val as string}
                onChange={(e) => set(p.name, e.target.value)}
              />
            )}
          </Form.Item>
        );
      })}
    </Form>
  );
};

export default StepParamsForm;
```

- [ ] **Step 6: 实现 PipelineSteps.tsx**

```tsx
import { DeleteOutlined, HolderOutlined } from '@ant-design/icons';
import {
  DndContext,
  PointerSensor,
  useSensor,
  useSensors,
  type DragEndEvent,
} from '@dnd-kit/core';
import {
  SortableContext,
  useSortable,
  verticalListSortingStrategy,
} from '@dnd-kit/sortable';
import { CSS } from '@dnd-kit/utilities';
import { Button, Empty, Typography } from 'antd';

const { Text } = Typography;

const Row: React.FC<{
  id: string;
  index: number;
  label: string;
  active: boolean;
  onSelect: () => void;
  onRemove: () => void;
}> = ({ id, index, label, active, onSelect, onRemove }) => {
  const { attributes, listeners, setNodeRef, transform, transition } =
    useSortable({ id });
  return (
    <div
      ref={setNodeRef}
      style={{
        transform: CSS.Transform.toString(transform),
        transition,
        display: 'flex',
        alignItems: 'center',
        gap: 8,
        padding: '8px 12px',
        marginBottom: 8,
        borderRadius: 6,
        border: active ? '1px solid #1677ff' : '1px solid #f0f0f0',
        background: active ? '#e6f4ff' : '#fff',
        cursor: 'pointer',
      }}
      onClick={onSelect}
    >
      <span {...attributes} {...listeners} style={{ cursor: 'grab', color: '#999' }}>
        <HolderOutlined />
      </span>
      <Text style={{ flex: 1 }}>
        {index + 1}. {label}
      </Text>
      <Button
        type="text"
        size="small"
        danger
        icon={<DeleteOutlined />}
        onClick={(e) => {
          e.stopPropagation();
          onRemove();
        }}
      />
    </div>
  );
};

/** 中栏:有序步骤列表,拖拽排序,点选高亮。 */
const PipelineSteps: React.FC<{
  steps: DataPlatform.PipelineStep[];
  labelOf: (name: string) => string;
  activeIdx: number;
  onSelect: (idx: number) => void;
  onRemove: (idx: number) => void;
  onReorder: (from: number, to: number) => void;
}> = ({ steps, labelOf, activeIdx, onSelect, onRemove, onReorder }) => {
  const sensors = useSensors(useSensor(PointerSensor));
  if (!steps.length) {
    return <Empty description="从左侧算子库添加算子,组成处理流水线" style={{ padding: '48px 0' }} />;
  }
  const ids = steps.map((s, i) => `${s.name}-${i}`);
  const onDragEnd = (e: DragEndEvent) => {
    const { active, over } = e;
    if (!over || active.id === over.id) return;
    onReorder(ids.indexOf(String(active.id)), ids.indexOf(String(over.id)));
  };
  return (
    <DndContext sensors={sensors} onDragEnd={onDragEnd}>
      <SortableContext items={ids} strategy={verticalListSortingStrategy}>
        {steps.map((s, i) => (
          <Row
            key={ids[i]}
            id={ids[i]}
            index={i}
            label={labelOf(s.name)}
            active={i === activeIdx}
            onSelect={() => onSelect(i)}
            onRemove={() => onRemove(i)}
          />
        ))}
      </SortableContext>
    </DndContext>
  );
};

export default PipelineSteps;
```

- [ ] **Step 7: 实现 OperatorLibrary.tsx**

```tsx
import { PlusOutlined } from '@ant-design/icons';
import { Button, Input, List, Select, Space, Switch, Tag, Typography } from 'antd';
import { useEffect, useMemo, useState } from 'react';
import { listOperatorCatalog, getOperatorCatalogMeta } from '@/services/data-platform';

const { Text } = Typography;

/** 左栏:检索/场景/只看可运行,点 + 添加算子到流水线。 */
const OperatorLibrary: React.FC<{ onAdd: (name: string) => void }> = ({ onAdd }) => {
  const [scenarios, setScenarios] = useState<Record<string, number>>({});
  const [scenario, setScenario] = useState<string>();
  const [keyword, setKeyword] = useState<string>();
  const [onlyReady, setOnlyReady] = useState(true);
  const [data, setData] = useState<DataPlatform.CatalogOperator[]>([]);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    getOperatorCatalogMeta().then((r) => setScenarios(r.data.byScenario ?? {}));
  }, []);

  useEffect(() => {
    setLoading(true);
    listOperatorCatalog({
      scenario,
      keyword,
      runnable: onlyReady ? 'ready' : undefined,
      current: 1,
      pageSize: 200,
    })
      .then((r) => setData(r.data))
      .finally(() => setLoading(false));
  }, [scenario, keyword, onlyReady]);

  const scenarioOptions = useMemo(
    () =>
      Object.entries(scenarios)
        .sort((a, b) => b[1] - a[1])
        .map(([name, count]) => ({ label: `${name} (${count})`, value: name })),
    [scenarios],
  );

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <Space direction="vertical" style={{ width: '100%', marginBottom: 8 }}>
        <Input.Search allowClear placeholder="搜索算子" onSearch={(v) => setKeyword(v || undefined)} />
        <Select
          allowClear
          placeholder="全部场景"
          style={{ width: '100%' }}
          options={scenarioOptions}
          value={scenario}
          onChange={setScenario}
        />
        <Space size={6}>
          <Switch size="small" checked={onlyReady} onChange={setOnlyReady} />
          <Text type="secondary">只看可运行</Text>
        </Space>
      </Space>
      <div style={{ flex: 1, overflow: 'auto' }}>
        <List
          loading={loading}
          size="small"
          dataSource={data}
          renderItem={(op) => (
            <List.Item
              actions={[
                <Button
                  key="add"
                  type="text"
                  size="small"
                  icon={<PlusOutlined />}
                  disabled={op.runnable !== 'ready'}
                  onClick={() => onAdd(op.name)}
                />,
              ]}
            >
              <List.Item.Meta
                title={<Text style={{ fontSize: 13 }}>{op.zhLabel}</Text>}
                description={
                  <Text type="secondary" style={{ fontSize: 11, fontFamily: 'monospace' }}>
                    {op.name}
                  </Text>
                }
              />
              {op.runnable !== 'ready' && <Tag>{op.runnable}</Tag>}
            </List.Item>
          )}
        />
      </div>
    </div>
  );
};

export default OperatorLibrary;
```

- [ ] **Step 8: 类型检查 + Commit**

Run: `cd frontend && npm run tsc`
Expected: 这些新文件无类型错误(editor/index 尚未引入,组件独立可编译)。

```bash
git add frontend/src/pages/processing/editor/
git commit -m "feat(frontend): 流水线编辑页子组件(YAML/参数/步骤/算子库)"
```

---

### Task 8: 编辑页容器 + 路由

**Files:**
- Create: `frontend/src/pages/processing/editor/index.tsx`
- Modify: `frontend/config/routes.ts`
- Modify: `frontend/src/locales/zh-CN/menu.ts`、`frontend/src/locales/en-US/menu.ts`

- [ ] **Step 1: 实现 editor/index.tsx**

```tsx
import { PageContainer } from '@ant-design/pro-components';
import { history, useModel } from '@umijs/max';
import {
  Button,
  Card,
  Col,
  Input,
  Modal,
  Row,
  Select,
  Space,
  Typography,
  message,
} from 'antd';
import { useEffect, useMemo, useState } from 'react';
import {
  createJob,
  generatePipeline,
  listDatasets,
  getDataset,
  listOperatorCatalog,
} from '@/services/data-platform';
import OperatorLibrary from './OperatorLibrary';
import PipelineSteps from './PipelineSteps';
import StepParamsForm from './StepParamsForm';
import { stepsToYaml } from './yaml';

const { Text, Paragraph } = Typography;

const Editor: React.FC = () => {
  const { steps, add, remove, reorder, updateParams, replaceAll, clear } =
    useModel('opCart');
  const [name, setName] = useState('');
  const [datasetId, setDatasetId] = useState<string>();
  const [versionId, setVersionId] = useState<string>();
  const [datasets, setDatasets] = useState<DataPlatform.Dataset[]>([]);
  const [versions, setVersions] = useState<DataPlatform.DatasetVersion[]>([]);
  const [opMap, setOpMap] = useState<Record<string, DataPlatform.CatalogOperator>>({});
  const [activeIdx, setActiveIdx] = useState(0);
  const [submitting, setSubmitting] = useState(false);

  // 算子元信息(供 label/params 渲染):一次取全量 ready+非 ready 名称映射
  useEffect(() => {
    listOperatorCatalog({ current: 1, pageSize: 1000 }).then((r) => {
      setOpMap(Object.fromEntries(r.data.map((o) => [o.name, o])));
    });
  }, []);

  useEffect(() => {
    listDatasets({ current: 1, pageSize: 1000 }).then((r) => setDatasets(r.data));
  }, []);

  useEffect(() => {
    if (!datasetId) {
      setVersions([]);
      setVersionId(undefined);
      return;
    }
    getDataset(datasetId).then((r) => setVersions(r.data.versions ?? []));
  }, [datasetId]);

  const labelOf = (n: string) => opMap[n]?.zhLabel || n;
  const activeStep = steps[activeIdx];
  const activeOp = activeStep ? opMap[activeStep.name] : undefined;

  const yamlText = useMemo(() => stepsToYaml(steps), [steps]);

  const onGenerate = () => {
    let goal = '';
    Modal.confirm({
      title: 'AI 生成流水线',
      content: (
        <Input.TextArea
          placeholder="描述加工目标,如:中文语料清洗去重"
          onChange={(e) => {
            goal = e.target.value;
          }}
        />
      ),
      onOk: async () => {
        const r = await generatePipeline({ goal, datasetVersionId: versionId });
        replaceAll(r.data.operators);
        setActiveIdx(0);
        message.success(r.data.explanation || '已生成流水线');
      },
    });
  };

  const onSubmit = async () => {
    if (!name.trim()) return message.warning('请填写任务名');
    if (!versionId) return message.warning('请选择数据集版本');
    if (!steps.length) return message.warning('至少添加一个算子');
    setSubmitting(true);
    try {
      await createJob({
        name,
        datasetVersionId: versionId,
        operators: steps,
      });
      message.success('加工任务已创建');
      clear();
      history.push('/processing/jobs');
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <PageContainer
      header={{ title: '新建加工任务' }}
      extra={[
        <Button key="ai" onClick={onGenerate}>
          ✨ AI 生成
        </Button>,
        <Button key="submit" type="primary" loading={submitting} onClick={onSubmit}>
          创建任务
        </Button>,
      ]}
    >
      <Space style={{ marginBottom: 16 }} wrap>
        <Input
          placeholder="任务名"
          style={{ width: 220 }}
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <Select
          placeholder="选择数据集"
          style={{ width: 220 }}
          value={datasetId}
          onChange={(v) => setDatasetId(v)}
          options={datasets.map((d) => ({ label: d.name, value: d.id }))}
        />
        <Select
          placeholder="选择版本"
          style={{ width: 200 }}
          value={versionId}
          onChange={setVersionId}
          options={versions.map((v) => ({ label: `v${v.versionNo}`, value: v.id }))}
        />
      </Space>

      <Row gutter={16}>
        <Col span={7}>
          <Card title="算子库" size="small" styles={{ body: { height: 460, padding: 12 } }}>
            <OperatorLibrary onAdd={add} />
          </Card>
        </Col>
        <Col span={10}>
          <Card title="流水线(拖拽排序)" size="small" styles={{ body: { height: 460, overflow: 'auto' } }}>
            <PipelineSteps
              steps={steps}
              labelOf={labelOf}
              activeIdx={activeIdx}
              onSelect={setActiveIdx}
              onRemove={(i) => {
                remove(i);
                setActiveIdx(0);
              }}
              onReorder={reorder}
            />
          </Card>
        </Col>
        <Col span={7}>
          <Card title="参数" size="small" styles={{ body: { height: 460, overflow: 'auto' } }}>
            <StepParamsForm
              op={activeOp}
              params={activeStep?.params ?? {}}
              onChange={(p) => updateParams(activeIdx, p)}
            />
          </Card>
        </Col>
      </Row>

      <Card title="YAML 预览" size="small" style={{ marginTop: 16 }}>
        <Paragraph>
          <pre style={{ margin: 0, fontSize: 12 }}>{yamlText}</pre>
        </Paragraph>
        <Text type="secondary" style={{ fontSize: 12 }}>
          仅预览;真实配置在创建时由后端生成。
        </Text>
      </Card>
    </PageContainer>
  );
};

export default Editor;
```

- [ ] **Step 2: 路由**

在 `frontend/config/routes.ts` 的 `/processing` 子路由组里(`/processing/jobs` 之后)加:

```typescript
      {
        path: '/processing/editor',
        name: 'editor',
        component: './processing/editor',
        hideInMenu: true,
      },
```

- [ ] **Step 3: i18n**

`frontend/src/locales/zh-CN/menu.ts` 加:`'menu.processing.editor': '新建加工任务',`
`frontend/src/locales/en-US/menu.ts` 加:`'menu.processing.editor': 'New Processing Job',`

- [ ] **Step 4: 类型检查**

Run: `cd frontend && npm run tsc`
Expected: 通过。(已核对 api.ts 导出名:`listDatasets` 返回 PageResult、`getDataset(id)` 返回 `{data: DatasetDetail}`、`createJob` 返回 `{data: Job}`。)

- [ ] **Step 5: Commit**

```bash
git add frontend/src/pages/processing/editor/index.tsx frontend/config/routes.ts frontend/src/locales/zh-CN/menu.ts frontend/src/locales/en-US/menu.ts
git commit -m "feat(frontend): 算子流水线编辑页容器 + 路由"
```

---

### Task 9: 接入入口(市场购物车 + 加工列表),移除旧模态

**Files:**
- Modify: `frontend/src/pages/processing/market/index.tsx`
- Modify: `frontend/src/pages/processing/index.tsx`

- [ ] **Step 1: 市场页适配新 opCart**

在 `frontend/src/pages/processing/market/index.tsx`:
- 把 `const { ops: cart, add, clear } = useModel('opCart');` 改为
  `const { steps, add, clear } = useModel('opCart');`
- 凡 `cart.length` → `steps.length`;`cart.includes(op.name)` → `steps.some((s) => s.name === op.name)`。
- 底部「去新建加工任务」按钮 `onClick` 改为 `history.push('/processing/editor')`(已 import history)。

- [ ] **Step 2: 加工列表页:新建按钮跳编辑页,删除内嵌模态**

在 `frontend/src/pages/processing/index.tsx`:
- 删除 `ModalForm` 及其内部所有表单字段(operators 多选、参数 ProFormDependency、数据集/版本选择)与相关 `useModel('opCart')`、`createOpen`/`useEffect` 自动弹窗逻辑。
- ProTable `toolBarRender` 的「新建」按钮改为:
  ```tsx
  <Button type="primary" key="new" onClick={() => history.push('/processing/editor')}>
    新建任务
  </Button>
  ```
- 清理因删除模态而 orphan 的 import(`ModalForm`/`ProFormSelect`/`ProFormDigit`/`ProFormText`/`ProFormDependency` 等若不再使用则移除)。

- [ ] **Step 3: 类型检查 + lint**

Run: `cd frontend && npm run tsc && npm run biome:lint`
Expected: 通过,无 orphan import 报错。

- [ ] **Step 4: Commit**

```bash
git add frontend/src/pages/processing/market/index.tsx frontend/src/pages/processing/index.tsx
git commit -m "feat(frontend): 入口接入编辑页,移除旧编排模态"
```

---

### Task 10: 端到端验证(浏览器)

**Files:** 无(验证)

- [ ] **Step 1: 起后端 + 前端(真实后端)**

后端已部署/或本地:`cd backend && uv run uvicorn app.main:app --port 18003`
前端:`cd frontend && PORT=8001 MOCK=none npm run dev`

- [ ] **Step 2: 走查**

用 chrome-devtools 打开 `http://127.0.0.1:8001/processing/jobs`:
- 点「新建任务」→ 跳 `/processing/editor`。
- 左栏搜索/场景筛选,点 `+` 添加 2-3 个算子 → 中栏出现有序步骤。
- 拖拽调序生效;点选某步 → 右栏出参数表单,改值;底部 YAML 实时更新。
- 点「✨ AI 生成」输入"中文去重" → 中栏被填充(operators 非空);控制台无 ERR。
- 选数据集+版本,填任务名,点「创建任务」→ 成功提示并跳回列表,列表出现该任务。

- [ ] **Step 3: 算子市场联动**

打开 `/operators`,「加入」2 个算子 → 底部「去新建加工任务」→ 跳编辑页且中栏带出这 2 个算子。

- [ ] **Step 4: 回归**

Run: `cd frontend && npm run tsc && npm run biome:lint` 与 `cd backend && uv run pytest -q && uv run ruff check .`
Expected: 全过。

- [ ] **Step 5: 标记完成**

确认所有验收点通过后,本计划完成。后续部署:重建前端镜像(见 `deploy/`)。
