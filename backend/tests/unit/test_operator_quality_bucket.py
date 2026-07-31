"""质量评估业务桶(quality bucket)单测。

不连 DB:通过 monkeypatch 替换 visible_operators/all_operators,
模拟 query_catalog 真实过滤逻辑,验证 bucket="quality" 只返 filter 类算子,
未知桶名 graceful degrade 为不过滤。
"""

from __future__ import annotations

import pytest

from app.services import operator_catalog
from app.services.operator_catalog import _BUCKET_SETS, QUALITY_OPS, query_catalog


@pytest.fixture
def _stub_catalog(monkeypatch: pytest.MonkeyPatch):
    """塞入 5 个跨 category 算子:1 非 filter,2 个 quality 内,2 个 quality 外。"""

    sample = [
        {
            "name": "text_length_filter",  # filter,in quality
            "category": "filter",
            "scenario_group": "质量过滤",
            "resource_class": "cpu",
            "modality": ["text"],
            "recommend": False,
            "zh_label": "文本长度过滤",
            "summary_zh": "按文本长度过滤",
        },
        {
            "name": "language_id_score_filter",  # filter,in quality
            "category": "filter",
            "scenario_group": "质量过滤",
            "resource_class": "cpu",
            "modality": ["text"],
            "recommend": False,
            "zh_label": "语种识别",
            "summary_zh": "语种分数过滤",
        },
        {
            "name": "punctuation_normalization_mapper",  # mapper,not in quality
            "category": "mapper",
            "scenario_group": "文本清洗",
            "resource_class": "cpu",
            "modality": ["text"],
            "recommend": False,
            "zh_label": "标点规范化",
            "summary_zh": "标点规范化",
        },
        {
            "name": "frequency_specified_field_selector",  # selector,not in quality
            "category": "selector",
            "scenario_group": "数据选择",
            "resource_class": "cpu",
            "modality": ["text"],
            "recommend": False,
            "zh_label": "按字段频次选择",
            "summary_zh": "频次选择",
        },
        {
            "name": "document_deduplicator",  # dedup,not in quality
            "category": "deduplicator",
            "scenario_group": "去重",
            "resource_class": "cpu",
            "modality": ["text"],
            "recommend": False,
            "zh_label": "文档去重",
            "summary_zh": "文档去重",
        },
    ]

    monkeypatch.setattr(operator_catalog, "visible_operators", lambda: sample)
    monkeypatch.setattr(operator_catalog, "all_operators", lambda: sample)
    return sample


def test_quality_bucket_registered():
    """_BUCKET_SETS 含 quality 键,值与 QUALITY_OPS 同对象。"""
    assert "quality" in _BUCKET_SETS
    assert _BUCKET_SETS["quality"] is QUALITY_OPS
    # 桶内全是 filter 类(data-juicer 约定:质量评估只对 filter 算子跑统计)
    assert len(QUALITY_OPS) >= 50  # 防御性下限;实际 57


def test_quality_bucket_filters_to_quality_ops(_stub_catalog):
    """bucket=quality 时,只返 quality 桶内算子(mapper/dedup/selector 全部剔除)。"""
    result = query_catalog(bucket="quality")
    names = {op["name"] for op in result["data"]}
    assert names == {"text_length_filter", "language_id_score_filter"}
    assert result["total"] == 2


def test_quality_bucket_with_category_filter(_stub_catalog):
    """bucket=quality + category=filter 应与单用 bucket=quality 一致。

    quality 桶本就是 filter 子集。
    """
    a = query_catalog(bucket="quality")
    b = query_catalog(bucket="quality", category="filter")
    assert {op["name"] for op in a["data"]} == {op["name"] for op in b["data"]}


def test_unknown_bucket_graceful_degrade(_stub_catalog):
    """未知桶名 → _BUCKET_SETS.get 返 None → 不过滤,返全量(向后兼容)。"""
    result = query_catalog(bucket="nonsense_bucket")
    assert result["total"] == len(_stub_catalog)


def test_no_bucket_returns_all(_stub_catalog):
    """不传 bucket → 不过滤,返全量(算子工厂默认行为)。"""
    result = query_catalog()
    assert result["total"] == len(_stub_catalog)


