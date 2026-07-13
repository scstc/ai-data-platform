"""加工算子目录:从数据库加载算子,提供查询/分面/UI 归一。

原设计:构建期生成 JSON 快照(operators_catalog.json)
新设计:算子入库,支持运行时统计、动态查询、用户自定义算子

设计见 docs/plan/04-算子市场设计.md。
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import settings
from app.models.operator import Operator
from app.services.capabilities import Capabilities, get_capabilities

_MEDIA_MODALITIES = {"image", "video", "audio", "multimodal"}

_MAXSIZE = 9223372036854775807  # sys.maxsize:DJ 用作"无上限"的默认,表单里清空


# 同步会话工厂:引擎/连接池模块级单例(远程 PG 建连慢,连接必须复用),会话每查一开
_sync_sessionmaker: sessionmaker | None = None


def _get_sync_session() -> Session:
    """创建同步会话(每次查询用完即关,连接归还池)。"""
    global _sync_sessionmaker
    if _sync_sessionmaker is None:
        sync_url = str(settings.database_url).replace("+asyncpg", "+psycopg2")
        engine = create_engine(sync_url, pool_pre_ping=True)
        _sync_sessionmaker = sessionmaker(bind=engine)
    return _sync_sessionmaker()


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
        "effect_demo": op.effect_demo,
        "detail_page": op.detail_page,
        "recommend": op.recommend,
        "runnable": op.runnable,
        "visible": op.visible,
        "usage_count": op.usage_count,
        "star_count": op.star_count,
        "is_custom": op.is_custom,
        "source_object_key": op.source_object_key,
        "created_by": op.created_by,
    }


def all_operators() -> list[dict[str, Any]]:
    """获取全部算子(含已隐藏)。每次直接落库查询——量小无需缓存,改库即时生效。"""
    session = _get_sync_session()
    try:
        ops = session.execute(select(Operator)).scalars().all()
        return [_operator_to_dict(op) for op in ops]
    finally:
        session.close()


def visible_operators() -> list[dict[str, Any]]:
    """市场/编排口径:仅 visible 算子(管理员隐藏的不展示;执行校验仍走全量)。"""
    return [op for op in all_operators() if op.get("visible", True)]


def get_operator(name: str) -> dict[str, Any] | None:
    """按名称获取单个算子(含已隐藏——已编排任务的执行/校验不受隐藏影响)。"""
    session = _get_sync_session()
    try:
        op = session.get(Operator, name)
        return _operator_to_dict(op) if op is not None else None
    finally:
        session.close()


def operator_names() -> set[str]:
    """全部算子名(用于存在性校验,含已隐藏)。"""
    session = _get_sync_session()
    try:
        return set(session.execute(select(Operator.name)).scalars().all())
    finally:
        session.close()


# DJ OPERATORS 注册表不含的类别(formatter/pipeline 不经算子注册表)
_NON_OPERATOR_CATEGORIES = frozenset({"formatter", "pipeline"})


def detect_operator_drift() -> dict[str, Any]:
    """对比 DB 算子快照与 DJ venv 真实安装的算子集(治理整改 G15)。

    DB 有 DJ 无(missingInDj):删/改名算子——守门校验会放行实际不存在的算子(危险)。
    DJ 有快照无(newInDj):DJ 升级后新增、快照未收录。
    DJ venv 不可探测 → status='unavailable'(降级,不误报漂移)。
    """
    from app.services.capabilities import probe_dj_operator_names

    dj = probe_dj_operator_names()
    if dj is None:
        return {
            "status": "unavailable",
            "reason": "DJ venv 不可探测(本环境未装 data-juicer)",
            "snapshotTotal": len(all_operators()),
        }
    db_names = {
        op["name"]
        for op in all_operators()
        if op["category"] not in _NON_OPERATOR_CATEGORIES
    }
    missing_in_dj = sorted(db_names - dj)
    new_in_dj = sorted(dj - db_names)
    return {
        "status": "ok" if not (missing_in_dj or new_in_dj) else "drift",
        "snapshotTotal": len(db_names),
        "djTotal": len(dj),
        "missingInDj": missing_in_dj,
        "newInDj": new_in_dj,
    }


def catalog_meta() -> dict[str, Any]:
    """目录概览(总数/各维度分布/推荐数),仅统计可见算子。"""
    ops = visible_operators()
    total = len(ops)

    # 按类别统计
    by_category = {}
    for op in ops:
        cat = op["category"]
        by_category[cat] = by_category.get(cat, 0) + 1

    # 按场景统计
    by_scenario = {}
    for op in ops:
        sc = op.get("scenario_group")
        if sc:
            by_scenario[sc] = by_scenario.get(sc, 0) + 1

    # 推荐数
    recommend_count = sum(1 for op in ops if op.get("recommend", False))

    return {
        "total": total,
        "by_category": by_category,
        "by_scenario": by_scenario,
        "recommend": recommend_count,
    }
# 由 data-juicer 全量算子业务归类生成
# (primary/secondary=蒸馏 且为 filter/dedup/selector、非多模态)。
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


# 清洗桶:规则类 mapper(字符/格式/繁简/标点/空白/HTML/链接/
# 版权/页眉/参考文献/脱敏 等),1→1 去噪规范化。
# 由 data-juicer 全量算子业务归类生成(primary/secondary=清洗 的 mapper);
# 另保留 2 个清洗场景常用 filter。
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
# 未配 OPENAI_API_KEY 时按 needs_api 拦截。合成(make)=造新数据(1→N);
# 增强(augment)=改写已有(1→1)。
# optimize_prompt / pair_preference 双用,同时在两桶。归类见 docs/;
# 平台侧硬编码,DJ 仓无 PR。
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


TRAINSET_OPS: frozenset[str] = frozenset(
    {
        "generate_qa_from_text_mapper",  # 1→N,无结构文本→QA 对
        "generate_qa_from_examples_mapper",  # Self-Instruct:从种子示例生成新 QA
        "pair_preference_mapper",  # 构造 DPO 偏好对
        "generate_cot_mapper",  # API 型 CoT 推理链生成(平台自定义算子)
        "generate_sft_mapper",  # API 型 SFT 三元组生成(平台自定义算子)
        "generate_qa_from_text_api_mapper",  # API 型文本生成 QA 对(平台自定义算子)
    }
)


# 质量评估桶:全量 filter 类算子(质量评估只跑 dj-analyze,统计类指标只对 filter 生效)。
# 由 catalog 中 category=='filter' 的全量算子固化,DJ 新增 filter 时需同步追加;
# 可由 scripts/build_operator_catalog.py 重生成后核对。LLM/GPU/媒体类 filter
# 仍走运行时算力门 gating,不在此处单独硬编码。
QUALITY_OPS: frozenset[str] = frozenset(
    {
        "alphanumeric_filter",
        "audio_duration_filter",
        "audio_nmf_snr_filter",
        "audio_size_filter",
        "average_line_length_filter",
        "character_repetition_filter",
        "flagged_words_filter",
        "general_field_filter",
        "image_aesthetics_filter",
        "image_aspect_ratio_filter",
        "image_face_count_filter",
        "image_face_ratio_filter",
        "image_nsfw_filter",
        "image_pair_similarity_filter",
        "image_shape_filter",
        "image_size_filter",
        "image_subplot_filter",
        "image_text_matching_filter",
        "image_text_similarity_filter",
        "image_watermark_filter",
        "in_context_influence_filter",
        "instruction_following_difficulty_filter",
        "language_id_score_filter",
        "llm_analysis_filter",
        "llm_condition_filter",
        "llm_difficulty_score_filter",
        "llm_perplexity_filter",
        "llm_quality_score_filter",
        "llm_task_relevance_filter",
        "maximum_line_length_filter",
        "perplexity_filter",
        "phrase_grounding_recall_filter",
        "special_characters_filter",
        "specified_field_filter",
        "specified_numeric_field_filter",
        "stopwords_filter",
        "suffix_filter",
        "text_action_filter",
        "text_embd_similarity_filter",
        "text_entity_dependency_filter",
        "text_length_filter",
        "text_pair_similarity_filter",
        "token_num_filter",
        "video_aesthetics_filter",
        "video_aspect_ratio_filter",
        "video_duration_filter",
        "video_frames_text_similarity_filter",
        "video_motion_score_filter",
        "video_motion_score_ptlflow_filter",
        "video_motion_score_raft_filter",
        "video_nsfw_filter",
        "video_ocr_area_ratio_filter",
        "video_resolution_filter",
        "video_tagging_from_frames_filter",
        "video_watermark_filter",
        "word_repetition_filter",
        "words_num_filter",
    }
)


def is_make_operator(name: str) -> bool:
    return name in MAKE_OPS


def is_augment_operator(name: str) -> bool:
    return name in AUGMENT_OPS


def is_trainset_operator(name: str) -> bool:
    return name in TRAINSET_OPS


# 业务桶 → 白名单集合:供算子库按任务类型过滤(清洗/蒸馏/合成/增强各自只展示对应算子)。
# 成员可重叠(如 pair_preference / optimize_prompt 同属 make+augment),
# 按集合成员判定而非单值归属。
_BUCKET_SETS: dict[str, frozenset[str]] = {
    "cleansing": CLEANSING_OPS,
    "distillation": DISTILLATION_OPS,
    "make": MAKE_OPS,
    "augment": AUGMENT_OPS,
    "trainset": TRAINSET_OPS,
    "quality": QUALITY_OPS,
}


# 业务桶的中文展示名(给算子市场 / 编辑器算子库当搜索别名用)。
# 同一算子可属多桶(例如 language_id_score_filter 同时在 cleansing/distillation),最终
# 搜索 hay 里会把命中的桶别名全部塞进去,搜「评估/质量评估」就能找到 quality 桶、
# 搜「蒸馏」就能找到 distillation 桶。改这里不动白名单,只影响搜索可命中词。
_BUCKET_LABELS: dict[str, str] = {
    "cleansing": "数据清洗",
    "distillation": "数据蒸馏",
    "make": "数据合成",
    "augment": "数据增强",
    "trainset": "训练集生成",
    "quality": "质量评估",
}


def _bucket_labels_for(name: str) -> str:
    """返回该算子所在所有业务桶的中文名(以空格连接),无桶归属则返回空串。

    用于把"业务桶的中文叫法"作为额外搜索词挂到 hay stack,让用户在算子库里搜
    「评估/质量评估」就能找到 quality 桶的 filter 算子,搜「蒸馏」找到 distillation 桶。
    """
    return " ".join(
        _BUCKET_LABELS[b] for b, s in _BUCKET_SETS.items() if name in s
    )

_MAXSIZE = 9223372036854775807  # sys.maxsize:DJ 用作"无上限"的默认,表单里清空


# 蒸馏桶:filter + deduplicator + selector。蒸馏 = 过滤 + 去重 + 选择,
# 把数据集减量成高质量子集。
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
    "effect_demo": "effectDemo",
    "is_custom": "isCustom",
    "created_by": "createdBy",
    "star_count": "starCount",
}
_META_KEY_MAP = {
    "with_detail_page": "withDetailPage",
    "by_category": "byCategory",
    "by_resource_class": "byResourceClass",
    "by_modality": "byModality",
    "by_scenario": "byScenario",
    "by_runnable": "byRunnable",
}


# 详情页展示用补充字段:
# - usageMode 使用方式:data-juicer 算子均为离线批处理,固定"离线"(随接口下发,非 DB 列)
# - tags 标签:由 scenarioGroup + 类别派生(我们无语义标签源,best-effort;随接口下发)
# - effectDemo 效果展示:处理前/后样例,存 DB effect_demo 列(LLM 批量生成);
#   无则为空,前端隐藏该块
_CATEGORY_LABEL = {
    "mapper": "数据编辑",
    "filter": "规则过滤",
    "deduplicator": "去重",
    "selector": "数据选择",
    "formatter": "格式转换",
    "grouper": "分组",
    "aggregator": "聚合",
}


def _derive_tags(op: dict[str, Any]) -> list[str]:
    """标签:场景分组 + 类别中文 + 自定义标记,去空去重(保序)。"""
    cands = [
        "自定义算子" if op.get("is_custom") else None,
        op.get("scenario_group"),
        _CATEGORY_LABEL.get(op.get("category", "")),
    ]
    seen: dict[str, None] = {}
    for t in cands:
        if t:
            seen.setdefault(t, None)
    return list(seen)


def to_api(op: dict[str, Any]) -> dict[str, Any]:
    """单个算子 → camelCase 出参形态;``runnable`` 用运行时有效状态覆盖。

    scenarioGroup 保留 data-juicer 原生中文场景(如 质量过滤 / 文本清洗 / 去重),
    供算子市场左侧场景菜单分组——不再覆盖为业务桶英文键。业务桶(cleansing/
    distillation/make/augment/trainset/quality)归属由独立 ``bucket`` 查询参数 +
    ``_BUCKET_SETS`` 表达,与场景维度解耦。
    """
    out = {_OP_KEY_MAP.get(k, k): v for k, v in op.items() if k != "source_object_key"}
    # 参数内层键 camelCase:desc_zh(全量中文翻译,快照富化)→ descZh
    if out.get("params"):
        out["params"] = [
            {("descZh" if k == "desc_zh" else k): v for k, v in p.items()}
            for p in out["params"]
        ]
    # 市场/编辑器口径:只看环境能力(media_ok=True),不预判数据集格式——
    # 媒体算子按环境(GPU/LLM/...)判 ready,数据集适配留到提交期 runnable_reason。
    out["runnable"] = effective_runnable(op, media_ok=True)
    # 详情页补充字段(见上)
    out["usageMode"] = "离线"
    out["tags"] = _derive_tags(op)
    out["effectDemo"] = out.get("effectDemo") or []
    return out


def meta_api() -> dict[str, Any]:
    """目录概览 → camelCase 出参形态;``byRunnable`` 按当前环境实时重算。"""
    out = {_META_KEY_MAP.get(k, k): v for k, v in catalog_meta().items()}
    caps = get_capabilities()
    counts: dict[str, int] = {}
    for op in visible_operators():
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
        return (
            f"算子 {name} 需要 LLM API:请在运维监控 → LLM 配置页"
            "配置并测试通过(无需激活)"
        )
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


def _ui_field(param: Any) -> dict[str, Any] | None:
    """把 DJ 参数表的一行转成前端表单字段;无意义的 args/kwargs 跳过。

    容忍 ``param`` 为 None / 非 dict(数据库列允许 params 为 None 时,
    list 里偶有混入空元素,迭代 None 会炸)。
    """
    if not isinstance(param, dict):
        return None
    name = param.get("name")
    if not name or name in ("args", "kwargs"):
        return None
    type_str = param.get("type") or ""
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
        "default": _parse_default(type_str, param.get("default") or ""),
        "desc": param.get("desc") or "",
    }


def _ui_params(op: dict[str, Any]) -> list[dict[str, Any]]:
    """算子参数 → 前端表单字段;``op['params']`` 允许为 None(数据库列 nullable)。"""
    if op.get("name") in _CURATED_PARAMS:
        return _CURATED_PARAMS[op["name"]]
    # 注意:key 存在但 value=None 时 dict.get 的默认不生效,必须显式 `or []`
    return [f for f in (_ui_field(p) for p in (op.get("params") or [])) if f]


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
    for op in visible_operators():
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
    include_hidden: bool = False,
    current: int = 1,
    page_size: int = 24,
) -> dict[str, Any]:
    """按多维条件过滤算子目录,返回分页数据 + 总数。

    ``bucket``:业务桶(cleansing/distillation/make/augment/trainset/quality),
    供任务编辑器只展示对应算子;按白名单集合成员判定(见 ``_BUCKET_SETS``),
    未知桶名退化为不限制。
    ``include_hidden``:纳入已隐藏算子(市场管理视图用);默认只出可见算子。
    """
    ops = all_operators() if include_hidden else visible_operators()
    bucket_set = _BUCKET_SETS.get(bucket) if bucket else None
    caps = get_capabilities()
    kw = keyword.lower().strip() if keyword else None

    filtered = []
    for op in ops:
        # Bucket 过滤
        if bucket_set and op["name"] not in bucket_set:
            continue
        # 基础过滤
        if scenario and op.get("scenario_group") != scenario:
            continue
        if category and op["category"] != category:
            continue
        if resource_class and op["resource_class"] != resource_class:
            continue
        if recommend is not None and op.get("recommend", False) != recommend:
            continue
        # Modality 过滤
        if modality and modality not in (op.get("modality") or []):
            continue
        # Runnable 过滤
        if runnable and effective_runnable(op, caps, media_ok=True) != runnable:
            continue
        # 关键字搜索:hay = 英文名 + 中文标签 + 中文摘要 + 场景分组 + 业务桶别名
        # 加 scenarioGroup 是为了支持"质量过滤/视频处理"这类 DJ 原生场景词;
        # 加业务桶中文名是为了支持"评估/蒸馏/清洗"这类平台业务叫法(同一算子可
        # 属多桶 → 多别名一并塞进去,大小写无关)。别名来源见 _BUCKET_LABELS。
        if kw:
            hay = (
                op["name"]
                + (op.get("summary_zh") or "")
                + (op.get("zh_label") or "")
                + (op.get("scenario_group") or "")
                + _bucket_labels_for(op["name"])
            ).lower()
            if kw not in hay:
                continue
        filtered.append(op)

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
    for op in visible_operators():
        if effective_runnable(op, caps) != "ready":
            continue
        if category and op["category"] != category:
            continue
        ctx.append(
            {
                "name": op["name"],
                "label": op.get("zh_label") or op["name"],
                "scenario": op.get("scenario_group") or "",
                # 自定义算子 params 列可为 None,必须显式 `or []`(同 _ui_params)
                "params": [p["name"] for p in (op.get("params") or [])],
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
