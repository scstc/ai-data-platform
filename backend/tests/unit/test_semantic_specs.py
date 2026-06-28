"""语义类型层(L3)纯单测 —— 数据接入重构 #1/#2/#8(docs/plan/14)。

不依赖 DB/网络。锁的是需求意图:平台要**认识并区分** 10 类语义、能按标准 schema
归一别名 + 校验必填,且对未声明类型**向后兼容不改数据**(零回归)。
"""

from __future__ import annotations

import pytest

from app.services.semantic_registry import (
    SemanticType,
    SemanticValidationError,
    apply_semantic_spec,
    classify_modalities,
    collect_modalities,
    infer_semantic,
    infer_semantic_from_data_type,
    modalities_for_subtype,
    parse_semantic_type,
    semantic_type_catalog,
)


# ---------------------------------------------------------------------------
# 向后兼容:未声明 / 未知语义类型 → 不校验、不改记录
# ---------------------------------------------------------------------------
def test_none_semantic_passes_through_unchanged():
    """semantic_type=None:记录原样返回,validated=False —— 现有上传零回归。"""
    recs = [{"a": 1}, {"b": 2}]
    out, report = apply_semantic_spec(recs, None)
    assert out == recs
    assert report.validated is False
    assert report.bad_rows == 0
    assert report.total == 2


def test_unknown_semantic_passes_through_unchanged():
    """未知 semantic_type 不抛错,按兼容处理(防御历史/脏值)。"""
    recs = [{"x": 1}]
    out, report = apply_semantic_spec(recs, "not-a-type")
    assert out == recs
    assert report.validated is False


# ---------------------------------------------------------------------------
# 字段映射(别名归一):只 rename 不丢原字段
# ---------------------------------------------------------------------------
def test_qa_alias_normalization_and_keep_other_fields():
    """qa:q→question、a→answer,且保留其它字段(只 rename 不丢)。"""
    recs = [{"q": "1+1?", "a": "2", "src": "exam"}]
    out, report = apply_semantic_spec(recs, "qa")
    assert out[0]["question"] == "1+1?"
    assert out[0]["answer"] == "2"
    assert out[0]["src"] == "exam"  # 原其它字段保留
    assert "q" not in out[0] and "a" not in out[0]  # 别名已 rename
    assert report.validated is True
    assert report.bad_rows == 0


def test_alias_does_not_clobber_existing_canonical():
    """标准名已存在且非空时,别名不覆盖。"""
    recs = [{"question": "keep", "prompt": "drop"}]
    out, _ = apply_semantic_spec(recs, "qa")
    assert out[0]["question"] == "keep"


# ---------------------------------------------------------------------------
# 各类型必填校验
# ---------------------------------------------------------------------------
def test_qa_missing_answer_counts_bad_non_strict():
    """非严格:缺 answer 计 bad_rows,但不阻断(返回记录)。"""
    recs = [{"question": "q only"}]
    out, report = apply_semantic_spec(recs, "qa")
    assert len(out) == 1
    assert report.bad_rows == 1
    assert report.errors


def test_preference_requires_chosen_rejected():
    recs = [{"prompt": "p", "chosen": "c", "rejected": "r"}]
    _, ok = apply_semantic_spec(recs, "preference")
    assert ok.bad_rows == 0
    bad = [{"prompt": "p", "chosen": "c"}]  # 缺 rejected
    _, rep = apply_semantic_spec(bad, "preference")
    assert rep.bad_rows == 1


def test_cot_requires_three_fields_with_aliases():
    recs = [{"input": "q", "chain_of_thought": "step", "output": "a"}]
    out, rep = apply_semantic_spec(recs, "cot")
    assert out[0]["question"] == "q"
    assert out[0]["reasoning"] == "step"
    assert out[0]["answer"] == "a"
    assert rep.bad_rows == 0


def test_gis_range_validation():
    """gis:经纬度越界 → bad;别名 latitude/longitude 归一。"""
    good = [{"latitude": 39.9, "longitude": 116.4}]
    out, rep = apply_semantic_spec(good, "gis")
    assert out[0]["lat"] == 39.9 and out[0]["lon"] == 116.4
    assert rep.bad_rows == 0
    bad = [{"lat": 200, "lon": 0}]  # lat 越界
    _, rep2 = apply_semantic_spec(bad, "gis")
    assert rep2.bad_rows == 1


def test_timeseries_validation():
    good = [{"ts": "2026-06-17T10:00:00", "value": 1.5}]
    out, rep = apply_semantic_spec(good, "timeseries")
    assert out[0]["timestamp"] == "2026-06-17T10:00:00"
    assert rep.bad_rows == 0
    bad = [{"timestamp": "not-a-time", "value": "x"}]
    _, rep2 = apply_semantic_spec(bad, "timeseries")
    assert rep2.bad_rows == 1


def test_structured_needs_nonblank_column():
    _, rep = apply_semantic_spec([{"a": 1, "b": 2}], "structured")
    assert rep.bad_rows == 0
    _, rep2 = apply_semantic_spec([{"a": None, "b": ""}], "structured")
    assert rep2.bad_rows == 1


