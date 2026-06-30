# 系统架构说明

> ai-data-platform 运行时架构总览。领域模型见 [`plan/03-架构设计.md`](plan/03-架构设计.md),数据流见 [`data-governance-flow.md`](data-governance-flow.md)。日期:2026-06-30。

## 系统定位

面向 LLM 的数据治理平台:异构数据源(文件 / 数据库 / 对象存储 / API 推送)接入 → **数据治理引擎(Data Governance Engine,DGE)**按算子流水线治理(清洗 / 去重 / 质检 / 合成)→ 产出版本化、训练友好的数据集。前端可视化编排,后端驱动引擎执行。

## 部署拓扑(单机 Docker Compose)

```
浏览器 ──:80──▶ 前端 (nginx, SPA + /api 反代)
                   │
                   ▼
        ┌─ 后端 (FastAPI) ───────────────────┐
        │    API / Job 编排 / 连接器 / 数据落地  │
        │       │ 子进程驱动                    │
        │       ▼                             │
        │    数据治理引擎 DGE (算子流水线)        │
        └───────┬─────────────────────────────┘
                ├─▶ PostgreSQL   元数据(数据源/数据集/版本/Job)
                ├─▶ MinIO        对象存储(媒体归档 + 数据集托管)
                └─▶ 共享卷 /data  上传文件 + 数据集产物

        文件预览 (kkFileView)  非结构化文件在线预览
```

## 组件职责

| 组件 | 技术 | 职责 |
|---|---|---|
| 前端 | nginx + Ant Design Pro v6 | SPA 托管 + 反代后端(同源) |
| 后端 | FastAPI (py3.12) | API、Job 编排、连接器、数据落地 |
| 数据治理引擎(DGE) | 与后端同镜像,进程隔离 | 子进程执行算子流水线(100+ 算子) |
| PostgreSQL | PG 16 | 元数据库 |
| MinIO | S3 兼容 | 对象存储 |
| kkFileView | — | pdf/office/视频等在线预览 |

> 后端与引擎**同镜像、共享文件系统**:后端以子进程调引擎并读其产物,故二者同机同盘。

## 技术栈

前端 Ant Design Pro v6 / React 19 / TypeScript · 后端 FastAPI / SQLAlchemy(async) / Alembic · 数据库 PostgreSQL 16 · 对象存储 MinIO(S3 协议)· 部署 Docker Compose。

## 核心数据流

三层解耦(详见 `data-governance-flow.md`):

1. **接入层** — 多源统一落地为不可变数据集版本(结构化 parquet,文本/媒体 jsonl)。
2. **治理层** — 前端拖拽算子生成配置 → 后端调引擎执行 → 产出新版本 + 质量统计。
3. **交付层** — 导出 jsonl 或 parquet(结构化数据用 parquet 保留列类型、列式可分片;文本/半结构化用 jsonl),媒体走对象存储路径引用。
