"""version modalities: dataset_versions.modalities 多模态模态快照

Revision ID: 0026_version_modalities
Revises: 0025_schedule_incremental
Create Date: 2026-06-27

多模态子类型(图片/视频/音频/跨模态)列表筛选 + 子标签(方案 A · 语义 B):
- dataset_versions 加 1 列:
  * modalities JSONB nullable —— 该版本出现过的模态集合
    (images/audios/videos/text 子集;仅 semantic_type=multimodal 版本写入)。
    落地时由 ``SemanticReport.modalities``(land_records / upload-batch,真图文配对)
    或 ``data_type`` 映射(land_upload_raw / upload-media,单媒体原样/manifest,无真实
    文本)填充;存量版本为空 → 列表兜底显示"多模态"主标签(无子标签),不回填。

仅 add column(JSONB nullable),无数据迁移、无回填风险。downgrade 反向 drop。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0026_version_modalities"
down_revision: str | None = "0025_schedule_incremental"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "dataset_versions",
        sa.Column(
            "modalities",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("dataset_versions", "modalities")
