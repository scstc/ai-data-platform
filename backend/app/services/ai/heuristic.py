"""启发式 AI 提供者（默认实现，零外部依赖）。

逻辑移植自前端 mock（frontend/mock/dataPlatform.ts），保证后端化后行为一致：
- infer_schema：JSON → JSONL（≥60% 行成功）→ TSV/CSV 表头探测 → 兜底 text/plain
- generate_task：中文关键词 → cron 调度 + 数据源类型 + dbKind
- qa：6 条固定问答 + 兜底引导语

confidence、latencyMs 等带随机性的字段沿用 mock 的取值区间。
"""

from __future__ import annotations

import json
import random
import re
from typing import Any

from app.services.ai.base import AIProvider

# ---------------------------------------------------------------------------
# 字段类型推断
# ---------------------------------------------------------------------------


def _js_type(value: Any) -> str:
    """模拟 mock 的 jsType：在原生类型基础上对字符串做正则识别。"""
    if value is None:
        return "null"
    if isinstance(value, list):
        return "array"
    if isinstance(value, bool):
        # 注意：Python 中 bool 是 int 子类，必须先于数字判断
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "float"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, str):
        if re.match(r"^\d{4}-\d{2}-\d{2}([ T]\d{2}:\d{2}(:\d{2})?)?", value):
            return "datetime"
        if re.match(r"^-?\d+$", value):
            return "integer"
        if re.match(r"^-?\d*\.\d+$", value):
            return "float"
        if re.match(r"^(true|false)$", value, re.IGNORECASE):
            return "boolean"
        return "string"
    return type(value).__name__


def _is_empty(value: Any) -> bool:
    """对应 mock 中 `v == null || v === ''` 的空值判定。"""
    return value is None or value == ""


