"""加工算子目录:从数据库加载算子,提供查询/分面/UI 归一。

原设计:构建期生成 JSON 快照(operators_catalog.json)
新设计:算子入库,支持运行时统计、动态查询、用户自定义算子

设计见 docs/plan/04-算子市场设计.md。
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.db import get_db
from app.models.operator import Operator
from app.services.capabilities import Capabilities, get_capabilities

_MEDIA_MODALITIES = {"image", "video", "audio", "multimodal"}

_MAXSIZE = 9223372036854775807  # sys.maxsize:DJ 用作"无上限"的默认,表单里清空


def _operator_to_dict(op: Operator) -> dict[str, Any]:
    """ORM 模型转字典(兼容原 JSON 结构)。"""
    return {
        "name": op.name,
        "category": op.category,
        "zh_label": op.zh_label,
        "summary_en": op.summary_en,
        "summary_zh": op.summary_zh,
        "desc_en": op.desc_en,
        "desc_zh": op.desc_zh,
        "zh_usage_tip": op.zh_usage_tip,
        "scenario_group": op.scenario_group,
        "resource_class": op.resource_class,
        "modality": op.modality,
        "frameworks": op.frameworks,
        "params": op.params,
        "example": op.example,
        "detail_page": op.detail_page,
        "recommend": op.recommend,
        "runnable": op.runnable,
        "usage_count": op.usage_count,
    }


def all_operators() -> list[dict[str, Any]]:
    """获取全部算子(从数据库)。"""
    db = next(get_db())
    ops = db.execute(select(Operator)).scalars().all()
    return [_operator_to_dict(op) for op in ops]


def get_operator(name: str) -> dict[str, Any] | None:
    """按名称获取单个算子。"""
    db = next(get_db())
    op = db.execute(select(Operator).where(Operator.name == name)).scalar_one_or_none()
    return _operator_to_dict(op) if op else None


def operator_names() -> set[str]:
    """全部算子名(用于存在性校验)。"""
    db = next(get_db())
    names = db.execute(select(Operator.name)).scalars().all()
    return set(names)


def catalog_meta() -> dict[str, Any]:
    """目录概览(总数/各维度分布/推荐数)。"""
    db = next(get_db())
    total = db.scalar(select(func.count()).select_from(Operator))

    # 按类别统计
    by_category = {}
    rows = db.execute(
        select(Operator.category, func.count())
        .group_by(Operator.category)
    ).all()
    for cat, cnt in rows:
        by_category[cat] = cnt

    # 按场景统计
    by_scenario = {}
    rows = db.execute(
        select(Operator.scenario_group, func.count())
        .group_by(Operator.scenario_group)
    ).all()
    for sc, cnt in rows:
        if sc:
            by_scenario[sc] = cnt

    # 推荐数
    recommend_count = db.scalar(
        select(func.count()).select_from(Operator).where(Operator.recommend == True)
    )

    return {
        "total": total,
        "by_category": by_category,
        "by_scenario": by_scenario,
        "recommend": recommend_count,
    }
# 由 data-juicer 全量算子业务归类生成(primary/secondary=蒸馏 且为 filter/dedup/selector、非多模态)。
# 含 LLM/GPU 评分类 filter——运行时按算力门 gating(UI「只看可运行」隐藏不可用项)。
# 归类见 docs/ 算子业务归纳;平台侧硬编码,data-juicer 仓无 PR。
DISTILLATION_OPS: frozenset[str] = frozenset(
    {
        "alphanumeric_filter",
        "average_line_length_filter",
        "character_repetition_filter",
        "document_deduplicator",
        "document_line_deduplicator",
        "document_minhash_deduplicator",
        "document_simhash_deduplicator",
        "flagged_words_filter",
        "frequency_specified_field_selector",
        "general_field_filter",
        "in_context_influence_filter",
        "instruction_following_difficulty_filter",
        "language_id_score_filter",
        "llm_condition_filter",
        "llm_difficulty_score_filter",
        "llm_perplexity_filter",
        "llm_quality_score_filter",
        "llm_task_relevance_filter",
        "maximum_line_length_filter",
        "perplexity_filter",
        "random_selector",
        "range_specified_field_selector",
        "ray_bts_minhash_deduplicator",
        "ray_document_deduplicator",
        "special_characters_filter",
        "specified_field_filter",
        "specified_numeric_field_filter",
        "stopwords_filter",
        "suffix_filter",
        "tags_specified_field_selector",
        "text_action_filter",
        "text_embd_similarity_filter",
        "text_entity_dependency_filter",
        "text_length_filter",
        "text_pair_similarity_filter",
        "token_num_filter",
        "topk_specified_field_selector",
        "word_repetition_filter",
        "words_num_filter",
    }
)


def is_distillation_operator(name: str) -> bool:
    """该算子是否在蒸馏白名单内(供 distillation router 校验)。"""
    return name in DISTILLATION_OPS


# 清洗桶:规则类 mapper(字符/格式/繁简/标点/空白/HTML/链接/版权/页眉/参考文献/脱敏 等),1→1 去噪规范化。
# 由 data-juicer 全量算子业务归类生成(primary/secondary=清洗 的 mapper);另保留 2 个清洗场景常用 filter。
# 归类见 docs/ 算子业务归纳;平台侧硬编码,data-juicer 仓无 PR。
CLEANSING_OPS: frozenset[str] = frozenset(
    {
        # 规则 mapper:去噪 / 规范化
        "agent_dialog_normalize_mapper",
        "chinese_convert_mapper",
        "clean_copyright_mapper",
        "clean_email_mapper",
        "clean_html_mapper",
        "clean_ip_mapper",
        "clean_links_mapper",
        "expand_macro_mapper",
        "fix_unicode_mapper",
        "latex_merge_tex_mapper",
        "pii_redaction_mapper",
        "punctuation_normalization_mapper",
        "remove_bibliography_mapper",
        "remove_comments_mapper",
        "remove_header_mapper",
        "remove_long_words_mapper",
        "remove_non_chinese_character_mapper",
        "remove_repeat_sentences_mapper",
        "remove_specific_chars_mapper",
        "remove_table_text_mapper",
        "remove_words_with_incorrect_substrings_mapper",
        "replace_content_mapper",
        "sentence_split_mapper",
        "whitespace_normalization_mapper",
        # 清洗场景常用 filter(语言识别 / 敏感词)
        "language_id_score_filter",
        "flagged_words_filter",
    }
)


# 合成 / 增强桶:LLM-based mapper(需求 #8:LLM 造数据 + 改写 + 区分原始/合成)。
# 由 data-juicer 全量算子业务归类生成(make/augment 桶的 mapper)。多数依赖 LLM,
# 未配 OPENAI_API_KEY 时按 needs_api 拦截。合成(make)=造新数据(1→N);增强(augment)=改写已有(1→1)。
# optimize_prompt / pair_preference 双用,同时在两桶。归类见 docs/;平台侧硬编码,DJ 仓无 PR。
MAKE_OPS: frozenset[str] = frozenset(
    {
        "generate_qa_from_examples_mapper",  # Self-Instruct:从种子示例生成新 QA
        "generate_qa_from_text_mapper",  # 1→N,无结构文本→QA 对
        "optimize_prompt_mapper",  # few-shot 合成/扩展 prompt(亦在增强桶)
        "pair_preference_mapper",  # 构造 DPO 偏好对(造新偏好数据)
    }
)

AUGMENT_OPS: frozenset[str] = frozenset(
    {
        "calibrate_qa_mapper",  # 事实校准 QA
        "calibrate_query_mapper",  # 校准优化 query
        "calibrate_response_mapper",  # 事实校准 response
        "llm_extract_mapper",  # 通用结构化抽取
        "nlpaug_en_mapper",  # 英文规则增强
        "nlpcda_zh_mapper",  # 中文规则增强
        "optimize_prompt_mapper",  # 优化已有 prompt(亦在合成桶)
        "optimize_qa_mapper",  # 优化 QA 对(同时优化 q+a)
        "optimize_query_mapper",  # Evol-Instruct:只优化 query
        "optimize_response_mapper",  # Evol-Instruct:只优化 response
        "pair_preference_mapper",  # DPO 偏好数据构造(亦在合成桶)
        "sentence_augmentation_mapper",  # 通用 paraphrasing/改写/扩写
        "text_tagging_by_prompt_mapper",  # LLM 文本分类打标
    }
)


def is_make_operator(name: str) -> bool:
    return name in MAKE_OPS


def is_augment_operator(name: str) -> bool:
    return name in AUGMENT_OPS


# 业务桶 → 白名单集合:供算子库按任务类型过滤(清洗/蒸馏/合成/增强各自只展示对应算子)。
# 成员可重叠(如 pair_preference / optimize_prompt 同属 make+augment),按集合成员判定而非单值归属。
_BUCKET_SETS: dict[str, frozenset[str]] = {
    "cleansing": CLEANSING_OPS,
    "distillation": DISTILLATION_OPS,
    "make": MAKE_OPS,
    "augment": AUGMENT_OPS,
}

_MAXSIZE = 9223372036854775807  # sys.maxsize:DJ 用作"无上限"的默认,表单里清空


# 蒸馏桶:filter + deduplicator + selector。蒸馏 = 过滤 + 去重 + 选择,把数据集减量成高质量子集。
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

    scenarioGroup 保留 data-juicer 原生中文场景(如 质量过滤 / 文本清洗 / 去重),
    供算子市场左侧场景菜单分组——不再覆盖为业务桶英文键。业务桶(cleansing/
    distillation/make/augment)归属由独立 ``bucket`` 查询参数 + ``_BUCKET_SETS``
    表达,与场景维度解耦。
    """
    out = {_OP_KEY_MAP.get(k, k): v for k, v in op.items()}
    # 市场/编辑器口径:只看环境能力(media_ok=True),不预判数据集格式——
    # 媒体算子按环境(GPU/LLM/...)判 ready,数据集适配留到提交期 runnable_reason。
    out["runnable"] = effective_runnable(op, media_ok=True)
    return out


