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
| `数据治理.md` | 数据治理 PRD:多源统一数据湖+标准化算子加工流水线的四层架构(ODS原始层/抽取解析层/DJ加工层/成品数据集层),湖集分离设计,双版本机制(source_v/dataset_v),血缘字段规范,全品类 JSONL 格式定义 |
| `data-lake-implementation.md` | 数据湖 ODS 层实现说明:数据模型(DataLake/DataLakeSnapshot)、迁移(0036)、服务层(入湖/抽取/血缘注入)、API 端点(7 个)、使用示例;第一阶段完成,支持结构化 Parquet + 文档/多媒体原格式入湖(2026-07-01) |
| `data-lake-migration-guide.md` | 数据湖改造指南:从现有 `run_pg_ingest` 直接落集流程迁移到"数据源→数据湖→数据集"湖集分离链路,含 Connector 改造步骤、灰度策略(via_lake 开关)、血缘追踪验证、性能影响评估、故障排查(2026-07-01) |
| `data-lake-summary.md` | 数据湖实现总结:第一阶段成果清单(11 个文件)、核心特性(版本号规范/血缘追踪/湖集分离)、验证状态(12/12 单测通过)、性能指标、下一步(第二层抽取解析/第三层 DJ 集成)(2026-07-01) |
| `数据湖文件版本模型整改.md` | 数据湖两层改三层(湖→文件→版本)设计:文件身份键/按文件自增版本号/存储路径自描述/湖内合并(union/join+血缘)/存量回填与 adp_gov 迁移策略;取代 source_v 天级批次号语义(2026-07-04) |
| `治理工场与任务广场设计.md` | 数据治理菜单收口设计:清洗/蒸馏/合成/增强四模块复制粘贴现状 → 统一「治理工场」(场景 Tab + 流水线模板 + 一键执行);pipeline 数据模型与 6 端点 API 契约、执行链路(pipeline→job→版本/血缘)、菜单前后对照、分阶段清单(本期完成项/遗留项)(2026-07-05) |

## 项目级文档(根目录)

| 文件 | 说明 |
|------|------|
| [根 `README.md`](../README.md) → 「数据库操作(统一使用 dbx)」 | 约定本项目所有 DB 操作走 dbx MCP;含 `PostgreSQL_adp` 连接信息(10.60.1.60:55433/adp)、常用工具对照、安全提示(2026-06-24) |

> 新增文档时,请在上表登记,保持索引最新。
