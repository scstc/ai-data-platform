"""算子 ORM 模型：数据加工算子目录（从构建期 JSON 快照迁移到数据库）。"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import JSON, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Operator(Base):
    """算子：data-juicer 数据加工算子目录，支持工厂展示、动态查询、统计分析。

    原设计：构建期从 data-juicer 文档解析生成 JSON 快照（operators_catalog.json）
    新设计：入库后支持运行时统计、用户自定义算子、版本管理
    """

    __tablename__ = "operators"

    # 算子唯一标识（data-juicer 算子名，如 text_length_filter）
    name: Mapped[str] = mapped_column(String(128), primary_key=True)

    # 分类（mapper/filter/deduplicator/selector）
    category: Mapped[str] = mapped_column(String(32), nullable=False)

    # 中文标签
    zh_label: Mapped[str] = mapped_column(String(128), nullable=False)

    # 英文摘要
    summary_en: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 中文摘要
    summary_zh: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 英文详细描述
    desc_en: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 中文详细描述
    desc_zh: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 中文使用提示（何时使用）
    zh_usage_tip: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 场景分组（如：质量过滤、文本清洗、去重）
    scenario_group: Mapped[str | None] = mapped_column(String(64), nullable=True)

    # 资源类型（cpu/gpu/api_llm/hf_model/vllm）
    resource_class: Mapped[str] = mapped_column(String(32), nullable=False, default="cpu")

    # 模态支持（["text", "image"] 等）
    modality: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)

    # 支持的框架（["hf", "api"] 等）
    frameworks: Mapped[list[str] | None] = mapped_column(JSON, nullable=True)

    # 参数列表（[{name, type, default, desc}, ...]）
    params: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)

    # 用法示例（YAML 代码块）
    example: Mapped[str | None] = mapped_column(Text, nullable=True)

    # 效果展示：处理前/后样例 [{"before": str, "after": str}, ...]（详情页展示，LLM 批量生成）
    effect_demo: Mapped[list[dict] | None] = mapped_column(JSON, nullable=True)

    # 详情页链接（data-juicer 文档 URL）
    detail_page: Mapped[str | None] = mapped_column(String(256), nullable=True)

    # 是否推荐
    recommend: Mapped[bool] = mapped_column(default=False, nullable=False)

    # 基础可运行状态（快照，运行时按能力动态计算）
    # ready/needs_api/needs_compute/needs_media
    runnable: Mapped[str] = mapped_column(String(32), nullable=False, default="ready")

    # 使用次数统计（由 job 提交时更新）
    usage_count: Mapped[int] = mapped_column(default=0, nullable=False)

    # 星标计数(工厂/详情页五角星按钮,每次点击 +1,纯人气信号,不做撤销)
    star_count: Mapped[int] = mapped_column(default=0, nullable=False)

    # 是否在工厂/编排展示（False=管理员隐藏,不影响已编排任务的执行与校验）
    visible: Mapped[bool] = mapped_column(default=True, nullable=False)

    # 是否用户自定义上传（False=data-juicer 内置快照）
    is_custom: Mapped[bool] = mapped_column(default=False, nullable=False)

    # 自定义算子源码相对路径(相对 UPLOAD_DIR/custom_operators/);内置算子为空
    source_object_key: Mapped[str | None] = mapped_column(
        String(256), nullable=True
    )

    # 上传者 username；内置算子为空
    created_by: Mapped[str | None] = mapped_column(String(64), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )
