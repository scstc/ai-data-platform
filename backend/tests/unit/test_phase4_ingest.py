"""阶段4 后端纯单测(G12 去格式 / G10 PDF识别+OCR门控 / G15 漂移检测)—— 无 DB。

锁意图:
- G12:markitdown 的 markdown 标记被去除,纯文本进 data-juicer。
- G10:扫描型识别纯函数正确;ocr_enabled=False 时扫描件仍 Fail-loud(零回归);
       ocr_enabled=True 且 OCR 返回长文本时落地成功。
- G15:DB 快照 vs DJ 算子集 diff 正确;DJ 不可探测时降级 unavailable 不误报。
"""

from __future__ import annotations

import pytest

from app.services import landing
from app.services.landing import (
    ParseError,
    _detect_pdf_type,
    _markdown_to_plain_text,
)


# --- G12:markdown 去格式 ----------------------------------------------------
def test_markdown_strips_markup():
    src = (
        "# 标题\n\nHello **bold** and [link](http://x.com).\n\n"
        "* one\n* two\n\n| A | B |\n| --- | --- |\n| 1 | 2 |"
    )
    out = _markdown_to_plain_text(src)
    assert "#" not in out
    assert "**" not in out
    assert "](http" not in out  # 链接 URL 去除
    assert "---" not in out  # 表格分隔行去除
    assert "标题" in out and "bold" in out and "link" in out
    assert "one" in out and "two" in out


def test_markdown_keeps_plain_text_intact():
    assert _markdown_to_plain_text("普通一行文本") == "普通一行文本"


# --- G10:扫描型识别 ---------------------------------------------------------
@pytest.mark.parametrize(
    "text, pages, expected",
    [
        ("", 1, True),  # 空 → 扫描型
        ("x" * 10, 1, True),  # 10 字符/页 < 50 → 扫描型
        ("x" * 500, 1, False),  # 500 字符/页 → 文本型
        ("x" * 500, 20, True),  # 25 字符/页 < 50 → 扫描型
    ],
)
def test_detect_pdf_type(text, pages, expected):
    assert _detect_pdf_type(text, pages) is expected


def _fake_markit(text: str):
    return type(
        "M", (), {"convert_stream": lambda self, *a, **k: type("R", (), {"text_content": text})()}
    )()


def test_scanned_pdf_fail_loud_when_ocr_disabled(monkeypatch):
    """ocr_enabled=False(默认):扫描件提取空 → Fail-loud(零回归)。"""
    monkeypatch.setattr(landing, "_get_markitdown", lambda: _fake_markit(""))
    monkeypatch.setattr(landing, "_pdf_page_count", lambda c: 1)
    monkeypatch.setattr(landing.settings, "ocr_enabled", False)
    with pytest.raises(ParseError) as ei:
        landing.normalize_to_records(b"%PDF fake", "pdf")
    assert "扫描" in str(ei.value) or "OCR" in str(ei.value)


def test_scanned_pdf_ocr_recovers_when_enabled(monkeypatch):
    """ocr_enabled=True 且 OCR 返回长文本 → 落地成功(用 OCR 结果)。"""
    monkeypatch.setattr(landing, "_get_markitdown", lambda: _fake_markit(""))
    monkeypatch.setattr(landing, "_pdf_page_count", lambda c: 1)
    monkeypatch.setattr(landing.settings, "ocr_enabled", True)
    monkeypatch.setattr(
        landing, "_ocr_pdf", lambda c: "这是 OCR 识别出的合同正文,内容足够长。" * 3
    )
    recs = landing.normalize_to_records(b"%PDF fake", "pdf")
    assert recs and any("OCR 识别" in r["text"] for r in recs)


def test_text_pdf_goes_through_deformat(monkeypatch):
    """文本型 PDF(字符密集):走去格式,markdown 标记被清除。"""
    monkeypatch.setattr(
        landing, "_get_markitdown", lambda: _fake_markit("# 合同\n\n**甲方**:示例公司 " + "正文" * 100)
    )
    monkeypatch.setattr(landing, "_pdf_page_count", lambda c: 1)
    monkeypatch.setattr(landing.settings, "ocr_enabled", False)
    recs = landing.normalize_to_records(b"%PDF fake", "pdf")
    joined = " ".join(r["text"] for r in recs)
    assert "#" not in joined and "**" not in joined
    assert "合同" in joined


# --- G15:漂移检测 -----------------------------------------------------------
def test_drift_detects_missing_and_new(monkeypatch):
    from app.services import operator_catalog as oc

    monkeypatch.setattr(
        oc,
        "all_operators",
        lambda: [
            {"name": "a_mapper", "category": "mapper"},
            {"name": "b_filter", "category": "filter"},
            {"name": "some_loader", "category": "formatter"},  # 非算子类,应被排除
        ],
    )
    # DJ 真实:有 a_mapper + 新增 c_mapper,缺 b_filter
    monkeypatch.setattr(
        "app.services.capabilities.probe_dj_operator_names",
        lambda: {"a_mapper", "c_mapper"},
    )
    r = oc.detect_operator_drift()
    assert r["status"] == "drift"
    assert r["missingInDj"] == ["b_filter"]  # DB 有 DJ 无
    assert r["newInDj"] == ["c_mapper"]  # DJ 有快照无
    assert "some_loader" not in r["missingInDj"]  # formatter 被排除


def test_drift_ok_when_aligned(monkeypatch):
    from app.services import operator_catalog as oc

    monkeypatch.setattr(
        oc, "all_operators", lambda: [{"name": "a_mapper", "category": "mapper"}]
    )
    monkeypatch.setattr(
        "app.services.capabilities.probe_dj_operator_names", lambda: {"a_mapper"}
    )
    assert oc.detect_operator_drift()["status"] == "ok"


def test_drift_unavailable_when_no_dj(monkeypatch):
    """DJ venv 不可探测 → unavailable(降级,不误报漂移)。"""
    from app.services import operator_catalog as oc

    monkeypatch.setattr(
        oc, "all_operators", lambda: [{"name": "a_mapper", "category": "mapper"}]
    )
    monkeypatch.setattr(
        "app.services.capabilities.probe_dj_operator_names", lambda: None
    )
    r = oc.detect_operator_drift()
    assert r["status"] == "unavailable"
    assert "snapshotTotal" in r