# ---------------------------------------------------------------------------
# 关键字搜索:hits scenarioGroup + 桶中文别名
# ---------------------------------------------------------------------------
@pytest.fixture
def _stub_search_corpus(monkeypatch: pytest.MonkeyPatch):
    """专门验证搜索 hay stack 的语料:含 scenario_group 字段、跨多桶。

    - text_length_filter:在 quality + distillation 两桶,scenario_group="质量过滤"
    - punctuation_normalization_mapper:仅在 cleansing 桶
    - document_deduplicator:仅在 distillation 桶
    """
    sample = [
        {
            "name": "text_length_filter",
            "category": "filter",
            "scenario_group": "质量过滤",
            "resource_class": "cpu",
            "modality": ["text"],
            "recommend": False,
            "zh_label": "文本长度过滤",
            "summary_zh": "按文本长度过滤",
        },
        {
            "name": "language_id_score_filter",
            "category": "filter",
            "scenario_group": "质量过滤",
            "resource_class": "cpu",
            "modality": ["text"],
            "recommend": False,
            "zh_label": "语种识别",
            "summary_zh": "语种分数过滤",
        },
        {
            "name": "punctuation_normalization_mapper",
            "category": "mapper",
            "scenario_group": "文本清洗",
            "resource_class": "cpu",
            "modality": ["text"],
            "recommend": False,
            "zh_label": "标点规范化",
            "summary_zh": "标点规范化",
        },
        {
            "name": "document_deduplicator",
            "category": "deduplicator",
            "scenario_group": "去重",
            "resource_class": "cpu",
            "modality": ["text"],
            "recommend": False,
            "zh_label": "文档去重",
            "summary_zh": "文档去重",
        },
    ]
    monkeypatch.setattr(operator_catalog, "visible_operators", lambda: sample)
    monkeypatch.setattr(operator_catalog, "all_operators", lambda: sample)
    return sample


def test_keyword_matches_bucket_label_alias(_stub_search_corpus):
    """搜「评估」→ 命中 quality 桶的中文别名,返所有 quality 桶算子。"""
    result = query_catalog(keyword="评估")
    names = {op["name"] for op in result["data"]}
    # text_length_filter 与 language_id_score_filter 都在 quality 桶
    assert "text_length_filter" in names
    assert "language_id_score_filter" in names
    # 非 quality 桶的不命中
    assert "punctuation_normalization_mapper" not in names
    assert "document_deduplicator" not in names


def test_keyword_matches_full_bucket_label(_stub_search_corpus):
    """搜「质量评估」(全名)效果同搜「评估」(子串匹配)。"""
    result = query_catalog(keyword="质量评估")
    names = {op["name"] for op in result["data"]}
    assert "text_length_filter" in names
    assert "language_id_score_filter" in names


def test_keyword_matches_scenario_group(_stub_search_corpus):
    """搜「质量过滤」→ 命中 scenarioGroup="质量过滤" 的算子。"""
    result = query_catalog(keyword="质量过滤")
    names = {op["name"] for op in result["data"]}
    assert names == {"text_length_filter", "language_id_score_filter"}


def test_keyword_matches_distillation_alias(_stub_search_corpus):
    """搜「蒸馏」→ 命中 distillation 桶算子(text_length_filter 同在两桶,仍命中)。"""
    result = query_catalog(keyword="蒸馏")
    names = {op["name"] for op in result["data"]}
    # text_length_filter 在 quality + distillation;
    # document_deduplicator 只在 distillation
    assert "text_length_filter" in names
    assert "document_deduplicator" in names
    # cleansing 桶不在 distillation 中
    assert "punctuation_normalization_mapper" not in names


def test_keyword_matches_cleansing_alias(_stub_search_corpus):
    """搜「清洗」→ cleansing 桶算子命中。"""
    result = query_catalog(keyword="清洗")
    names = {op["name"] for op in result["data"]}
    assert "punctuation_normalization_mapper" in names
    assert "text_length_filter" not in names


def test_keyword_still_matches_zh_label(_stub_search_corpus):
    """原有能力保留:搜「语种」→ zh_label 命中。"""
    result = query_catalog(keyword="语种")
    names = {op["name"] for op in result["data"]}
    assert names == {"language_id_score_filter"}


def test_keyword_still_matches_english_name(_stub_search_corpus):
    """原有能力保留:搜英文名 text_length 仍命中。"""
    result = query_catalog(keyword="text_length")
    names = {op["name"] for op in result["data"]}
    assert "text_length_filter" in names


def test_keyword_case_insensitive(_stub_search_corpus):
    """搜索大小写无关:text_length 与 TEXT_LENGTH 等价。"""
    r1 = {op["name"] for op in query_catalog(keyword="text_length")["data"]}
    r2 = {op["name"] for op in query_catalog(keyword="TEXT_LENGTH")["data"]}
    assert r1 == r2


def test_no_keyword_returns_all(_stub_search_corpus):
    """不传 keyword → 全量返回(回归原有行为)。"""
    result = query_catalog()
    assert result["total"] == len(_stub_search_corpus)
