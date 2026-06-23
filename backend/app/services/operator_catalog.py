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

from app.services.capabilities import Capabilities, get_capabilities

_MEDIA_MODALITIES = {"image", "video", "audio", "multimodal"}

_CATALOG_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "operators_catalog.json"
)

# 蒸馏场景白名单:平台侧硬编码,data-juicer 仓无 PR
# 限定"第一期只暴露轻量 CPU 算子":文本规则 filter + 文本去重 + 5 类 selector
# (不含 LLM 评分、embedding 相似度、image/video 多模态算子)
DISTILLATION_OPS: frozenset[str] = frozenset(
    {
        # text filters
        "text_length_filter",
        "token_num_filter",
        "word_repetition_filter",
        "character_repetition_filter",
        "language_id_score_filter",
        "perplexity_filter",
        "flagged_words_filter",
        "stopwords_filter",
        "alphanumeric_filter",
        "special_characters_filter",
        # deduplicators (轻量,无 GPU/embedding)
        "document_minhash_deduplicator",
        "document_simhash_deduplicator",
        # selectors
        "topk_specified_field_selector",
        "range_specified_field_selector",
        "frequency_specified_field_selector",
        "tags_specified_field_selector",
        "random_selector",
    }
)


def is_distillation_operator(name: str) -> bool:
    """该算子是否在蒸馏白名单内(供 distillation router 校验)。"""
    return name in DISTILLATION_OPS


# 清洗场景白名单:数据清洗(需求 #7)的"清洗算子"——规则类 mapper + 繁简 / 标点 / 表情等
# 与加工页通用 mapper 列表重叠但更聚焦,前端在加工页顶部 Select 选「清洗」时只显示这些
CLEANSING_OPS: frozenset[str] = frozenset(
    {
        # 字符级 / 格式规范化
        "remove_specific_chars_mapper",
        "remove_repeat_sentences_mapper",
        "remove_long_duplicate_sentences_mapper",
        "remove_text_to_remove_mapper",
        "clean_email_mapper",
        "clean_ip_mapper",
        "clean_links_mapper",
        "clean_html_mapper",
        "chinese_convert_mapper",
        "punctuation_normalization_mapper",
        "whitespace_normalization_mapper",
        "unicode_normalization_mapper",
        "replace_content_mapper",
        # 语言识别(虽属 filter,但在清洗场景里常用)
        "language_id_score_filter",
        "flagged_words_filter",
    }
)


# 合成与增强白名单:全部 LLM-based mapper(需求 #8)
# 需求文档 #8 关键能力:LLM 造数据 + LLM 改写 + 蒸馏 + 区分原始/合成
# 全部依赖 LLM,未配 OPENAI_API_KEY 时 _operator_block 走 needs_api 拦截
# 拆为两个独立白名单:合成(make)=造新数据;增强(augment)=改写已有数据
MAKE_OPS: frozenset[str] = frozenset(
    {
        # 合成:从无结构 / 种子 / 上下文生成新数据
        "generate_qa_from_text_mapper",  # 1→N,无结构文本→QA 对
        "generate_qa_from_examples_mapper",  # Self-Instruct:从种子示例生成新 QA
        "optimize_prompt_mapper",  # 上下文扩展:few-shot 合成新 prompt
    }
)

AUGMENT_OPS: frozenset[str] = frozenset(
    {
        # 增强:改写/扩写/校准/打标(均 1→1)
        "optimize_qa_mapper",  # 优化 QA 对(同时优化 q+a)
        "optimize_query_mapper",  # Evol-Instruct:只优化 query
        "optimize_response_mapper",  # Evol-Instruct:只优化 response
        "sentence_augmentation_mapper",  # 通用 paraphrasing/改写/扩写
        "calibrate_qa_mapper",  # 事实校准 QA
        "calibrate_response_mapper",  # 事实校准 response
        "llm_extract_mapper",  # 通用结构化抽取
        "pair_preference_mapper",  # DPO 偏好数据构造
        "text_tagging_by_prompt_mapper",  # LLM 文本分类打标
    }
)


def is_make_operator(name: str) -> bool:
    return name in MAKE_OPS


def is_augment_operator(name: str) -> bool:
    return name in AUGMENT_OPS

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
    """单个算子 → camelCase 出参形态;``runnable`` 用运行时有效状态覆盖。

    一个算子可以同时打多个 scenarioGroup 标签(同时在加工和清洗场景里都合理):
    前端 OperatorLibrary 用 scenarioGroup 过滤时显示"凡含此标签的算子"即可。
    """
    out = {_OP_KEY_MAP.get(k, k): v for k, v in op.items()}
    out["runnable"] = effective_runnable(op)
    # 多个 scenarioGroup 标签并存:首次设置,后续 append。
    # (前端暂只展示第一个;未来多标签场景再切到数组。)
    if out["name"] in DISTILLATION_OPS:
        out["scenarioGroup"] = "distillation"
    elif out["name"] in CLEANSING_OPS:
        out["scenarioGroup"] = "cleansing"
    elif out["name"] in MAKE_OPS:
        out["scenarioGroup"] = "make"
    elif out["name"] in AUGMENT_OPS:
        out["scenarioGroup"] = "augment"
    return out


