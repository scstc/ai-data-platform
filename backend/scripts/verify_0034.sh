#!/usr/bin/env bash
# backend/scripts/verify_0034.sh —— 在一次性 scratch 库上跑真实迁移
set -euo pipefail
SCRATCH_URL="${SCRATCH_DATABASE_URL:?需指向一次性 scratch 库,勿用 dev adp}"
export DATABASE_URL="$SCRATCH_URL"
.venv/Scripts/alembic.exe upgrade head
# 断言:表存在 + ingest_tasks.dataset_id NOT NULL + 无版本缺成员
.venv/Scripts/python.exe - <<'PY'
import os
from sqlalchemy import inspect, text, create_engine
e = create_engine(os.environ["DATABASE_URL"].replace("+asyncpg", ""))
with e.connect() as c:
    insp = inspect(c)
    assert "dataset_version_tables" in insp.get_table_names()
    it = {col["name"]: col for col in insp.get_columns("ingest_tasks")}
    assert it["dataset_id"]["nullable"] is False, "dataset_id 应为 NOT NULL"
    assert "dataset_id" in {col["name"] for col in insp.get_columns("upload_records")}
    miss = c.execute(text(
        "SELECT count(*) FROM dataset_versions dv WHERE NOT EXISTS "
        "(SELECT 1 FROM dataset_version_tables t WHERE t.dataset_version_id=dv.id)"
    )).scalar()
    assert miss == 0, f"{miss} 个版本未回填成员"
print("0034 upgrade OK")
PY
# 回退验证
.venv/Scripts/alembic.exe downgrade -1
.venv/Scripts/alembic.exe upgrade head
echo "0034 downgrade/upgrade roundtrip OK"
