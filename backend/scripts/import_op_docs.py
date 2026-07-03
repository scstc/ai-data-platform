"""把 parse_op_docs.py 输出的 _op_demos.json 写回 operators.example / effect_demo 列。

与 import_effect_demos.py 区别:
1. 同时更新 example 列(import_effect_demos 只写 effect_demo)
2. 默认不覆盖已有 effect_demo(只填空的行)——保护手工 / LLM 已填的高质量内容
3. --overwrite 时全量覆盖,DJ 仓升级 / 内容修正时用

用法:
    python scripts/import_op_docs.py                       # 仅补空
    python scripts/import_op_docs.py --overwrite           # 全量覆盖
    python scripts/import_op_docs.py /path/to/demos.json   # 自定义输入
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

DEFAULT_SRC = Path(__file__).resolve().parent / "_op_demos.json"


def main() -> None:
    args = sys.argv[1:]
    overwrite = "--overwrite" in args
    args = [a for a in args if a != "--overwrite"]
    src = Path(args[0]) if args else DEFAULT_SRC
    if not src.exists():
        raise SystemExit(f"未找到输入 JSON: {src}(先跑 parse_op_docs.py)")
    rows = json.loads(src.read_text(encoding="utf-8"))
    mode = "全量覆盖" if overwrite else "仅补空(保留已有)"
    print(f"读取 {len(rows)} 条自 {src}  模式: {mode}")

    engine = create_engine(
        str(settings.database_url).replace("+asyncpg", "+psycopg2")
    )
    Session = sessionmaker(bind=engine)

    updated_example = 0
    updated_demos = 0
    skipped_demos = 0
    missing = []
    with Session() as session:
        for row in rows:
            name = row["name"]
            op = session.get(Operator, name)
            if op is None:
                missing.append(name)
                continue
            # example: overwrite=True 全量,否则只补空
            if row.get("example") and (overwrite or not op.example):
                op.example = row["example"]
                updated_example += 1
            # effect_demo: 同上
            if row.get("demos"):
                if overwrite or not op.effect_demo:
                    op.effect_demo = row["demos"]
                    updated_demos += 1
                else:
                    skipped_demos += 1
        session.commit()

    print(
        f"已更新 example: {updated_example} 个\n"
        f"已更新 effect_demo: {updated_demos} 个(已有跳过: {skipped_demos})\n"
        f"DB 不存在(跳过): {len(missing)}"
    )
    if missing:
        print(f"  示例: {missing[:5]}")


if __name__ == "__main__":
    main()
