"""回填无成员版本的成员行(0034 之后新增的存量)。

0034 已给当时全部版本回填 table_name='data' 成员;此后 construct / api 推送 /
对象存储原样接入 / 各 LLM 场景 fallback 等路径又产生了无成员版本。engine 加工
已统一为成员级 staging 流程(无成员版本运行时合成伪成员兜底),本迁移把存量
补齐,使"每个非 manifest 版本至少有一个成员"再次成立。

manifest 版本除外:它天然不落 dataset_version_tables,由 datasets._attach_tables
合成 MANIFEST_MEMBER_NAME 伪成员(回填反而会顶掉该合成逻辑、破坏成员名校验)。

面向 adp_gov 克隆库。回退:删除本迁移插入的行(以 id 前缀 'dvt-bf56' 标记)。
"""
from typing import Sequence, Union

from alembic import op

revision: str = "0056_backfill_memberless_dvt"
down_revision: Union[str, None] = "0055_seed_menu_buttons"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        INSERT INTO dataset_version_tables
            (id, dataset_version_id, table_name, storage_uri, format,
             rows, size, schema_snapshot, schema_variant, created_at)
        SELECT
            'dvt-bf56' || substr(md5(random()::text || dv.id), 1, 6),
            dv.id, 'data', dv.storage_uri, dv.format,
            dv.rows, dv.size, dv.schema_snapshot, dv.schema_variant, now()
        FROM dataset_versions dv
        WHERE dv.format != 'manifest'
          AND NOT EXISTS (
            SELECT 1 FROM dataset_version_tables t
            WHERE t.dataset_version_id = dv.id
        )
        """
    )


def downgrade() -> None:
    op.execute(
        "DELETE FROM dataset_version_tables WHERE id LIKE 'dvt-bf56%'"
    )