def meta_api() -> dict[str, Any]:
    """目录概览 → camelCase 出参形态;``byRunnable`` 按当前环境实时重算。"""
    out = {_META_KEY_MAP.get(k, k): v for k, v in catalog_meta().items()}
    caps = get_capabilities()
    counts: dict[str, int] = {}
    for op in all_operators():
        status = effective_runnable(op, caps)
        counts[status] = counts.get(status, 0) + 1
    out["byRunnable"] = counts
    return out


# ---------------------------------------------------------------------------
# 有效可运行状态:静态需求(resource_class)× 运行时能力(capabilities)
# ---------------------------------------------------------------------------
# 目录 JSON 里烤死的 ``runnable`` 是"无 GPU 环境"快照,运行时不再采信——改由
# 本函数按当前环境真实能力实时计算,守门 / 徽章 / 计数 / AI 上下文统一口径。
def effective_runnable(op: dict[str, Any], caps: Capabilities | None = None) -> str:
    """算子在当前环境的有效可运行状态。

    优先级与构建期 ``runnable()`` 一致,但每条算力门改为按 ``caps`` 实时判定:
    媒体模态(平台受管数据集为文本 jsonl,永不适用)→ needs_media;否则按
    resource_class / ray_ 前缀对应所需能力,满足则 ready,不满足给对应阻塞态。
    """
    caps = caps or get_capabilities()
    res = op["resource_class"]
    mod = set(op.get("modality") or [])
    name = op["name"]
    if mod & _MEDIA_MODALITIES:
        return "needs_media"
    if res == "api_llm":
        return "ready" if caps.llm else "needs_api"
    if name.startswith("ray_"):
        return "ready" if caps.ray else "needs_compute"
    if res in ("gpu", "hf_model"):
        return "ready" if caps.cuda else "needs_compute"
    if res == "vllm":
        return "ready" if caps.vllm else "needs_compute"
    return "ready"


# ---------------------------------------------------------------------------
# 资源前置校验(执行守门)—— "把算子路由到合适后端"的落点
# ---------------------------------------------------------------------------
def runnable_reason(
    name: str, *, llm_configured: bool = False, media_ok: bool = False
) -> str | None:
    """返回该算子在当前环境不可执行的原因;None 表示可执行。

    ``llm_configured``:平台已配 LLM API,放行 ``needs_api`` 算子(显式覆盖探测值)。
    ``media_ok``:输入是媒体/manifest 数据集且多模态引擎就绪,放行 ``needs_media`` 算子。
    其余算力门(GPU/vLLM/Ray)由 ``effective_runnable`` 按运行时能力实时判定。
    """
    op = get_operator(name)
    if op is None:
        return f"未知算子:{name}"
    caps = get_capabilities()
    if llm_configured:
        caps = Capabilities(cuda=caps.cuda, vllm=caps.vllm, ray=caps.ray, llm=True)
    status = effective_runnable(op, caps)
    if status == "ready":
        return None
    if status == "needs_api":
        return f"算子 {name} 需要配置 LLM API(在 .env 设置 OPENAI_*)"
    if status == "needs_media":
        if media_ok:
            return None
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


def legacy_operators(
    *, llm_configured: bool = False, multimodal_ready: bool = False
) -> list[dict[str, Any]]:
    """旧 5 字段形态(name/category/label/description/params),供加工页下拉与动态表单。

    默认仅含 ready 算子(向后兼容)。``llm_configured`` 为真时额外纳入
    ``needs_api`` 算子;``multimodal_ready`` 为真时纳入 ``needs_media`` 算子——它们
    仍在提交时按数据类型二次校验(``runnable_reason``)。``needs_compute`` 始终不列出。
    ``category`` 用场景分组(比 mapper/filter 更贴近用户),``params`` 已归一为表单字段。
    """
    caps = get_capabilities()
    if llm_configured:
        caps = Capabilities(cuda=caps.cuda, vllm=caps.vllm, ray=caps.ray, llm=True)
    allowed = {"ready"}
    if multimodal_ready:
        allowed.add("needs_media")
    result: list[dict[str, Any]] = []
    for op in all_operators():
        if effective_runnable(op, caps) not in allowed:
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
    caps = get_capabilities()

    def match(op: dict[str, Any]) -> bool:
        if scenario and op["scenario_group"] != scenario:
            return False
        if category and op["category"] != category:
            return False
        if modality and modality not in (op["modality"] or []):
            return False
        if resource_class and op["resource_class"] != resource_class:
            return False
        if runnable and effective_runnable(op, caps) != runnable:
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
def ready_operator_context(category: str | None = None) -> list[dict[str, Any]]:
    """供 LLM 提示的 ready 算子清单:name + 中文标签 + 场景 + 合法参数名。

    ``category`` 可选,传入时只保留该类算子(如 ``"filter"``)。
    """
    caps = get_capabilities()
    ctx: list[dict[str, Any]] = []
    for op in all_operators():
        if effective_runnable(op, caps) != "ready":
            continue
        if category and op["category"] != category:
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
    caps = get_capabilities()
    result: list[dict[str, Any]] = []
    for step in steps:
        name = step.get("name")
        op = get_operator(name) if name else None
        if op is None or effective_runnable(op, caps) != "ready":
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
