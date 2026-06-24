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

## 项目级文档(根目录)

| 文件 | 说明 |
|------|------|
| [根 `README.md`](../README.md) → 「数据库操作(统一使用 dbx)」 | 约定本项目所有 DB 操作走 dbx MCP;含 `PostgreSQL_adp` 连接信息(10.60.1.60:55433/adp)、常用工具对照、安全提示(2026-06-24) |

> 新增文档时,请在上表登记,保持索引最新。
