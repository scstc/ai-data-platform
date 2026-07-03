"""手写 8 个 formatter 的文本示例,写回 DB operators.effect_demo 列。

formatter 在 DJ 仓 .md 里没有"📊 Effect demonstration"节(概念上 formatter 是
加载器,没有"前后效果"语义)。手写输入文件片段 + 规范化后的内部行结构作为示例,
帮用户理解每个 formatter 加载什么、加载后成什么样。

跑:cd backend && ./.venv/Scripts/python.exe scripts/import_formatter_demos.py
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

# 8 个 formatter 的 effect_demo 手写示例
# 每条:{"before": 输入文件片段, "after": 加载后单行 dict 结构}
DEMOS: dict[str, list[dict]] = {
    "csv_formatter": [
        {
            "before": "name,age,city\nAlice,30,Beijing\nBob,25,Shanghai\nCharlie,35,Shenzhen",
            "after": "{'name': 'Alice', 'age': 30, 'city': 'Beijing'}\n{'name': 'Bob', 'age': 25, 'city': 'Shanghai'}\n{'name': 'Charlie', 'age': 35, 'city': 'Shenzhen'}\n[共 3 行,列名: name/age/city]",
        },
        {
            "before": "text,label\n\"Hello, world\",greeting\n\"Good morning\",greeting",
            "after": "{'text': 'Hello, world', 'label': 'greeting'}\n{'text': 'Good morning', 'label': 'greeting'}",
        },
    ],
    "json_formatter": [
        {
            "before": '{"prompt": "你好", "response": "你好,有什么可以帮你?"}\n{"prompt": "天气", "response": "今天晴,25度"}',
            "after": "{'prompt': '你好', 'response': '你好,有什么可以帮你?'}\n{'prompt': '天气', 'response': '今天晴,25度'}",
        },
    ],
    "jsonl_formatter": [  # 同 json_formatter 走 JSONL 格式
        {
            "before": '{"q": "中国首都是?", "a": "北京"}\n{"q": "长城有多长?", "a": "约 21196.18 km"}',
            "after": "{'q': '中国首都是?', 'a': '北京'}\n{'q': '长城有多长?', 'a': '约 21196.18 km'}",
        },
    ],
    "parquet_formatter": [
        {
            "before": "data.parquet(列存储,二进制)\n[内部 schema]\n  text: string\n  label: int64\n  score: float64\n行数: 10000",
            "after": "{'text': '...', 'label': 1, 'score': 0.95}\n{'text': '...', 'label': 0, 'score': 0.32}\n[10000 行,内存占用约 2-5 MB]",
        },
    ],
    "text_formatter": [
        {
            "before": "data.txt(每行一条样本,无表头)\n这是第一段文本。\n这是第二段文本,可有多行,直到文件末尾。\n\n第三段。",
            "after": "{'text': '这是第一段文本。'}\n{'text': '这是第二段文本,可有多行,直到文件末尾。'}\n{'text': '第三段。'}\n[3 行,key: text]",
        },
    ],
    "tsv_formatter": [
        {
            "before": "question\\tanswer\\n你好\\t你好啊\n天气怎么样\\t晴 25度",
            "after": "{'question': '你好', 'answer': '你好啊'}\n{'question': '天气怎么样', 'answer': '晴 25度'}",
        },
    ],
    "empty_formatter": [
        {
            "before": "(无输入文件)\n传 --empty_sample_num=5 时生成 5 条空样本",
            "after": "{'text': ''}\n{'text': ''}\n{'text': ''}\n{'text': ''}\n{'text': ''}\n[5 条空样本,key: text]",
        },
    ],
    "local_formatter": [
        {
            "before": "/path/to/dataset/  (混合后缀目录)\n  train.jsonl\n  val.parquet\n  test.csv\n  notes.txt",
            "after": "自动按文件后缀分发到对应 formatter:\n  train.jsonl  →  jsonl_formatter  →  10000 行\n  val.parquet  →  parquet_formatter →  1000 行\n  test.csv     →  csv_formatter    →  500 行\n  notes.txt    →  text_formatter   →  1 行\n[统一 schema,各文件行 dict 拼接到同一 dataset]",
        },
    ],
    "remote_formatter": [
        {
            "before": "repo_id: 'imdb' (HuggingFace Hub)\nconfig: 'plain_text'\nsplit: 'train'",
            "after": "远程拉取 imdb 数据集 train split:\n  25000 条电影评论\n  schema: {'text': str, 'label': int}\n  缓存到 ~/.cache/huggingface/",
        },
        {
            "before": "repo_id: 'wiki_lingua_chinese'\nsplit: ['train[:1000]', 'validation[:100]']",
            "after": "远程拉取指定 split 子集:\n  1000 训练样本 + 100 验证样本\n  按文件后缀分发到对应 formatter 解析",
        },
    ],
}


def main() -> None:
    engine = create_engine(str(settings.database_url).replace("+asyncpg", "+psycopg2"))
    Session = sessionmaker(bind=engine)
    updated, missing = 0, []
    with Session() as session:
        for name, demos in DEMOS.items():
            op = session.get(Operator, name)
            if op is None:
                missing.append(name)
                continue
            op.effect_demo = demos
            updated += 1
        session.commit()
    print(f"已写入: {updated} 个 formatter")
    if missing:
        print(f"DB 不存在(跳过): {missing}")


if __name__ == "__main__":
    main()
