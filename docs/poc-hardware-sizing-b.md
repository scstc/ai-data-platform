# POC 硬件资源清单 — 档位 B

> ai-data-platform 单机 Docker Compose POC,覆盖文本治理 + 多模态(图像/视频)+ HF 本地模型算子。

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
