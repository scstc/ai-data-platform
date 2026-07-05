"""预置流水线模板:治理工场开箱即用的算子编排,不入库(纯代码常量)。

覆盖 scenario='clean'(规则类 mapper,参数确定)与 scenario='augmentation'
(LLM 改写 / 规则增强,goal 恒 {'mode':'augment'},无业务相关字段依赖故可预置)。
蒸馏、合成两个 LLM 场景仍不做预置——蒸馏的 goal.score_field 依赖具体打分字段、
合成的 goal 依赖 merge 布局,预置一个通用默认值反而误导用户,故留空,由用户在
工场里另存为自定义流水线。

增强预置的 LLM 算子(calibrate_qa / optimize_qa / optimize_response)模型名与端点由
engine 按平台激活的 LLM 配置自动注入(见 engine._subprocess_env / api_model 覆盖),
故预置只需固定「哪些算子 + 非模型类参数」;optimize_* 默认 is_hf_model=True(走本地
HF 权重),预置显式置 False 改走平台 API。规则增强(nlpcda_zh)不用 LLM,但增强场景
统一要求配置 LLM Key 才放行(见 augment._augment_operator_block),与其余增强算子同口径。

模板算子逐个核对自 ``operator_catalog`` 的 CLEANSING_OPS / AUGMENT_OPS(见该模块注释),
不在此白名单内的能力项一律跳过、不臆造算子名:
- 移除不可见字符 / 去表情:CLEANSING_OPS 内无专用算子,复用
  ``remove_specific_chars_mapper``(指定字符删除)传入不同 ``chars_to_remove``。
- 规范化空格:``whitespace_normalization_mapper``。
- 去乱码:``fix_unicode_mapper``(修复 Unicode 编码错误)。
- 繁转简:``chinese_convert_mapper``(``mode=t2s``)。
- 去网页标识:``clean_html_mapper``(清除 HTML 标签)。
- 去重:CLEANSING_OPS 不含去重算子(去重算子归在 DISTILLATION_OPS),故标准清洗
  模板跳过此项,另建「去重专项」模板,使用真实存在于算子目录的
  ``document_deduplicator`` / ``document_minhash_deduplicator``。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

# 预置模板无真实创建时间,统一给个固定占位(不随查询变化,便于前端缓存/排序稳定)。
_PRESET_CREATED_AT = datetime(2026, 1, 1)

# 不可见字符(零宽空格/零宽连接符/BOM/软连字符等);data-juicer 该算子按字符集合精确匹配。
_INVISIBLE_CHARS = "​‌‍‎‏﻿­"
# 常见表情符号(Emoji)样例集,非穷举;chars_to_remove 按字符匹配,足以覆盖高频场景。
_EMOJI_CHARS = "😀😁😂🤣😊😉😍😘😢😭😡👍👎❤️🔥✨🎉"

PRESET_PIPELINES: list[dict[str, Any]] = [
    {
        "id": "preset-standard-clean",
        "name": "标准文本清洗",
        "description": (
            "移除不可见字符/规范化空格/去乱码/繁转简/去网页标识/去表情,"
            "一键跑通常规文本清洗(不含去重,去重见「去重专项」模板)。"
        ),
        "scenario": "clean",
        "spec": {
            "operators": [
                {
                    "name": "remove_specific_chars_mapper",
                    "params": {"chars_to_remove": _INVISIBLE_CHARS},
                },
                {"name": "whitespace_normalization_mapper", "params": None},
                {"name": "fix_unicode_mapper", "params": {"normalization": "NFC"}},
                {"name": "chinese_convert_mapper", "params": {"mode": "t2s"}},
                {"name": "clean_html_mapper", "params": None},
                {
                    "name": "remove_specific_chars_mapper",
                    "params": {"chars_to_remove": _EMOJI_CHARS},
                },
            ],
            "goal": None,
            "text_keys": None,
        },
        "is_preset": True,
        "created_by": "system",
        "created_at": _PRESET_CREATED_AT,
        "updated_at": _PRESET_CREATED_AT,
    },
    {
        "id": "preset-dedup-clean",
        "name": "去重专项",
        "description": "精确去重 + MinHash 近似去重,产出唯一样本子集。",
        "scenario": "clean",
        "spec": {
            "operators": [
                {"name": "document_deduplicator", "params": None},
                {"name": "document_minhash_deduplicator", "params": None},
            ],
            "goal": None,
            "text_keys": None,
        },
        "is_preset": True,
        "created_by": "system",
        "created_at": _PRESET_CREATED_AT,
        "updated_at": _PRESET_CREATED_AT,
    },
    {
        "id": "preset-augment-optimize-qa",
        "name": "问答对优化",
        "description": (
            "LLM 同时优化问答对的问题与答案表述,提升清晰度、完整度与信息量"
            "(1→1 改写,不改变语义)。"
        ),
        "scenario": "augmentation",
        "spec": {
            # is_hf_model=False:改走平台 API(engine 注入 api_or_hf_model=平台模型名),
            # 否则默认走本地 HF 权重(Qwen)会触发模型下载。
            "operators": [
                {"name": "optimize_qa_mapper", "params": {"is_hf_model": False}},
            ],
            "goal": {"mode": "augment"},
            "text_keys": None,
        },
        "is_preset": True,
        "created_by": "system",
        "created_at": _PRESET_CREATED_AT,
        "updated_at": _PRESET_CREATED_AT,
    },
    {
        "id": "preset-augment-calibrate-qa",
        "name": "问答事实校准",
        "description": (
            "依据上下文对问答对做事实校准,纠正幻觉与过时表述,提升答案可信度。"
        ),
        "scenario": "augmentation",
        "spec": {
            # calibrate_* 系 API 型算子(api_model),端点/模型由 engine 按平台 LLM 注入。
            "operators": [
                {"name": "calibrate_qa_mapper", "params": None},
            ],
            "goal": {"mode": "augment"},
            "text_keys": None,
        },
        "is_preset": True,
        "created_by": "system",
        "created_at": _PRESET_CREATED_AT,
        "updated_at": _PRESET_CREATED_AT,
    },
    {
        "id": "preset-augment-nlpcda-zh",
        "name": "中文规则增强",
        "description": (
            "近义词替换 + 随机字序扰动的规则式中文增强,无需模型即可按样本扩增变体。"
        ),
        "scenario": "augmentation",
        "spec": {
            # 规则增强(不调 LLM):布尔开关默认全 False(不增强),预置显式开两种扰动。
            "operators": [
                {
                    "name": "nlpcda_zh_mapper",
                    "params": {
                        "replace_similar_word": True,
                        "swap_random_char": True,
                    },
                },
            ],
            "goal": {"mode": "augment"},
            "text_keys": None,
        },
        "is_preset": True,
        "created_by": "system",
        "created_at": _PRESET_CREATED_AT,
        "updated_at": _PRESET_CREATED_AT,
    },
]


def list_presets(scenario: str | None = None) -> list[dict[str, Any]]:
    """按 scenario 过滤预置模板(不传则返回全部);预置模板排序固定(声明顺序)。"""
    if scenario is None:
        return list(PRESET_PIPELINES)
    return [p for p in PRESET_PIPELINES if p["scenario"] == scenario]


def get_preset(pipeline_id: str) -> dict[str, Any] | None:
    """按 id 取单个预置模板;不存在返回 None。"""
    for p in PRESET_PIPELINES:
        if p["id"] == pipeline_id:
            return p
    return None
