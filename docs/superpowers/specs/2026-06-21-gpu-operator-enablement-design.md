# 设计:GPU 算子在本机可用(运行时能力探测 + 动态可运行状态)

- 日期:2026-06-21
- 目标:让需要 GPU/模型算力的 data-juicer 算子在装有显卡的本机**真正可用**(不仅显示可用)。
- 背景:算子市场里 66 个 `needs_compute` 算子永远灰色不可点,因为 `runnable` 是构建期写死的"无 GPU 环境"快照,且 DJ venv 装的是 CPU 版 torch。

## 现状(改动前)

| 层 | 状态 |
|---|---|
| 宿主机 GPU | RTX 4060 Laptop 8GB,驱动 566.26(CUDA 12.7) |
| DJ venv torch | `2.8.0+cpu`,`cuda.is_available()=False` —— **根本用不了显卡** |
| 后端 | py3.12,无 torch,纯子进程编排 `dj-process` |
| 可运行判定 | `operators_catalog.json` 里 `runnable` 构建期烤死;`runnable_reason()` 只读快照,无运行时探测 |

## 方案 A:运行时能力探测 + 有效可运行状态(已采纳)

`runnable` 从"构建期快照"改为 **静态需求(`resource_class`)× 运行时能力(capabilities)** 实时计算。

### 能力探测器 `app/services/capabilities.py`(新增)

探测当前环境真实能力 `Capabilities{cuda, vllm, ray, llm}`,失败一律视作 False(绝不抛):

| 能力 | 探测方式 | 缓存 |
|---|---|---|
| `cuda` | 子进程问 **DJ venv** `python -c "import torch;...cuda.is_available()"`(执行器在 DJ,不在后端) | 进程生命周期 |
| `vllm` | HTTP 探 `settings.vllm_base_url` 的 `/v1/models` | TTL 30s |
| `ray` | `settings.ray_enabled` 且 DJ venv `import ray` 成功 | 进程生命周期 |
| `llm` | `bool(settings.openai_api_key)` | 实时 |

DJ python 解释器由 `settings.dj_process_bin` 同目录推导(`Scripts/python.exe` / `bin/python`)。
`.env` 新增 `VLLM_BASE_URL` / `RAY_ENABLED` / `CUDA_FORCE` / `VLLM_FORCE` / `RAY_FORCE`(强制覆盖,调试/CI 用)。

### 有效状态 `operator_catalog.effective_runnable(op, caps)`

优先级保持与构建期 `runnable()` 一致,每条算力门改为按 caps 实时判定:

```
媒体模态(image/video/audio/multimodal)→ needs_media  # 平台受管数据集为文本 jsonl,永不适用
api_llm   → ready if caps.llm  else needs_api
ray_*     → ready if caps.ray  else needs_compute
gpu|hf_model → ready if caps.cuda else needs_compute
vllm      → ready if caps.vllm else needs_compute
其余 cpu  → ready
```

**诚实门**:vLLM/Ray 算子只有对应服务/executor 真就绪才转 ready,没配仍灰——避免"刷绿了点下去报错"。

所有消费方统一改走 `effective_runnable`:`to_api`(出参 runnable)、`meta_api`(byRunnable 计数)、`query_catalog`(runnable 过滤)、`legacy_operators`、`ready_operator_context`、`sanitize_pipeline`、`runnable_reason`(执行守门)。

### API

新增 `GET /api/v1/operators/capabilities` → `{cuda,vllm,ray,llm}`。

### 前端(`pages/processing/market/index.tsx`)

前端所有逻辑已 key 在 `op.runnable === 'ready'`(徽章/计数/"加入"禁用/"只看可运行"/页头),后端返回动态 runnable 后**自动跟随翻绿**。仅新增「环境能力」指示(GPU/LLM/vLLM/Ray ✓/✗ 标签),让灰/绿成因可见。

## 验证口径(基线 vs 有 GPU)

- 无 GPU(`CUDA_FORCE=false`):`ready=87`(与现状一致),`needs_compute=24`。
- 有 GPU(`CUDA_FORCE=true`):`ready=106`(+19 文本类 GPU/HF 算子),`needs_compute=5`(剩 vLLM/Ray)。
- 媒体类正确留在 `needs_media`(文本数据集跑不了媒体算子,与 GPU 无关)。

## 前提(ops,非代码)

DJ venv 把 `torch==2.8.0+cpu` 换成 CUDA 版:`uv pip install torch==2.8.0 torchaudio==2.8.0 --index-url https://download.pytorch.org/whl/cu126`(cu126 ≤ 驱动 12.7;cu128 需更新驱动)。换后 `cuda.is_available()` 应为 True。

## 范围与后续

- 本次打通:纯 GPU(文本类)+ HF 模型类算子的真实执行 + 全平台动态可运行状态。
- vLLM 类:需本地起 vLLM 服务(8GB 仅能跑 1~3B 量化模型),探测就绪即自动放开。
- Ray 类:需引擎支持 Ray executor(`build_config` 切 `executor_type: ray`),与 GPU 解耦,后续单独立项。

## 端到端验证目标

`llm_perplexity_filter`(默认 `Qwen/Qwen2.5-0.5B`,约 1GB,适配 8GB)经 `dj-process` 实跑:在 GPU 上加载模型算困惑度并产出 jsonl,证明"dj-process → GPU 算子 → 产出"链路通。