def meta_api() -> dict[str, Any]:
    """目录概览 → camelCase 出参形态;``byRunnable`` 按当前环境实时重算。"""
    out = {_META_KEY_MAP.get(k, k): v for k, v in catalog_meta().items()}
    caps = get_capabilities()
    counts: dict[str, int] = {}
    for op in all_operators():
        status = effective_runnable(op, caps, media_ok=True)
        counts[status] = counts.get(status, 0) + 1
    out["byRunnable"] = counts
    return out


# ---------------------------------------------------------------------------
# 有效可运行状态:静态需求(resource_class)× 运行时能力(capabilities)
# ---------------------------------------------------------------------------
# 目录 JSON 里烤死的 ``runnable`` 是"无 GPU 环境"快照,运行时不再采信——改由
# 本函数按当前环境真实能力实时计算,守门 / 徽章 / 计数 / AI 上下文统一口径。
def effective_runnable(
    op: dict[str, Any], caps: Capabilities | None = None, *, media_ok: bool = False
) -> str:
    """算子在当前环境的有效可运行状态。

    优先级与构建期 ``runnable()`` 一致,但每条算力门改为按 ``caps`` 实时判定。
    媒体模态的处理分两处口径,由 ``media_ok`` 切换:
    - ``media_ok=False``(默认;提交期 ``runnable_reason`` 用):数据集已知,媒体算子
      在非 manifest(文本)数据集上 → needs_media,由调用方再按真实数据集类型定夺。
    - ``media_ok=True``(市场 / 编辑器浏览用):假定数据集匹配,**跳过 needs_media 分支**,
      继续按 resource_class / ray_ 前缀判环境能力——市场只回答"环境能不能跑",
      不预判数据集格式(那是提交时 _operator_block 的事)。
    """
    caps = caps or get_capabilities()
    res = op["resource_class"]
    mod = set(op.get("modality") or [])
    name = op["name"]
    if mod & _MEDIA_MODALITIES and not media_ok:
        return "needs_media"
    if res == "api_llm":
        return "ready" if caps.llm else "needs_api"
    if name.startswith("ray_"):
        return "ready" if caps.ray else "needs_compute"
    if res in ("gpu", "hf_model"):
        return "ready" if caps.cuda else "needs_compute"
    if res == "vllm":
        # vllm 标注的算子绝大多数同时支持 HF 本地模型(frameworks 含 "hf",
        # enable_vllm 默认 False;平台 build_config 不强制 vLLM)。故:vLLM 服务就绪
        # 即用;否则有 GPU 时退回 HF-on-GPU 跑(与 hf_model 算子同口径)。纯 vLLM
        # (无 hf 回退)才在缺 vLLM 时拦 needs_compute。
        if caps.vllm:
            return "ready"
        if caps.cuda and "hf" in (op.get("frameworks") or []):
            return "ready"
        return "needs_compute"
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
    bucket: str | None = None,
    category: str | None = None,
    modality: str | None = None,
    resource_class: str | None = None,
    runnable: str | None = None,
    recommend: bool | None = None,
    keyword: str | None = None,
    current: int = 1,
    page_size: int = 24,
) -> dict[str, Any]:
    """按多维条件过滤算子目录,返回分页数据 + 总数。

    ``bucket``:业务桶(cleansing/distillation/make/augment),供任务编辑器只展示对应算子;
    按白名单集合成员判定(见 ``_BUCKET_SETS``),未知桶名退化为不限制。
    """
    db = next(get_db())
    stmt = select(Operator)

    # 基础过滤
    if scenario:
        stmt = stmt.where(Operator.scenario_group == scenario)
    if category:
        stmt = stmt.where(Operator.category == category)
    if resource_class:
        stmt = stmt.where(Operator.resource_class == resource_class)
    if recommend is not None:
        stmt = stmt.where(Operator.recommend == recommend)

    # 关键字搜索
    if keyword:
        kw = f"%{keyword.lower()}%"
        stmt = stmt.where(
            (Operator.name.ilike(kw))
            | (Operator.summary_zh.ilike(kw))
            | (Operator.zh_label.ilike(kw))
        )

    # 获取全部结果做内存过滤（bucket、modality、runnable 需要业务逻辑）
    all_results = db.execute(stmt).scalars().all()
    bucket_set = _BUCKET_SETS.get(bucket) if bucket else None
    caps = get_capabilities()

    filtered = []
    for op in all_results:
        op_dict = _operator_to_dict(op)
        if bucket_set and op.name not in bucket_set:
            continue
        if modality and modality not in (op.modality or []):
            continue
        if runnable and effective_runnable(op_dict, caps, media_ok=True) != runnable:
            continue
        filtered.append(op_dict)

    # 分页
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
