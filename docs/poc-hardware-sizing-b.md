# POC 硬件资源清单 — 档位 B

> ai-data-platform 单机 Docker Compose POC,覆盖文本治理 + 多模态(图像/视频)+ HF 本地模型算子。

## 部署拓扑

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

> 全部组件单机部署;GPU 仅 DGE 算子流水线消费(图像/视频/HF 模型类算子)。架构详见 [`architecture.md`](architecture.md)。

## 推荐配置

| 维度 | 规格 |
|---|---|
| CPU | 16 vCPU(物理 8C16T 起,要求 AVX2) |
| 内存 | 64 GB |
| 系统盘 | 200 GB SSD |
| 数据盘 | 1 TB NVMe SSD |
| GPU | 1× NVIDIA 24 GB(RTX 4090 / A10 / L4 任选) |
| 网络 | 千兆内网,出网 ≥ 100 Mbps |
| 操作系统 | Ubuntu 24.04 LTS x86_64 |
| 容器栈 | Docker 24+ / compose v2 / nvidia-container-toolkit ≥ 1.16 |
| 驱动 | NVIDIA Driver ≥ 565(CUDA 12.6+) |

## 云规格对照

| 厂商 | 实例 | 配置 |
|---|---|---|
| 阿里云 | `ecs.gn7i-c16g1.4xlarge` | 16C / 64G / A10 24G |
| 火山引擎 | `ecs.g1ie.4xlarge` + A10 | 16C / 64G / A10 24G |
| 自建 | Xeon W / EPYC + RTX 4090 | 16C / 64G / 4090 24G + 2T NVMe |
