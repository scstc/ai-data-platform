"""加工算子目录:加载构建期生成的全量目录(212 算子),提供查询/分面/UI 归一。

目录由 ``backend/scripts/build_operator_catalog.py`` 解析 data-juicer 文档
(``docs/Operators.md`` + ``docs/operators/**``)生成,随后端发布为
``app/data/operators_catalog.json``。后端 py3.12 无法直接 import DJ(py3.11)
的算子类,故采用"构建期快照";DJ 升级后重跑脚本刷新即可。

设计见 docs/plan/04-算子市场设计.md。
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

_CATALOG_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "operators_catalog.json"
)

_MAXSIZE = 9223372036854775807  # sys.maxsize:DJ 用作"无上限"的默认,表单里清空


@lru_cache(maxsize=1)
def _data() -> dict[str, Any]:
    return json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))


def catalog_meta() -> dict[str, Any]:
    """目录概览(总数/各维度分布/推荐数),驱动市场筛选项与统计卡。"""
    return _data()["meta"]


def all_operators() -> list[dict[str, Any]]:
    return _data()["operators"]


@lru_cache(maxsize=1)
def _by_name() -> dict[str, dict[str, Any]]:
    return {o["name"]: o for o in all_operators()}


def get_operator(name: str) -> dict[str, Any] | None:
    return _by_name().get(name)


def operator_names() -> set[str]:
    """全部 212 个算子名(用于存在性校验)。"""
    return set(_by_name())


# ---------------------------------------------------------------------------
# 出参 camelCase 化(与平台其余 API 一致;只浅改顶层键,不动嵌套数据键)
# ---------------------------------------------------------------------------
_OP_KEY_MAP = {
    "summary_en": "summaryEn",
    "summary_zh": "summaryZh",
    "desc_en": "descEn",
    "desc_zh": "descZh",
    "resource_class": "resourceClass",
    "scenario_group": "scenarioGroup",
    "zh_label": "zhLabel",
    "zh_usage_tip": "zhUsageTip",
    "detail_page": "detailPage",
}
_META_KEY_MAP = {
    "with_detail_page": "withDetailPage",
    "by_category": "byCategory",
    "by_resource_class": "byResourceClass",
    "by_modality": "byModality",
    "by_scenario": "byScenario",
    "by_runnable": "byRunnable",
}


def to_api(op: dict[str, Any]) -> dict[str, Any]:
    """单个算子 → camelCase 出参形态。"""
    return {_OP_KEY_MAP.get(k, k): v for k, v in op.items()}


def meta_api() -> dict[str, Any]:
    """目录概览 → camelCase 出参形态(保留嵌套统计的原始键)。"""
    return {_META_KEY_MAP.get(k, k): v for k, v in catalog_meta().items()}


# ---------------------------------------------------------------------------
# 资源前置校验(执行守门)—— "把算子路由到合适后端"的落点
# ---------------------------------------------------------------------------
def runnable_reason(name: str, *, llm_configured: bool = False) -> str | None:
    """返回该算子在当前环境不可执行的原因;None 表示可执行。"""
    op = get_operator(name)
    if op is None:
        return f"未知算子:{name}"
    status = op["runnable"]
    if status == "ready":
        return None
    if status == "needs_api":
        if llm_configured:
            return None
        return f"算子 {name} 需要配置 LLM API(在 .env 设置 OPENAI_*)"
    if status == "needs_media":
        return f"算子 {name} 需要图像/音视频数据,当前文本数据集不适用"
    return f"算子 {name} 需要 GPU/模型算力,当前环境不可执行"


# ---------------------------------------------------------------------------
# UI 参数归一(供加工页动态表单)
# ---------------------------------------------------------------------------
# 经实测的精选参数:保留原 9 算子里需 select / 友好默认值的项
_CURATED_PARAMS: dict[str, list[dict[str, Any]]] = {
    "chinese_convert_mapper": [
        {
            "name": "mode",
            "label": "模式",
            "type": "select",
            "default": "t2s",
            "options": ["t2s", "s2t", "s2tw", "tw2s", "s2hk", "hk2s"],
        },
    ],
    "remove_specific_chars_mapper": [
        {
            "name": "chars_to_remove",
            "label": "待移除字符",
            "type": "string",
            "default": "◆●■►▼▲▴∆▻▷❖♡□",
        },
    ],
    "text_length_filter": [
        {"name": "min_len", "label": "最小长度", "type": "number", "default": 10},
        {"name": "max_len", "label": "最大长度", "type": "number", "default": 100000},
    ],
}


def _parse_default(type_str: str, raw: str) -> Any:
    """把 DJ 文档里的字符串默认值解析成对应 JSON 类型(超大 int 视作无默认)。"""
    raw = (raw or "").strip().strip("'\"")
    if raw in ("", "None"):
        return None
    if "bool" in type_str:
        return raw == "True"
    if "int" in type_str:
        try:
            value = int(raw)
        except ValueError:
            return None
        return None if abs(value) >= _MAXSIZE else value
    if "float" in type_str:
        try:
            return float(raw)
        except ValueError:
            return None
    return raw


def _ui_field(param: dict[str, Any]) -> dict[str, Any] | None:
    """把 DJ 参数表的一行转成前端表单字段;无意义的 args/kwargs 跳过。"""
    name = param["name"]
    if name in ("args", "kwargs"):
        return None
    type_str = param.get("type", "")
    if "bool" in type_str:
        ftype = "switch"
    elif "int" in type_str or "float" in type_str:
        ftype = "number"
    else:
        ftype = "string"
    return {
        "name": name,
        "label": name,
        "type": ftype,
        "default": _parse_default(type_str, param.get("default", "")),
        "desc": param.get("desc", ""),
    }


def _ui_params(op: dict[str, Any]) -> list[dict[str, Any]]:
    if op["name"] in _CURATED_PARAMS:
        return _CURATED_PARAMS[op["name"]]
    return [f for f in (_ui_field(p) for p in op.get("params", [])) if f]


def legacy_operators() -> list[dict[str, Any]]:
    """旧 5 字段形态(name/category/label/description/params),仅含 ready 算子。

    供现有加工页下拉与动态参数表单使用,保持向后兼容。``category`` 用场景分组
    (比 mapper/filter 更贴近用户),``params`` 已归一为表单字段。
    """
    result: list[dict[str, Any]] = []
    for op in all_operators():
        if op["runnable"] != "ready":
            continue
        result.append(
            {
                "name": op["name"],
                "category": op["scenario_group"],
                "label": op["zh_label"],
                "description": op["summary_zh"],
                "params": _ui_params(op),
            }
        )
    return result


# ---------------------------------------------------------------------------
# 市场查询(分面 + 分页)
# ---------------------------------------------------------------------------
def query_catalog(
    *,
    scenario: str | None = None,
    category: str | None = None,
    modality: str | None = None,
    resource_class: str | None = None,
    runnable: str | None = None,
    recommend: bool | None = None,
    keyword: str | None = None,
    current: int = 1,
    page_size: int = 24,
) -> dict[str, Any]:
    """按多维条件过滤算子目录,返回分页数据 + 总数。"""
    kw = keyword.lower().strip() if keyword else None

    def match(op: dict[str, Any]) -> bool:
        if scenario and op["scenario_group"] != scenario:
            return False
        if category and op["category"] != category:
            return False
        if modality and modality not in (op["modality"] or []):
            return False
        if resource_class and op["resource_class"] != resource_class:
            return False
        if runnable and op["runnable"] != runnable:
            return False
        if recommend is not None and op["recommend"] != recommend:
            return False
        if kw:
            hay = (
                op["name"]
                + (op.get("summary_zh") or "")
                + (op.get("zh_label") or "")
            ).lower()
            if kw not in hay:
                return False
        return True

    filtered = [op for op in all_operators() if match(op)]
    total = len(filtered)
    start = (current - 1) * page_size
    return {"data": filtered[start : start + page_size], "total": total}


# ---------------------------------------------------------------------------
# AI 流水线生成:ready 算子上下文 + 确定性校验(白名单 + 合法参数键)
# ---------------------------------------------------------------------------
def ready_operator_context() -> list[dict[str, Any]]:
    """供 LLM 提示的 ready 算子清单:name + 中文标签 + 场景 + 合法参数名。"""
    ctx: list[dict[str, Any]] = []
    for op in all_operators():
        if op["runnable"] != "ready":
            continue
        ctx.append(
            {
                "name": op["name"],
                "label": op.get("zh_label") or op["name"],
                "scenario": op.get("scenario_group") or "",
                "params": [p["name"] for p in op.get("params", [])],
            }
        )
    return ctx


def sanitize_pipeline(
    steps: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """裁剪到可执行流水线:丢弃未知/非 ready 算子,删除不在该算子参数表里的键。"""
    result: list[dict[str, Any]] = []
    for step in steps:
        name = step.get("name")
        op = get_operator(name) if name else None
        if op is None or op["runnable"] != "ready":
            continue
        # 排除 args/kwargs 变长占位项(与 _ui_field 口径一致):它们不是可配置参数,
        # 若放行经 build_config 进入 DJ YAML 会在运行期被 dj-process 当非法参数报错。
        allowed = {
            p["name"]
            for p in op.get("params", [])
            if p["name"] not in ("args", "kwargs")
        }
        raw = step.get("params") or {}
        params = {k: v for k, v in raw.items() if k in allowed}
        result.append({"name": name, "params": params})
    return result
