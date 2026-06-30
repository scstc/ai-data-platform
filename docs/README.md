# 文档

本目录用于记录 `ai-data-platform` 项目的相关文档。

## 目录说明

| 文件 / 目录 | 说明 |
|------------|------|
| `README.md` | 文档索引(本文件) |
| `requirements/数据工程&数据集.xlsx` | 原始需求文件(2026-05-26) |
| `requirements/数据工程&数据集.md` | 原始需求的 markdown 表格转写(数据工程 12 项 + 数据集仓库 8 项) |
| `data-engineering/` | LLM 数据工程知识库(零基础向,9 篇,基于深度调研+事实核验,2026-06-04) |
| `specs/2026-06-04-frontend-design.md` | 前端项目设计文档(Ant Design Pro v6,平台壳+数据接入,已批准) |
| `specs/2026-06-04-backend-design.md` | 后端项目设计文档(FastAPI+PostgreSQL,实现前端契约,已批准) |
| `plan/` | 实现规划:从现状到满足 20 项需求的任务清单(todolist)与里程碑建议 |
| `research/bcc-数据接入调研.md` | BCC PaaS「数据接入」功能实探报告:8 种数据源、字段级 UI 细节、兄弟页面概览及对本项目的参考意义(2026-06-11) |
| `research/data-juicer-文件格式支持调研.md` | data-juicer 文件格式支持调研:输入格式全表、Word/Excel/PDF 支持情况(docx/pdf✅、excel❌)、产物导出格式(默认 jsonl)、对照 LAS 需求、归一化算子落地建议(2026-06-24) |
| `research/LAS竞品复刻可行性调研.md` | 以火山引擎 LAS《数据集管理》为竞品,评估用当前框架(FastAPI+PG+data-juicer+AntD Pro)复刻的可行性:LAS 四层能力拆解(Catalog/双引擎SQL/在线编辑/回流)、Lance 技术依据、本地现状 gap 矩阵、DuckDB+LanceDB 复刻路径、风险与诚实声明(混合调研:本地源码+105-agent deep-research,23/25 claim 验证通过)(2026-06-24) |
| `dynamic-menu.md` | 动态菜单(RBAC)架构:侧边栏由 `menus` 表经 `getRouters`+`menuDataRender` 按角色驱动、路由仍静态;两个事实源、哪些进表、按角色控可见、新增页面三步流程、图标映射(2026-06-25) |
| `data-governance-flow.md` | 多格式数据治理流程:异构文件(文本+多媒体)→统一数据集→训练平台的端到端方案;接入层(landing)、治理层(data-juicer)、交付层(parquet)三层架构;纯文本/CSV/图片/视频四场景实施方案;后端扩展(媒体批量接入+manifest物化)、前端改造(列映射+算子筛选)、典型用户故事、技术决策、实施路线图(2026-06-30) |
| `data-governance-flow-summary.md` | 上文的快速参考版:一分钟速览、文件格式处理速查表、核心约束与解法,完整内容见 `data-governance-flow.md`(2026-06-30) |
| `architecture.md` | 系统架构说明:运行时架构总览(单机 Docker Compose 拓扑、组件职责、技术栈、三层数据流);偏部署后实际形态,与 `plan/03-架构设计.md`(领域模型)、`data-governance-flow.md`(数据流)互补(2026-06-30) |
| `poc-hardware-sizing-b.md` | POC 硬件资源清单(档位 B):部署拓扑 + 16C/64G/1TB NVMe/1× 24G GPU 推荐配置 + 云规格对照(2026-06-30) |
| `training-dataset-format-spec.md` | 训练数据集格式规范:数据治理平台→训练/推理平台的格式契约;8 种训练方式各自的数据集 schema(预训练`text`/SFT`messages`/DPO`prompt,chosen,rejected`/评估`prompt,response`≥300条等)、**蒸馏区分**(模型蒸馏=训练方式 vs 数据蒸馏=Selector选子集/Mapper合成)、**多模态变体**(图文/音频/视频走 `images/audios/videos` 路径+`<image>`占位符,媒体存OBS不嵌入)、数据集元数据(train_type 过滤)、数据集构造层(原始列→训练字段,当前缺口)、交付三件套、优先级建议(P0:SFT+评估);承接 `data-governance-flow.md` 下游(2026-06-30) |
| `data-governance-remediation-plan.md` | **数据治理系统整改方案**:依据上述三份目标文档对当前系统(后端/前端 + data-juicer fork)做逐文件核实的差距分析(6+1 路并行代码调研 + 核心模块亲读交叉验证);含已实现能力基线、**文档勘误**(DJ 算子真实性核实:`columns_selector`/`obs_*_file_mapper`/`python_lambda_filter` 等不存在,`_detect_pdf_type`/OCR/去格式为未实现示意代码)、19 项差距清单(P0:train_type 元数据/构造层/SFT messages/评估≥300+裁判员;P1:Ray接线/交付三件套/proprietary签名bug)、分 5 阶段整改路线 + 排期/风险/验证(2026-06-30) |

## 项目级文档(根目录)

| 文件 | 说明 |
|------|------|
| [根 `README.md`](../README.md) → 「数据库操作(统一使用 dbx)」 | 约定本项目所有 DB 操作走 dbx MCP;含 `PostgreSQL_adp` 连接信息(10.60.1.60:55433/adp)、常用工具对照、安全提示(2026-06-24) |

> 新增文档时,请在上表登记,保持索引最新。
