"""交付/导出纯单测(G8/G9)—— 无 DB。

锁意图:dataset_card 含规范 §6.1 必备字段;分片按条数正确切;已知局限确定性推导
(eval<300 / synthetic / pdf 源 各命中对应局限),改了模板/规则就该红。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.services.export_delivery import (
    build_dataset_card,
    collect_limitations,
    shard_records,
)


def _ver(**kw) -> SimpleNamespace:
    base = dict(
        train_type="sft",
        schema_variant="messages",
        format="jsonl",
        rows=5000,
        origin="managed",
        scan_verdict="passed",
        publish_status="published",
        version_no=2,
        stats_uri="/x/data_stats.jsonl",
        quality_stats=None,
    )
    base.update(kw)
    return SimpleNamespace(**base)


def test_shard_split_respects_size():
    assert [len(s) for s in shard_records([{}] * 120, 50)] == [50, 50, 20]
    assert [len(s) for s in shard_records([{}] * 10, None)] == [10]
    assert [len(s) for s in shard_records([{}] * 10, 0)] == [10]
    assert [len(s) for s in shard_records([{}] * 30, 100)] == [30]  # 不足一片


def test_dataset_card_contains_required_fields():
    card = build_dataset_card(
        dataset_name="银行客服SFT",
        version=_ver(),
        source_lines=["反馈表 · 来源=database/postgresql"],
        operator_chain=[{"jobType": "process", "name": "text_length_filter", "params": {"min_len": 10}}],
        limitations=["示例局限"],
    )
    assert "# 银行客服SFT" in card
    assert "train_type" in card and "sft" in card
    assert "messages" in card  # schema 变体
    assert "5000" in card  # 条数
    assert "数据来源" in card and "database/postgresql" in card
    assert "治理流程" in card and "text_length_filter" in card
    assert "已知局限" in card and "示例局限" in card


def test_limitations_eval_under_300():
    lims = collect_limitations(_ver(schema_variant="eval", rows=200), set())
    assert any("300" in x for x in lims)
    # sft 5000 条不命中 <300
    lims2 = collect_limitations(_ver(), set())
    assert not any("300" in x for x in lims2)


def test_limitations_synthetic_and_pdf_source():
    lims = collect_limitations(_ver(origin="synthetic"), {"pdf"})
    assert any("合成" in x for x in lims)
    assert any("PDF" in x or "OCR" in x for x in lims)


def test_limitations_unscanned_and_no_stats():
    lims = collect_limitations(
        _ver(scan_verdict="unscanned", stats_uri=None, quality_stats=None), set()
    )
    assert any("内容安全" in x for x in lims)
    assert any("质量统计" in x for x in lims)
