"""datasets: 来源 source_kind + 原始格式 source_format(类型三轴拆分)

Revision ID: 0016_source_kind_format
Revises: 0015_ingest_task_dataset
Create Date: 2026-06-22

把混在 data_type 里的「来源/接入方式」与「原始格式」拆成两个独立列(数据类型轴
复用已有 semantic_type)。设计见 docs/superpowers/specs/ 下同日期 redesign 设计稿。

新增两列均 nullable、无 CHECK/FK,旧查询零影响。data_type 列保留(向后兼容)。

存量 best-effort 回填(判不准留空,不臆造):
- source_kind:data_type='sql'→database;'csv-tsv'→local_upload;
  data_type 落在已知文件格式集 → local_upload(历史上传以文件格式记 data_type)。
- source_format:data_type='csv-tsv'→csv;data_type 是已知文件格式 → 同值。
- 托管(origin=hosted)本期不处理,留空。

downgrade:删两列。
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0016_source_kind_format"
down_revision: str | None = "0015_ingest_task_dataset"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# 历史 data_type 里实际是「文件格式」的取值(对齐 landing.py 的 LANDABLE/BINARY 集)
_FILE_FORMATS = (
    "csv", "tsv", "txt", "log", "jsonl", "json", "xlsx", "xls",
    "pdf", "doc", "docx", "ppt", "pptx", "html",
    "png", "jpg", "jpeg", "gif", "bmp", "webp",
    "mp3", "wav", "flac", "m4a", "aac", "ogg",
    "mp4", "avi", "mov", "mkv", "webm",
    "image", "audio", "video",
)


def upgrade() -> None:
    op.add_column(
        "datasets", sa.Column("source_kind", sa.String(), nullable=True)
    )
    op.add_column(
        "datasets", sa.Column("source_format", sa.String(), nullable=True)
    )

    # 回填 source_kind
    op.execute(
        "UPDATE datasets SET source_kind='database' WHERE data_type='sql'"
    )
    op.execute(
        "UPDATE datasets SET source_kind='local_upload' "
        "WHERE data_type='csv-tsv'"
    )
    fmt_list = ", ".join(f"'{f}'" for f in _FILE_FORMATS)
    op.execute(
        "UPDATE datasets SET source_kind='local_upload' "
        f"WHERE source_kind IS NULL AND data_type IN ({fmt_list})"
    )

    # 回填 source_format
    op.execute(
        "UPDATE datasets SET source_format='csv' WHERE data_type='csv-tsv'"
    )
    op.execute(
        "UPDATE datasets SET source_format=data_type "
        f"WHERE source_format IS NULL AND data_type IN ({fmt_list})"
    )


def downgrade() -> None:
    op.drop_column("datasets", "source_format")
    op.drop_column("datasets", "source_kind")
