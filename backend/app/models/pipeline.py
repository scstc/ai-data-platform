"""流水线(pipeline)ORM 模型:命名的算子编排,可保存复用。

治理工场(治理整改):清洗/蒸馏/合成/增强四场景共用一套"编排 → 执行"心智模型。
一条 pipeline 是可复用的算子编排(spec 存 operators/goal/textKeys);执行时按
scenario 分发到各自的 _start_*,落一条 jobs 行并回指 pipeline_id(见 pipelines.py)。
预置模板(如「标准文本清洗」)不入库,见 app/services/pipeline_presets.py。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import String, Text, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class Pipeline(Base):
    """流水线:scenario 与 Job.type 对齐(clean/distillation/synthesis/augmentation)。"""

    __tablename__ = "pipelines"

    # 主键形如 "pl-" + 6 位 hex
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    # clean | distillation | synthesis | augmentation(与 Job.type 对齐)
    scenario: Mapped[str] = mapped_column(String, nullable=False, index=True)
    # {operators: [{name, params}], goal?, textKeys?}(蛇形键落库,与 Job.spec 一致)
    spec: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_by: Mapped[str] = mapped_column(String, nullable=False, default="admin")
    created_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        server_default=func.now(), onupdate=func.now(), nullable=False
    )
