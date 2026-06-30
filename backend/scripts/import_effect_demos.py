"""把 LLM 批量生成的「处理前/后」效果样例写入 operators.effect_demo 列。

输入 JSON 形如 [{"name": "<算子名>", "demos": [{"before": "...", "after": "..."}, ...]}, ...]
运行：python backend/scripts/import_effect_demos.py <demos.json>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.models.operator import Operator


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("用法: python scripts/import_effect_demos.py <demos.json>")
    src = Path(sys.argv[1])
    rows = json.loads(src.read_text(encoding="utf-8"))
    print(f"读取 {len(rows)} 条样例自 {src}")

    engine = create_engine(
        str(settings.database_url).replace("+asyncpg", "+psycopg2")
    )
    session = sessionmaker(bind=engine)()

    updated, missing = 0, []
    try:
        for row in rows:
            name = row["name"]
            demos = row.get("demos") or []
            if not demos:
                continue
            op = session.get(Operator, name)
            if op is None:
                missing.append(name)
                continue
            op.effect_demo = demos
            updated += 1
        session.commit()
    finally:
        session.close()

    print(f"已更新 {updated} 个算子的 effect_demo")
    if missing:
        print(f"⚠️ 库中不存在的算子(跳过): {missing}")


if __name__ == "__main__":
    main()