def _fields_from_objects(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """从一组 JSON 对象推断字段列表（保持首次出现顺序）。"""
    names: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in names:
                names.append(key)

    fields: list[dict[str, Any]] = []
    for name in names:
        example = ""
        nullable = False
        field_type = "string"
        for row in rows:
            value = row.get(name)
            if _is_empty(value):
                nullable = True
                continue
            if example == "":
                example = (
                    json.dumps(value, ensure_ascii=False)
                    if isinstance(value, (dict, list))
                    else f"{value}"
                )
                field_type = _js_type(value)
        if any(name not in row for row in rows):
            nullable = True
        fields.append(
            {"name": name, "type": field_type, "example": example, "nullable": nullable}
        )
    return fields


def _rand(min_v: float, max_v: float) -> float:
    """模拟 mock 的 confidence 区间取值，保留两位小数。"""
    return round((min_v + random.random() * (max_v - min_v)) * 100) / 100


def infer_schema_from_sample(sample_raw: str) -> dict[str, Any]:
    """根据样本字符串推断 Schema，返回 InferredSchema 形状 dict。"""
    sample = (sample_raw or "").strip()

    # 1) 纯 JSON（对象或数组）
    try:
        parsed = json.loads(sample)
    except (json.JSONDecodeError, ValueError):
        parsed = None
    else:
        rows = parsed if isinstance(parsed, list) else [parsed]
        obj_rows = [
            r for r in rows if isinstance(r, dict)
        ]
        if obj_rows:
            return {
                "format": "application/json",
                "confidence": _rand(0.9, 0.98),
                "fields": _fields_from_objects(obj_rows),
                "suggestion": (
                    "检测到标准 JSON 结构，建议以「文件上传 / 对象存储」方式接入，"
                    "格式选择 json；如为数组建议逐元素拆分为记录。"
                ),
                "recommendedConfig": {"format": "json", "encoding": "utf-8"},
            }

    # 2) JSONL（逐行 JSON 对象）
    lines = [line for line in re.split(r"\r?\n", sample) if line.strip() != ""]
    if lines:
        obj_rows = []
        ok_lines = 0
        for line in lines:
            try:
                obj = json.loads(line.strip())
            except (json.JSONDecodeError, ValueError):
                continue
            if isinstance(obj, dict):
                obj_rows.append(obj)
                ok_lines += 1
        # 与 mock 一致：成功行数需达到总行数的 60%（向上取整，至少 1）
        threshold = max(1, -(-len(lines) * 6 // 10))
        if ok_lines >= threshold and obj_rows:
            return {
                "format": "application/x-ndjson",
                "confidence": _rand(0.88, 0.97),
                "fields": _fields_from_objects(obj_rows),
                "suggestion": (
                    "检测到 JSON Lines（每行一个 JSON 对象），"
                    "建议以「文件上传」方式接入，"
                    "格式选择 jsonl，适合大规模语料流式处理。"
                ),
                "recommendedConfig": {"format": "jsonl", "encoding": "utf-8"},
            }

    # 3) CSV / TSV（按分隔符探测表头）
    if lines:
        header = lines[0]
        tab_count = header.count("\t")
        comma_count = header.count(",")
        use_tab = tab_count > 0 and tab_count >= comma_count
        delimiter = "\t" if use_tab else ","
        cols = [c.strip() for c in header.split(delimiter)]
        if len(cols) >= 2:
            data_rows = [
                [c.strip() for c in line.split(delimiter)] for line in lines[1:]
            ]
            fields: list[dict[str, Any]] = []
            for idx, name in enumerate(cols):
                example = ""
                nullable = False
                field_type = "string"
                for row in data_rows:
                    value = row[idx] if idx < len(row) else None
                    if _is_empty(value):
                        nullable = True
                        continue
                    if example == "":
                        example = value
                        field_type = _js_type(value)
                if not data_rows:
                    example = ""
                fields.append(
                    {
                        "name": name or f"col_{idx + 1}",
                        "type": field_type,
                        "example": example,
                        "nullable": nullable,
                    }
                )
            fmt = "text/tab-separated-values" if use_tab else "text/csv"
            return {
                "format": fmt,
                "confidence": _rand(0.85, 0.94),
                "fields": fields,
                "suggestion": (
                    "检测到制表符分隔（TSV）表格，建议以「文件上传」方式接入，"
                    "格式选择 tsv，首行作为表头。"
                    if use_tab
                    else "检测到逗号分隔（CSV）表格，建议以「文件上传」方式接入，"
                    "格式选择 csv，首行作为表头。"
                ),
                "recommendedConfig": {
                    "format": "tsv" if use_tab else "csv",
                    "delimiter": delimiter,
                    "hasHeader": True,
                    "encoding": "utf-8",
                },
            }

    # 4) 兜底：纯文本单字段
    return {
        "format": "text/plain",
        "confidence": 0.85,
        "fields": [
            {
                "name": "text",
                "type": "string",
                "example": sample[:60],
                "nullable": False,
            }
        ],
        "suggestion": (
            "未识别出结构化格式，按纯文本处理，建议以「文件上传」方式接入，"
            "格式选择 text，每行作为一条文本记录。"
        ),
        "recommendedConfig": {"format": "text", "encoding": "utf-8"},
    }


# ---------------------------------------------------------------------------
# 采集任务生成（中文关键词启发式）
# ---------------------------------------------------------------------------


def generate_task_from_prompt(prompt_raw: str) -> dict[str, Any]:
    """根据自然语言描述生成采集任务配置，返回 GeneratedTaskConfig 形状 dict。"""
    prompt = (prompt_raw or "").strip()
    p = prompt.lower()
    reasons: list[str] = []

    # 调度解析
    schedule: dict[str, Any] = {"mode": "once"}
    hour_match = re.search(r"(凌晨|早上|上午|下午|晚上)?\s*(\d{1,2})\s*[点时:]", prompt)
    if re.search(r"每小时|每个小时|hourly", prompt):
        schedule = {"mode": "cron", "cron": "0 * * * *"}
        reasons.append("包含「每小时」→ 生成每小时整点 cron `0 * * * *`")
    elif re.search(r"每周|每星期|weekly", prompt):
        cron_h = int(hour_match.group(2)) if hour_match else 1
        schedule = {"mode": "cron", "cron": f"0 {cron_h} * * 1"}
        reasons.append(f"包含「每周」→ 生成每周一 {cron_h} 点 cron `0 {cron_h} * * 1`")
    elif re.search(r"每天|每日|凌晨|daily", prompt):
        if hour_match:
            cron_h = int(hour_match.group(2))
        elif re.search(r"凌晨", prompt):
            cron_h = 2
        else:
            cron_h = 0
        schedule = {"mode": "cron", "cron": f"0 {cron_h} * * *"}
        reasons.append(
            f"包含「每天/凌晨」→ 生成每天 {cron_h} 点 cron `0 {cron_h} * * *`"
        )
    else:
        reasons.append("未识别到周期关键词 → 调度方式为「单次执行」")

    # 数据源类型解析
    datasource_type = "api"
    if re.search(r"s3|oss|对象存储|对象存储桶|bucket", p) or re.search(
        r"对象存储", prompt
    ):
        datasource_type = "s3"
        reasons.append("包含「S3/OSS/对象存储」→ 数据源类型 s3")
    elif re.search(r"hdfs", p):
        datasource_type = "hdfs"
        reasons.append("包含「HDFS」→ 数据源类型 hdfs")
    elif re.search(
        r"数据库|database|达梦|dameng|hive|doris|kingbase|金仓|gaussdb|goldendb|hologres|sequoiadb|表|sql",
        prompt + p,
    ):
        datasource_type = "database"
        reasons.append("包含「数据库/达梦/hive 等」→ 数据源类型 database")
    elif re.search(r"api|接口|http|rest", p):
        datasource_type = "api"
        reasons.append("包含「API/接口」→ 数据源类型 api")
    else:
        reasons.append("未识别到数据源关键词 → 默认数据源类型 api")

    # 名称摘要：取前 20 字
    name = (re.sub(r"\s+", "", prompt)[:20] or "新建采集任务") + "采集任务"

    config: dict[str, Any] = {"datasourceType": datasource_type}
    if datasource_type == "database":
        kind_map = {
            "达梦": "dameng",
            "dameng": "dameng",
            "hive": "hive",
            "doris": "doris",
            "金仓": "kingbase",
            "kingbase": "kingbase",
            "gaussdb": "gaussdb",
            "goldendb": "goldendb",
            "hologres": "hologres",
            "sequoiadb": "sequoiadb",
        }
        for kw, kind in kind_map.items():
            if kw in prompt or kw in p:
                config["dbKind"] = kind
                reasons.append(f"识别到数据库品牌「{kw}」→ dbKind {kind}")
                break

    return {
        "name": name,
        "datasourceType": datasource_type,
        "schedule": schedule,
        "config": config,
        "explanation": f"根据描述「{prompt}」的关键词解析：{'；'.join(reasons)}。",
    }


# ---------------------------------------------------------------------------
# 固定问答
# ---------------------------------------------------------------------------

_QA_PAIRS: list[dict[str, Any]] = [
    {
        "keywords": ["哪些数据源", "支持的数据源", "数据源类型", "支持什么数据源"],
        "answer": (
            "平台支持 4 类数据源：对象存储（S3/OSS）、HDFS、数据库、HTTP API。"
            "其中数据库支持达梦、GoldenDB、人大金仓、GaussDB、Hologres、SequoiaDB、Hive、"
            "Doris 共 8 种国产/主流数据库。"
        ),
    },
    {
        "keywords": ["哪些格式", "支持的格式", "文件格式", "支持什么格式"],
        "answer": (
            "上传文件支持 JSON、JSONL（JSON Lines）、CSV、TSV、纯文本（text）等格式。"
            "上传后可用「AI 推断 Schema」自动识别字段名、类型与接入建议。"
        ),
    },
    {
        "keywords": [
            "如何建采集任务",
            "怎么建采集任务",
            "创建采集任务",
            "新建采集任务",
            "采集任务怎么",
        ],
        "answer": (
            "先在「数据源管理」中创建并测试连接一个数据源，再到「采集任务」点击新建，"
            "选择该数据源、配置调度方式（单次或 cron 周期），"
            "保存后任务即进入待调度状态。"
            "也可用「AI 生成任务」直接用自然语言描述生成配置。"
        ),
    },
    {
        "keywords": [
            "如何建数据源",
            "怎么建数据源",
            "创建数据源",
            "新建数据源",
            "添加数据源",
        ],
        "answer": (
            "进入「数据源管理」，点击新建，选择类型（S3/HDFS/数据库/API），填写连接配置后"
            "点「测试连接」，连通后保存即可。数据库类型需额外选择具体品牌（如达梦、Hive）。"
        ),
    },
    {
        "keywords": ["cron", "定时", "调度", "周期", "每天", "怎么定时"],
        "answer": (
            "采集任务调度支持「单次」和「cron 周期」两种。"
            "cron 表达式为标准 5 段格式（分 时 日 月 周），"
            "例如每天凌晨 2 点为 `0 2 * * *`。"
            "用「AI 生成任务」时输入「每天凌晨」等描述会自动生成对应 cron。"
        ),
    },
    {
        "keywords": ["测试连接", "连接失败", "连不上", "连接不上", "为什么失败"],
        "answer": (
            "测试连接会校验关键配置是否填齐："
            "S3 需 endpoint/bucket/accessKey/secretKey，HDFS 需 nameNode/path，"
            "数据库需 host/port/database/username/password，API 需 url。"
            "任一必填项缺失都会返回连接失败，请补全后重试。"
        ),
    },
]

_QA_FALLBACK = (
    "抱歉，暂时无法直接回答该问题。你可以咨询：支持哪些数据源 / 支持哪些文件格式 / "
    "如何新建数据源 / 如何创建采集任务 / cron 定时怎么配 / 测试连接为什么失败。"
)


def answer_question(question_raw: str) -> str:
    """命中固定问答返回对应答案，否则返回兜底引导语。"""
    q = (question_raw or "").lower()
    for pair in _QA_PAIRS:
        if any(k.lower() in q for k in pair["keywords"]):
            return pair["answer"]
    return _QA_FALLBACK


# ---------------------------------------------------------------------------
# 算子流水线生成（goal 关键词 → 场景分组）
# ---------------------------------------------------------------------------

# goal 关键词 → 场景分组(命中则取该场景下若干 ready 算子)
_GOAL_SCENARIO_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("去重", "重复", "dedup"), "去重"),
    (("脱敏", "隐私", "敏感"), "隐私脱敏"),
    (("繁简", "中文", "简体", "繁体"), "中文处理"),
    (("质量", "过滤", "筛"), "质量过滤"),
    (("清洗", "html", "url", "空白"), "文本清洗"),
]


def generate_pipeline_from_goal(
    goal_raw: str, ready_ops: list[dict[str, Any]]
) -> dict[str, Any]:
    """启发式:按 goal 关键词命中场景,取该场景下最多 4 个 ready 算子串成流水线。"""
    goal = (goal_raw or "").lower()
    scenarios: list[str] = [
        s for kws, s in _GOAL_SCENARIO_KEYWORDS if any(k in goal for k in kws)
    ]
    if not scenarios:
        scenarios = ["文本清洗", "质量过滤"]
    picked: list[dict[str, Any]] = []
    for sc in scenarios:
        for op in ready_ops:
            seen = {p["name"] for p in picked}
            if op.get("scenario") == sc and op["name"] not in seen:
                picked.append({"name": op["name"], "params": {}})
            if len(picked) >= 6:
                break
    explanation = (
        f"按目标「{goal_raw}」匹配场景 {scenarios},"
        f"推荐 {len(picked)} 个可运行算子(启发式,可自行调整)。"
    )
    return {"operators": picked, "explanation": explanation}


# ---------------------------------------------------------------------------
# 数据集命名（首个文件名清洗）
# ---------------------------------------------------------------------------


def suggest_name_from_files(
    filenames: list[str], data_type: str, category: str | None
) -> dict[str, str]:
    """启发式数据集命名：取首个文件名去路径/扩展名并清洗；空则按格式兜底。"""
    first = filenames[0] if filenames else ""
    stem = first.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]
    dot = stem.rfind(".")
    if dot > 0:
        stem = stem[:dot]
    stem = re.sub(r"[_\-]+", " ", stem).strip()
    if not stem:
        stem = f"{(data_type or '数据').upper()} 数据集"
    return {"name": stem[:60]}


