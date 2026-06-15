"""把 data-juicer 的算子文档解析为结构化算子目录(operators_catalog.json)。

来源(只读,不改 data-juicer):
- docs/Operators.md          —— 主表:全部算子 + emoji 能力标签 + 中英一行说明
- docs/operators/<cat>/*.md   —— 每个算子详情页:完整中英描述 + 参数表 + 示例

输出: backend/app/data/operators_catalog.json
运行: python backend/scripts/build_operator_catalog.py [<data-juicer 根目录>]
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

# ---- 路径 ----
HERE = Path(__file__).resolve()
BACKEND = HERE.parent.parent
REPO = BACKEND.parent
DJ = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "data-juicer"
DOCS = DJ / "docs"
OUT = BACKEND / "app" / "data" / "operators_catalog.json"

# ---- emoji → 规范标签 ----
MODALITY = {
    "🔤": "text",
    "🏞": "image",
    "📣": "audio",
    "🎬": "video",
    "🔮": "multimodal",
}
COMPUTE = {"💻": "cpu", "🚀": "gpu"}
STABILITY = {"🔴": "alpha", "🟡": "beta", "🟢": "stable"}
FRAMEWORK = {"🔗": "api", "🌊": "vllm", "🧩": "hf"}

CJK = re.compile(r"[一-鿿]")
ROW = re.compile(r"^\|(.+)\|$")
SECTION = re.compile(r"^##\s+([a-z_]+)\s+<a name=")
INFO_LINK = re.compile(r"\[info\]\(([^)]+)\)")


def split_en_zh(text: str) -> tuple[str, str]:
    """把『英文。中文。』一行拆成 (英文, 中文):以首个汉字为界。"""
    m = CJK.search(text)
    if not m:
        return text.strip(), ""
    return text[: m.start()].strip(), text[m.start() :].strip()


def parse_tags(cell: str) -> dict:
    """emoji 能力标签 → {modality[], compute, stability, frameworks[]}。"""
    modality, compute, stability, frameworks = [], None, None, []
    for ch in cell:
        if ch in MODALITY:
            modality.append(MODALITY[ch])
        elif ch in COMPUTE:
            compute = COMPUTE[ch]
        elif ch in STABILITY:
            stability = STABILITY[ch]
        elif ch in FRAMEWORK:
            frameworks.append(FRAMEWORK[ch])
    return {
        "modality": modality,
        "compute": compute,
        "stability": stability,
        "frameworks": frameworks,
    }


def resource_class(frameworks: list[str], compute: str | None) -> str:
    """算子运行需要的『模型/算力』类别 —— 用于平台路由到合适后端。"""
    if "api" in frameworks:
        return "api_llm"          # 需要 LLM API(key+endpoint),如 GPT-4o
    if "vllm" in frameworks:
        return "vllm"             # 需要 GPU + vLLM 推理服务
    if "hf" in frameworks:
        return "hf_model"         # 需要下载 HuggingFace 模型(多数需 GPU)
    if compute == "gpu":
        return "gpu"              # 需要 GPU/CUDA(无模型,如部分视频算子)
    return "cpu"                  # 纯 CPU,无外部模型


def parse_master_table() -> dict[str, dict]:
    """解析 Operators.md 主表 → {name: {...}}(全部算子的权威清单)。"""
    ops: dict[str, dict] = {}
    cat = None
    for line in (DOCS / "Operators.md").read_text(encoding="utf-8").splitlines():
        sec = SECTION.match(line)
        if sec:
            cat = sec.group(1)
            continue
        if cat is None or not line.startswith("|"):
            continue
        m = ROW.match(line.strip())
        if not m:
            continue
        cells = [c.strip() for c in m.group(1).split("|")]
        if len(cells) < 4:
            continue
        name = cells[0]
        # 跳过表头/分隔行
        if not re.fullmatch(r"[a-z][a-z0-9_]+", name):
            continue
        tags = parse_tags(cells[1])
        en, zh = split_en_zh(cells[2])
        link = INFO_LINK.search(cells[3])
        ref = cells[4].strip() if len(cells) > 4 else "-"
        ops[name] = {
            "name": name,
            "category": cat,
            "summary_en": en,
            "summary_zh": zh,
            **tags,
            "resource_class": resource_class(tags["frameworks"], tags["compute"]),
            "detail_page": link.group(1) if link else None,
            "reference": None if ref in ("-", "") else ref,
        }
    return ops


PARAM_ROW = re.compile(r"^\|\s*`?([^|`]+)`?\s*\|(.*)\|(.*)\|(.*)\|\s*$")


def parse_detail(path: Path) -> dict:
    """解析单个算子详情页 → {desc_en, desc_zh, params[], example}。"""
    lines = path.read_text(encoding="utf-8").splitlines()
    # 1) 描述:标题之后、`Type 算子类型:` 之前的段落,按中英拆分
    for i, ln in enumerate(lines):
        if ln.startswith("# "):
            start = i + 1
            break
    else:
        start = 0
    en_parts, zh_parts = [], []
    for ln in lines[start:]:
        if ln.startswith("Type 算子类型:") or ln.startswith("## "):
            break
        s = ln.strip()
        if not s:
            continue
        (zh_parts if CJK.search(s) else en_parts).append(s)
    # 2) 参数表
    params = []
    in_params = False
    for ln in lines:
        if ln.startswith("## ") and "Parameter Configuration" in ln:
            in_params = True
            continue
        if in_params and ln.startswith("## "):
            break
        if in_params and ln.strip().startswith("|"):
            m = PARAM_ROW.match(ln.strip())
            if not m:
                continue
            pname = m.group(1).strip().strip("`").strip()
            if pname in ("name 参数名", "") or set(pname) <= {"-"}:
                continue
            ptype = m.group(2).strip()
            pdefault = m.group(3).strip().strip("`")
            pdesc = m.group(4).strip()
            params.append(
                {
                    "name": pname,
                    "type": ptype,
                    "default": pdefault,
                    "desc": pdesc,
                }
            )
    # 3) 第一个 python 示例代码块
    example = None
    for j, ln in enumerate(lines):
        if ln.strip() == "```python":
            buf = []
            for k in range(j + 1, len(lines)):
                if lines[k].strip() == "```":
                    break
                buf.append(lines[k])
            example = "\n".join(buf).strip()
            break
    return {
        "desc_en": " ".join(en_parts),
        "desc_zh": " ".join(zh_parts),
        "params": params,
        "example": example,
    }


# ---------------------------------------------------------------------------
# 语义增强(确定性,无需 LLM):场景分组 / 可运行性 / 推荐位 / 中文短标签
# ---------------------------------------------------------------------------

# 场景分组(面向"算子市场"用户侧导航);按下方 _scenario 的优先级判定
SCENARIOS = [
    "文本清洗", "中文处理", "质量过滤", "去重", "隐私脱敏",
    "文本生成与增强", "信息抽取", "对话与智能体", "图像处理", "视频处理",
    "音频处理", "多模态对齐", "数据选择", "聚合与分组", "格式转换",
    "数据集级处理", "工具与IO",
]


def _has(name: str, *subs: str) -> bool:
    return any(s in name for s in subs)


def scenario_group(op: dict) -> str:
    """把算子归入一个用户侧场景分组(确定性规则,优先级自上而下)。"""
    name, cat, mod = op["name"], op["category"], set(op["modality"] or [])
    # 1) 按类型直接定档(非 mapper/filter)
    if cat == "deduplicator":
        return "去重"
    if cat == "selector":
        return "数据选择"
    if cat in ("aggregator", "grouper"):
        return "聚合与分组"
    if cat == "formatter":
        return "格式转换"
    if cat == "pipeline":
        return "数据集级处理"
    # 2) mapper / filter:隐私/中文优先于模态
    if _has(name, "clean_email", "clean_ip", "pii", "face_blur", "redaction"):
        return "隐私脱敏"
    if _has(name, "chinese_convert", "non_chinese", "nlpcda_zh"):
        return "中文处理"
    # 3) 工具/IO
    if _has(name, "download_file", "s3_", "python_file", "python_lambda"):
        return "工具与IO"
    # 4) 对话 / 智能体
    if _has(name, "dialog_", "agent_", "query_", "usage_counter", "tool_success"):
        return "对话与智能体"
    # 5) 信息抽取
    if _has(name, "extract_", "relation_identity", "llm_extract", "tables_from_html"):
        return "信息抽取"
    # 6) 跨模态对齐(媒体↔文本的桥接:字幕/图文匹配/VLM)优先于纯媒体
    if _has(
        name, "caption", "text_matching", "text_similarity", "frames_text",
        "mllm", "_vlm", "phrase_grounding", "image_text",
    ):
        return "多模态对齐"
    # 7) 纯媒体处理:名字前缀 + 模态双重判定
    if name.startswith("video_") or "video" in mod:
        return "视频处理"
    if name.startswith("image_") or "image" in mod:
        return "图像处理"
    if name.startswith("audio_") or "audio" in mod:
        return "音频处理"
    if "multimodal" in mod:
        return "多模态对齐"
    # 8) 文本生成 / 增强
    if _has(
        name, "calibrate", "optimize", "generate_qa", "pair_preference",
        "nlpaug", "augmentation", "text_chunk", "tagging_by_prompt",
        "sentence_split", "optimize_prompt",
    ):
        return "文本生成与增强"
    # 9) 文本清洗(mapper 的清理类)
    if cat == "mapper" and _has(
        name, "clean_", "remove_", "fix_unicode", "whitespace",
        "punctuation", "replace_content", "expand_macro", "latex",
    ):
        return "文本清洗"
    # 10) filter 兜底 → 质量过滤;mapper 兜底 → 文本生成与增强
    return "质量过滤" if cat == "filter" else "文本生成与增强"


def runnable(op: dict) -> str:
    """在本平台当前引擎中的可运行状态(决定市场里的徽章与拦截)。

    ready        纯 CPU + 文本/通用 → 现有 dj-process 引擎直接可跑
    needs_media  纯 CPU 但需图像/音视频文件(受管数据集为文本 jsonl,暂不适用)
    needs_api    需 LLM API(在 .env 配 OPENAI_* 后可用,引擎已支持回退)
    needs_compute 需 GPU/HuggingFace 模型/vLLM(当前无 GPU 环境,不可执行)
    """
    res, mod = op["resource_class"], set(op["modality"] or [])
    if res == "api_llm":
        return "needs_api"
    if res in ("hf_model", "gpu", "vllm"):
        return "needs_compute"
    # Ray 分布式算子需 Ray executor;本平台用 standalone dj-process,直接跑会失败
    if op["name"].startswith("ray_"):
        return "needs_compute"
    # cpu
    if mod & {"image", "video", "audio", "multimodal"}:
        return "needs_media"
    return "ready"


# 现在就能跑、低门槛、对中文/LLM 文本语料治理普遍有用的场景
_RECOMMEND_SCENARIOS = {
    "文本清洗", "中文处理", "质量过滤", "去重", "隐私脱敏", "数据选择",
}


def recommend(op: dict) -> bool:
    """是否进入市场默认"推荐"视图。"""
    return (
        op["runnable"] == "ready"
        and op["scenario_group"] in _RECOMMEND_SCENARIOS
        and op.get("stability") in ("stable", "beta")
    )


# 常用算子的精炼中文短标签(其余回退到摘要清洗)
_LABEL_OVERRIDES = {
    "clean_html_mapper": "HTML 清洗",
    "clean_links_mapper": "链接清洗",
    "clean_email_mapper": "邮箱脱敏",
    "clean_ip_mapper": "IP 脱敏",
    "clean_copyright_mapper": "版权声明清理",
    "fix_unicode_mapper": "Unicode 修复",
    "whitespace_normalization_mapper": "空白规范化",
    "punctuation_normalization_mapper": "标点规范化",
    "chinese_convert_mapper": "中文繁简转换",
    "remove_non_chinese_character_mapper": "移除非中文字符",
    "remove_specific_chars_mapper": "移除指定字符",
    "remove_repeat_sentences_mapper": "去重复句",
    "remove_long_words_mapper": "移除超长词",
    "remove_table_text_mapper": "移除表格文本",
    "remove_words_with_incorrect_substrings_mapper": "移除异常词",
    "replace_content_mapper": "正则替换",
    "sentence_split_mapper": "分句",
    "nlpcda_zh_mapper": "中文增强",
    "nlpaug_en_mapper": "英文增强",
    "text_length_filter": "文本长度过滤",
    "words_num_filter": "词数过滤",
    "token_num_filter": "Token 数过滤",
    "character_repetition_filter": "字符重复过滤",
    "word_repetition_filter": "词重复过滤",
    "special_characters_filter": "特殊字符过滤",
    "alphanumeric_filter": "字母数字比过滤",
    "average_line_length_filter": "平均行长过滤",
    "maximum_line_length_filter": "最大行长过滤",
    "language_id_score_filter": "语种识别过滤",
    "perplexity_filter": "困惑度过滤",
    "stopwords_filter": "停用词比过滤",
    "flagged_words_filter": "敏感词比过滤",
    "document_deduplicator": "文档精确去重",
    "document_minhash_deduplicator": "MinHash 去重",
    "document_simhash_deduplicator": "SimHash 去重",
    "document_line_deduplicator": "按行去重",
    "calibrate_qa_mapper": "QA 校准",
    "optimize_qa_mapper": "QA 优化",
    "generate_qa_from_text_mapper": "文本生成 QA",
    "pair_preference_mapper": "偏好对构造",
    "extract_keyword_mapper": "关键词抽取",
    "extract_entity_relation_mapper": "实体关系抽取",
    "image_captioning_mapper": "图像描述生成",
    "random_selector": "随机抽样",
    "topk_specified_field_selector": "字段 TopK 选择",
}

_LABEL_STRIP_PREFIX = (
    "映射器", "过滤器，以", "过滤器将", "过滤器以", "过滤器", "过滤以保持",
    "过滤以", "保留", "用于", "该类用于", "类用于", "选择器",
    "使用", "根据",
)


def zh_label(op: dict) -> str:
    """4-12 字中文短标签:优先精选表,否则从摘要清洗截断。"""
    if op["name"] in _LABEL_OVERRIDES:
        return _LABEL_OVERRIDES[op["name"]]
    text = (op.get("summary_zh") or op["name"]).rstrip("。 ")
    for p in _LABEL_STRIP_PREFIX:
        if text.startswith(p):
            text = text[len(p):]
            break
    return text[:12] + ("…" if len(text) > 12 else "")


# LLM 增强叠加层(由失败工作流抢救并校验:212/212 全覆盖、命名无误、场景值均在枚举内)。
# 取舍(见 04 设计 §9):语义字段(场景/标签/用法提示)采用更准的 LLM 结果,
# 逻辑字段(runnable)仍由代码从 resource_class 推导;recommend = LLM推荐 且 当前可运行。
_OVERLAY_PATH = HERE.parent / "operator_marketplace_meta.json"


def _load_overlay() -> dict[str, dict]:
    if _OVERLAY_PATH.exists():
        rows = json.loads(_OVERLAY_PATH.read_text(encoding="utf-8"))
        return {d["name"]: d for d in rows}
    return {}


_OVERLAY = _load_overlay()


def enrich(op: dict) -> None:
    """就地补全语义字段。runnable 始终由代码推导;其余优先用 LLM 叠加层。"""
    op["runnable"] = runnable(op)
    overlay = _OVERLAY.get(op["name"])
    if overlay and overlay.get("scenario_group") in SCENARIOS:
        op["scenario_group"] = overlay["scenario_group"]
        op["zh_label"] = overlay.get("zh_label") or zh_label(op)
        op["zh_usage_tip"] = overlay.get("zh_usage_tip", "")
        # 推荐 = LLM 认为常用 且 当前可运行(推荐视图不出现跑不了的算子)
        op["recommend"] = bool(overlay.get("recommend")) and op["runnable"] == "ready"
    else:
        op["scenario_group"] = scenario_group(op)
        op["zh_label"] = zh_label(op)
        op["zh_usage_tip"] = ""
        op["recommend"] = recommend(op)


def main() -> None:
    ops = parse_master_table()
    # 合并详情页
    enriched = 0
    for op in ops.values():
        rel = op.get("detail_page")
        if not rel:
            continue
        detail_path = DOCS / rel
        if detail_path.exists():
            op.update(parse_detail(detail_path))
            enriched += 1
    # 语义增强(确定性)
    for op in ops.values():
        enrich(op)
    catalog = sorted(ops.values(), key=lambda o: (o["category"], o["name"]))
    # 分类计数
    from collections import Counter

    by_cat = Counter(o["category"] for o in catalog)
    by_res = Counter(o["resource_class"] for o in catalog)
    by_mod = Counter(m for o in catalog for m in (o["modality"] or ["(none)"]))
    by_scn = Counter(o["scenario_group"] for o in catalog)
    by_run = Counter(o["runnable"] for o in catalog)
    payload = {
        "meta": {
            "source": "data-juicer/docs/Operators.md + docs/operators/",
            "total": len(catalog),
            "with_detail_page": enriched,
            "recommended": sum(1 for o in catalog if o["recommend"]),
            "by_category": dict(sorted(by_cat.items())),
            "by_resource_class": dict(sorted(by_res.items())),
            "by_modality": dict(sorted(by_mod.items())),
            "by_scenario": dict(sorted(by_scn.items())),
            "by_runnable": dict(sorted(by_run.items())),
        },
        "operators": catalog,
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"wrote {OUT}")
    print(json.dumps(payload["meta"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
