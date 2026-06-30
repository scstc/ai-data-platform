"""landing.py 治理整改纯单测(G10 / G13 / G14)—— 不依赖 DB/网络。

锁三条整改意图:
- G13:md/markdown 被受理,逐行落地为 {"text": 行}(文档把 md 列为支持格式)。
- G10:扫描型 PDF(markitdown 提取为空)不静默落 [](D2 红线 + Rule 12 Fail loud),
       抛 ParseError 让上层转明确 400。
- G14:_stamp_lineage 为每条记录注入 source_file/doc_id/ingest_batch,且不覆盖已有值。
"""

from __future__ import annotations

import pytest

from app.services import landing
from app.services.landing import (
    LANDABLE_FORMATS,
    ParseError,
    _stamp_lineage,
    normalize_to_records,
)


# --- G13:md/markdown 受理 ---------------------------------------------------
@pytest.mark.parametrize("fmt", ["md", "markdown"])
def test_md_is_landable_and_lines_to_text(fmt):
    """md/markdown 在白名单内,逐非空行 → {"text": 行}(与 txt 同分支)。"""
    assert fmt in LANDABLE_FORMATS
    content = "# 标题\n\n正文一行\n".encode()
    records = normalize_to_records(content, fmt)
    assert {"text": "# 标题"} in records
    assert {"text": "正文一行"} in records
    # 空行被跳过
    assert all(r["text"].strip() for r in records)


# --- G10:扫描型 PDF 提取为空 → Fail loud -----------------------------------
def test_scanned_pdf_empty_extract_raises(monkeypatch):
    """markitdown 对 pdf 提取为空时抛 ParseError(不静默返回 []),并点名扫描型/OCR。"""

    class _EmptyResult:
        text_content = ""

    class _FakeMarkit:
        def convert_stream(self, *a, **k):  # noqa: ANN002, ANN003
            return _EmptyResult()

    monkeypatch.setattr(landing, "_get_markitdown", lambda: _FakeMarkit())
    with pytest.raises(ParseError) as ei:
        normalize_to_records(b"%PDF-1.4 fake", "pdf")
    # 文案需指向扫描型/OCR,便于前端给出可操作提示
    assert "扫描" in str(ei.value) or "OCR" in str(ei.value)


def test_docx_empty_extract_raises(monkeypatch):
    """非 pdf 文档提取为空同样 Fail loud(通用文案),不静默落 0 行。"""

    class _EmptyResult:
        text_content = "   \n  "

    monkeypatch.setattr(
        landing, "_get_markitdown",
        lambda: type(
            "M", (), {"convert_stream": lambda self, *a, **k: _EmptyResult()}
        )(),
    )
    with pytest.raises(ParseError):
        normalize_to_records(b"fake", "docx")


# --- G14:逐条血缘注入 -------------------------------------------------------
def test_stamp_lineage_injects_fields():
    """为每条记录注入 source_file/doc_id/ingest_batch。"""
    records = [{"text": "a"}, {"text": "b"}]
    _stamp_lineage(
        records,
        source_file="年报.pdf",
        doc_id="sha256:abc",
        ingest_batch="2026-06-30T00:00:00Z",
    )
    for r in records:
        assert r["source_file"] == "年报.pdf"
        assert r["doc_id"] == "sha256:abc"
        assert r["ingest_batch"] == "2026-06-30T00:00:00Z"


def test_stamp_lineage_does_not_clobber_existing():
    """上游已注入同名字段时尊重其值(setdefault 语义),不覆盖。"""
    records = [{"text": "a", "source_file": "上游来源.csv"}]
    _stamp_lineage(
        records, source_file="覆盖.pdf", doc_id="sha256:x", ingest_batch="t"
    )
    assert records[0]["source_file"] == "上游来源.csv"  # 未被覆盖
    assert records[0]["doc_id"] == "sha256:x"  # 缺失的才补


def test_stamp_lineage_skips_non_dict():
    """非 dict 行原样跳过,不抛错。"""
    records = ["not-a-dict", {"text": "ok"}]
    out = _stamp_lineage(records, source_file="f", doc_id="d", ingest_batch="t")
    assert out[0] == "not-a-dict"
    assert out[1]["source_file"] == "f"
