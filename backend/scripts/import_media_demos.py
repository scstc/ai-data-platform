"""把 render_op_demos.py 生成的 _media_demos.json 写回 operators.effect_demo 列。

每个 media demo 转成 effect_demo 的一条记录:
  {"before": "原图", "after": "处理后",
   "before_url": "/operator-demos/{op}/before.png",
   "after_url":  "/operator-demos/{op}/after.png",
   "media_type": "image|video|audio"}

只对 effect_demo 为空的算子写入(保护手工 / LLM 已有内容)。
--overwrite 强制覆盖。

用法:python scripts/import_media_demos.py [--overwrite]
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

DEFAULT_SRC = Path(__file__).resolve().parent / "_media_demos.json"


def main() -> None:
    args = sys.argv[1:]
    overwrite = "--overwrite" in args
    args = [a for a in args if a != "--overwrite"]
    src = Path(args[0]) if args else DEFAULT_SRC
    if not src.exists():
        raise SystemExit(f"未找到: {src}(先跑 render_op_demos.py)")
    rows = json.loads(src.read_text(encoding="utf-8"))
    print(f"读取 {len(rows)} 条 media demo  自 {src}  模式: {'全量覆盖' if overwrite else '仅补空'}")

    engine = create_engine(str(settings.database_url).replace("+asyncpg", "+psycopg2"))
    Session = sessionmaker(bind=engine)

    updated, skipped, missing = 0, 0, []
    with Session() as session:
        for row in rows:
            name = row["name"]
            op = session.get(Operator, name)
            if op is None:
                missing.append(name)
                continue
            demo = {
                "before": f"原图({row['name']} 处理前)",
                "after": f"处理后({row['name']})",
                "before_url": row["before_url"],
                "after_url": row["after_url"],
                "media_type": row["media_type"],
            }
            if overwrite or not op.effect_demo:
                op.effect_demo = [demo]
                updated += 1
            else:
                # 已存在 → 追加(不覆盖)
                op.effect_demo = (op.effect_demo or []) + [demo]
                updated += 1
        session.commit()

    print(f"已写入: {updated} 个算子(覆盖模式: {overwrite})")
    if missing:
        print(f"DB 不存在(跳过): {missing[:5]}")


if __name__ == "__main__":
    main()
