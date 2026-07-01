"""应用配置：基于 pydantic-settings，从环境变量 / .env 读取。"""

from __future__ import annotations

from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """全局配置项。"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 数据库连接串（async 驱动）
    database_url: str = "postgresql+asyncpg://adp:adp_dev_pw@127.0.0.1:55433/adp"

    # 令牌签名密钥（env AUTH_SECRET）；dev 默认值仅供开发，生产必须覆盖
    auth_secret: str = "adp-dev-insecure-secret-change-me"

    # OpenAI 兼容 LLM 配置（均可空；配置齐全时上层可启用 LLM 模式）
    openai_base_url: str | None = None
    openai_api_key: str | None = None
    openai_model: str = "gpt-4o-mini"

    # 平台 MinIO 对象存储(文件管理 #19);env STORAGE_MINIO_*。未配置→文件管理 503。
    storage_minio_endpoint: str | None = None
    storage_minio_access_key: str | None = None
    storage_minio_secret_key: str | None = None
    # 媒体批量接入(manifest 数据集)上传成员/清单的目标桶(平台自有,可删可回收)
    storage_minio_upload_bucket: str = "uploads"
    # 数据湖专用桶(ODS 原始数据归档,见 docs/数据治理.md §2.1)。
    # 与 upload 桶物理隔离:数据湖=原始不可变,upload 桶=加工产物可回收;
    # 生产环境可给数据湖桶独立配额、生命周期、备份策略。
    storage_minio_lake_bucket: str = "adp-data-lake"

    # 上传文件落盘目录
    upload_dir: str = (
        "/Users/enjoy/ai-project/ai-data-platform/backend/var/uploads"
    )

    # 数据集受管存储目录(落地产物 jsonl,按 <dataset_id>/v<n>/data.jsonl 组织)
    datasets_dir: str = (
        "/Users/enjoy/ai-project/ai-data-platform/backend/var/datasets"
    )

    # 外部 S3 托管(#18)物化缓存:避免每次加工/预览都从三方 S3 重复拉同一对象。
    # 按 (endpoint,bucket,key,etag) 缓存到本地磁盘,LRU(按访问时间)+ 总量上限淘汰;
    # 源对象 etag 变即自动失效重拉。缓存只是可丢弃的性能副本——绝不回写源、可随时清空,
    # hosted "source of truth 在三方" 语义不变。max_bytes<=0 关闭缓存(回退按需下载)。
    hosted_cache_dir: str = (
        "/Users/enjoy/ai-project/ai-data-platform/backend/var/hosted-cache"
    )
    hosted_cache_max_bytes: int = 10 * 1024 * 1024 * 1024  # 10 GiB

    # data-juicer 加工引擎:dj-process 可执行文件(子进程调用)
    dj_process_bin: str = (
        "/Users/enjoy/ai-project/ai-data-platform/data-juicer/.venv/bin/dj-process"
    )
    # data-juicer 质量评估:dj-analyze 可执行文件(留空则取 dj_process_bin 同目录)
    dj_analyze_bin: str = ""
    # 单 job 内并行度(传给 dj-process 的 np)
    engine_np: int = 2
    # 多 job 并发上限(信号量,避免单机被打爆)
    engine_concurrency: int = 3
    # 单个加工任务超时(秒);超时杀子进程并标记失败。0/负 = 不限时
    engine_job_timeout: int = 3600

    # 评估数据集硬指标(治理整改 G4,规范 §3.7):每集 ≥N 条,落地前 Fail-loud 校验。
    # 可配但默认 300(统计显著性下限);测试可 monkeypatch 调小验证逻辑。
    eval_min_records: int = 300

    # 部署镜像 tag(治理整改 G18 可复现,env IMAGE_TAG):容器化部署由 entrypoint /
    # docker run -e IMAGE_TAG=xxx 注入;本地开发为 None。随 job 记录,供一键复现。
    image_tag: str | None = None

    # 扫描型 PDF OCR(治理整改 G10):引擎本体为独立 OCR service(Unlimited-OCR/
    # paddleocr,~1GB 模型 + 理想 GPU),平台仅作 HTTP 客户端调用。默认关闭 → 零行为变化
    # (扫描件仍 Fail-loud 抛错);开启需配 ocr_endpoint。
    ocr_enabled: bool = False
    ocr_endpoint: str | None = None  # 独立 OCR service,如 http://10.60.1.x:xxxx/ocr/pdf
    ocr_timeout: int = 300  # OCR 单次超时(秒);CPU 模式 10 页约 2-5 分钟
    ocr_lang: str = "ch"  # OCR 语言(中文)

    # 运行时能力(算子有效可运行状态的事实来源,见 services/capabilities.py)
    # vLLM 推理服务地址(配置后探测其 /v1/models 决定 vllm 类算子是否可用)
    vllm_base_url: str | None = None
    # 是否启用 Ray 分布式 executor(需 DJ venv 装 ray + 引擎支持,默认关)
    ray_enabled: bool = False
    # 强制覆盖探测结果(None=自动探测;调试 / CI 用)
    cuda_force: bool | None = None
    vllm_force: bool | None = None
    ray_force: bool | None = None

    # 允许的跨域来源（前端 dev server）
    cors_origins: list[str] = [
        "http://localhost:8001",
        "http://127.0.0.1:8001",
    ]

    # 调度器开关(切片 C):False 时启动不构造 AsyncIOScheduler,适合测试 / 无 PG
    # 环境。生产默认开;启动失败(如 PG 不可达)也仅告警不阻断主流程(采集不依赖
    # 调度器在线)。
    scheduler_enabled: bool = True

    @model_validator(mode="after")
    def _derive_dj_analyze_bin(self) -> Settings:
        """dj_analyze_bin 未显式配置时,默认与 dj_process_bin 同目录。"""
        if not self.dj_analyze_bin:
            self.dj_analyze_bin = str(
                Path(self.dj_process_bin).parent / "dj-analyze"
            )
        return self


settings = Settings()