# ---------------------------------------------------------------------------
# 多模态 / 跨模态:modalities 归一
# ---------------------------------------------------------------------------
def test_multimodal_cross_modal_lists_all_modalities():
    """同行 image+audio+text → modalities 含三者(跨模态)。"""
    recs = [{"image": "a.png", "audio": "a.wav", "caption": "hi"}]
    out, rep = apply_semantic_spec(recs, "multimodal")
    assert out[0]["images"] == "a.png"  # image→images
    assert out[0]["audios"] == "a.wav"  # audio→audios
    assert out[0]["text"] == "hi"  # caption→text
    assert set(out[0]["modalities"]) == {"images", "audios", "text"}
    assert set(rep.modalities) == {"images", "audios", "text"}


def test_multimodal_empty_row_is_bad():
    _, rep = apply_semantic_spec([{"foo": "bar"}], "multimodal")
    assert rep.bad_rows == 1


# ---------------------------------------------------------------------------
# 严格模式:不合规整单抛错(由调用方转 422)
# ---------------------------------------------------------------------------
def test_strict_mode_raises_on_bad_rows():
    with pytest.raises(SemanticValidationError):
        apply_semantic_spec([{"question": "no answer"}], "qa", strict=True)


def test_strict_mode_ok_when_all_valid():
    out, rep = apply_semantic_spec(
        [{"question": "q", "answer": "a"}], "qa", strict=True
    )
    assert rep.bad_rows == 0 and len(out) == 1


# ---------------------------------------------------------------------------
# 推断:由记录样本 / 由 data_type
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "sample, expected",
    [
        ({"prompt": "p", "chosen": "c", "rejected": "r"}, SemanticType.PREFERENCE),
        ({"question": "q", "reasoning": "s", "answer": "a"}, SemanticType.COT),
        ({"q": "q", "a": "a"}, SemanticType.QA),
        ({"lat": 1, "lon": 2}, SemanticType.GIS),
        ({"timestamp": "t", "value": 1}, SemanticType.TIMESERIES),
        ({"images": ["a.png"]}, SemanticType.MULTIMODAL),
        ({"col1": 1, "col2": 2}, SemanticType.STRUCTURED),
        ({"text": "hi"}, SemanticType.TEXT),
    ],
)
def test_infer_semantic_from_records(sample, expected):
    assert infer_semantic([sample]) is expected


def test_infer_semantic_empty_is_none():
    assert infer_semantic([]) is None


@pytest.mark.parametrize(
    "data_type, expected",
    [
        ("sql", SemanticType.STRUCTURED),
        ("csv-tsv", SemanticType.STRUCTURED),
        ("image", SemanticType.MULTIMODAL),
        ("audio", SemanticType.MULTIMODAL),
        ("video", SemanticType.MULTIMODAL),
        ("pdf", SemanticType.UNSTRUCTURED),
        ("html", SemanticType.UNSTRUCTURED),
        ("log", SemanticType.TEXT),
        ("json", SemanticType.TEXT),
        ("unknown-key", None),
        (None, None),
    ],
)
def test_infer_semantic_from_data_type(data_type, expected):
    assert infer_semantic_from_data_type(data_type) is expected


# ---------------------------------------------------------------------------
# 写入校验 parse_semantic_type + 目录
# ---------------------------------------------------------------------------
def test_parse_semantic_type_none_passes():
    assert parse_semantic_type(None) is None
    assert parse_semantic_type("") is None


def test_parse_semantic_type_valid():
    assert parse_semantic_type("qa") is SemanticType.QA


def test_parse_semantic_type_invalid_raises():
    with pytest.raises(SemanticValidationError):
        parse_semantic_type("nonsense")


def test_catalog_covers_all_ten_types():
    cat = semantic_type_catalog()
    assert {c["key"] for c in cat} == {t.value for t in SemanticType}
    qa = next(c for c in cat if c["key"] == "qa")
    assert qa["required"] == ["question", "answer"]
    mm = next(c for c in cat if c["key"] == "multimodal")
    assert mm["mediaFields"] == ["images", "audios", "videos"]


# ---------------------------------------------------------------------------
# 多模态子分类(图片/视频/音频/跨模态):classify_modalities —— 语义 B(图文算跨模态)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "modalities, expected",
    [
        (None, None),
        ([], None),
        (["images"], "image"),  # 纯图片(无 text)→ 图片
        (["videos"], "video"),
        (["audios"], "audio"),
        (["images", "text"], "cross"),  # 图文配对 → 跨模态(语义 B,核心意图)
        (["images", "audios"], "cross"),  # 多媒体 → 跨模态
        (["images", "audios", "text"], "cross"),
        (["text"], None),  # 仅 text → 兜底无子标签
    ],
)
def test_classify_modalities(modalities, expected):
    """锁语义 B:text 计入计数 → 图文配对算跨模态,纯单媒体才算图片/视频/音频。"""
    assert classify_modalities(modalities) == expected


@pytest.mark.parametrize("subtype", ["image", "video", "audio", "cross"])
def test_modalities_for_subtype_roundtrip(subtype):
    """快速设置子类型的反写值必须能被 classify_modalities 原样还原:
    否则列表「数据类型」设了图片却显示成别的(或退化无子标签),设置即失真。"""
    assert classify_modalities(modalities_for_subtype(subtype)) == subtype


def test_collect_modalities_does_not_mutate_records():
    """collect_modalities 纯读:聚合模态但不改调用方记录(land_records 推断路径零回归)。"""
    recs = [{"image": "a.png", "caption": "hi"}, {"video": "b.mp4"}]
    snapshot = [dict(r) for r in recs]
    mods = collect_modalities(recs)
    assert set(mods) == {"images", "videos", "text"}  # 别名归一后聚合
    assert recs == snapshot  # 原记录未被改动(零回归)