# ---------------------------------------------------------------------------
# Provider
# ---------------------------------------------------------------------------


class HeuristicProvider(AIProvider):
    """纯本地启发式实现，零外部依赖，作为默认 provider 与 LLM 失败时的兜底。"""

    async def infer_schema(self, sample: str) -> dict[str, Any]:
        return infer_schema_from_sample(sample)

    async def generate_task(self, prompt: str) -> dict[str, Any]:
        return generate_task_from_prompt(prompt)

    async def qa(self, question: str) -> dict[str, str]:
        return {"answer": answer_question(question)}

    async def generate_pipeline(
        self, goal: str, ready_ops: list[dict[str, Any]]
    ) -> dict[str, Any]:
        return generate_pipeline_from_goal(goal, ready_ops)

    async def suggest_dataset_name(
        self, filenames: list[str], data_type: str, category: str | None
    ) -> dict[str, str]:
        return suggest_name_from_files(filenames, data_type, category)

    async def moderate_texts(self, texts: list[str]) -> list[dict[str, Any]]:
        """无 LLM 时的保守兜底:全部判为正常。

        语义判定能力依赖 LLM;启发式不做关键词二次判断——违规词命中已由
        review.py 的内置词表(source=flagged_words)与规则覆盖,这里再判会重复计数。
        故返回全 正常,LLM source 对结果无贡献(降级到规则+PII)。
        """
        return [
            {
                "flagged": False,
                "category": "other",
                "severity": "low",
                "reason": "",
            }
            for _ in texts
        ]
