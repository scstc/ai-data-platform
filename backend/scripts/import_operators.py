"""从 operators_catalog.json 导入算子到数据库。

运行：python backend/scripts/import_operators.py

清空重插仅针对内置目录(is_custom=False);自定义算子(页面上传 / 迁移 0070
种子化)不受影响,可放心重跑。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# 添加项目根目录到 Python 路径
BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND))

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.models.operator import Operator

CATALOG_PATH = BACKEND / "app" / "data" / "operators_catalog.json"


def main():
    """导入算子目录到数据库。"""
    print(f"读取目录: {CATALOG_PATH}")
    catalog = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
    operators_data = catalog["operators"]
    print(f"找到 {len(operators_data)} 个算子")

    # 连接数据库
    engine = create_engine(str(settings.database_url).replace('+asyncpg', '+psycopg2'))
    Session = sessionmaker(bind=engine)
    session = Session()

    try:
        # 清空现有内置目录(保留自定义算子——它们不在 catalog.json 里,
        # 全量 delete 会把用户上传/迁移种子化的算子一并抹掉)
        deleted = (
            session.query(Operator)
            .filter(Operator.is_custom.is_(False))
            .delete()
        )
        print(f"清空现有内置算子: {deleted} 行(自定义算子保留)")

        # 批量插入
        count = 0
        for op_data in operators_data:
            op = Operator(
                name=op_data["name"],
                category=op_data["category"],
                zh_label=op_data["zh_label"],
                summary_en=op_data.get("summary_en"),
                summary_zh=op_data.get("summary_zh"),
                desc_en=op_data.get("desc_en"),
                desc_zh=op_data.get("desc_zh"),
                zh_usage_tip=op_data.get("zh_usage_tip"),
                scenario_group=op_data.get("scenario_group"),
                resource_class=op_data.get("resource_class", "cpu"),
                modality=op_data.get("modality"),
                frameworks=op_data.get("frameworks"),
                params=op_data.get("params"),
                example=op_data.get("example"),
                detail_page=op_data.get("detail_page"),
                recommend=op_data.get("recommend", False),
                runnable=op_data.get("runnable", "ready"),
                usage_count=0,
            )
            session.add(op)
            count += 1

            if count % 50 == 0:
                print(f"已处理 {count} 个算子...")

        session.commit()
        print(f"✓ 成功导入 {count} 个算子")

    except Exception as e:
        session.rollback()
        print(f"✗ 导入失败: {e}")
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
