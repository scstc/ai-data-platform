"""审核引擎 scan_version 纯单测(#4)。

不依赖 DB / 网络(useLlm=false,不传 provider)。覆盖:
- 内置词表(flagged_words)命中对应 category/source。
- 自定义敏感词(keyword)子串、大小写不敏感命中。
- 自定义正则(regex)命中 + 坏正则不崩(记 warning)。
- PII(pii)命中。
- 文本字段优先 text,否则首个 str 值。
- sampleLimit 生效:只扫前 N 行,sampleLimitApplied=true,未扫行 scanned=false。
- 报告聚合 byCategory/bySource/bySeverity 与 taggedRows 的 safety 结构。
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.services.review import scan_version


def _run(rows: list[dict[str, Any]], config: dict[str, Any]):
    return asyncio.run(scan_version(rows, config, provider=None))


def test_flagged_words_hit_category_and_source() -> None:
    """内置词表命中:source=flagged_words,category 取自词表(如 gambling)。"""
    rows = [{"text": "这是一个赌博网站推广"}, {"text": "正常的一句话"}]
    findings, tagged, report = _run(rows, {"useFlaggedWords": True})

    hit = [f for f in findings if f["source"] == "flagged_words"]
    assert hit and hit[0]["category"] == "gambling"
    assert hit[0]["rowIndex"] == 0
    assert report["totalRows"] == 2
    assert report["scannedRows"] == 2
    assert report["flaggedRows"] == 1
    assert report["byCategory"].get("gambling") == 1
    assert report["bySource"].get("flagged_words") == 1
    # 命中行 safety
    safety = tagged[0]["safety"]
    assert safety["scanned"] is True
    assert safety["flagged"] is True
    assert "gambling" in safety["categories"]
    assert "flagged_words" in safety["sources"]
    assert safety["maxSeverity"] == "medium"
    assert safety["hits"][0]["detail"] == "赌博"
    # 未命中行
    assert tagged[1]["safety"]["flagged"] is False
    assert tagged[1]["safety"]["scanned"] is True


def test_custom_words_substring_case_insensitive() -> None:
    """自定义敏感词:子串、大小写不敏感命中,source=keyword。"""
    rows = [{"text": "包含 FooBar 字样"}]
    findings, _tagged, report = _run(
        rows, {"useFlaggedWords": False, "customWords": ["foobar"]}
    )
    assert len(findings) == 1
    assert findings[0]["source"] == "keyword"
    assert findings[0]["detail"] == "foobar"
    assert report["bySource"] == {"keyword": 1}


def test_custom_regex_hit_and_bad_regex_skipped() -> None:
    """好正则命中(source=regex);坏正则跳过且记 warning,不抛异常。"""
    rows = [{"text": "订单号 ORDER-12345"}]
    findings, _tagged, report = _run(
        rows,
        {
            "useFlaggedWords": False,
            "customRegex": [
                {"name": "order", "pattern": r"ORDER-\d+"},
                {"name": "broken", "pattern": r"([a-z"},  # 未闭合,坏正则
            ],
        },
    )
    sources = {f["source"] for f in findings}
    assert sources == {"regex"}
    assert findings[0]["detail"] == "order"
    assert any("broken" in w for w in report["warnings"])


def test_pii_detection_when_enabled() -> None:
    """usePii=true 时 PII 命中:source=pii,category=pii,detail=PII 类型。"""
    rows = [{"text": "联系 13800001111 谢谢"}]
    findings, _tagged, _report = _run(
        rows, {"useFlaggedWords": False, "usePii": True}
    )
    assert len(findings) == 1
    assert findings[0]["source"] == "pii"
    assert findings[0]["category"] == "pii"
    assert findings[0]["detail"] == "phone"


def test_text_field_fallback_to_first_str() -> None:
    """无 text 字段时取首个 str 值。"""
    rows = [{"id": 1, "content": "包含毒品交易内容", "score": 0.5}]
    findings, _tagged, _report = _run(rows, {"useFlaggedWords": True})
    assert any(f["category"] == "drugs" for f in findings)


def test_sample_limit_applied_and_unscanned_rows() -> None:
    """sampleLimit 生效:只扫前 N 行,标 sampleLimitApplied,未扫行 scanned=false。"""
    rows = [{"text": "赌博"} for _ in range(5)]
    findings, tagged, report = _run(
        rows, {"useFlaggedWords": True, "sampleLimit": 2}
    )
    assert report["totalRows"] == 5
    assert report["scannedRows"] == 2
    assert report["sampleLimitApplied"] is True
    # 命中只来自前 2 行
    assert all(f["rowIndex"] < 2 for f in findings)
    # 未扫行 scanned=false 且 flagged=false
    assert tagged[2]["safety"]["scanned"] is False
    assert tagged[2]["safety"]["flagged"] is False
    # 已扫行 scanned=true
    assert tagged[0]["safety"]["scanned"] is True


def test_no_sample_limit_scans_all() -> None:
    """行数不超过默认上限时全扫,sampleLimitApplied=false。"""
    rows = [{"text": "正常文本"} for _ in range(3)]
    _findings, _tagged, report = _run(rows, {})
    assert report["scannedRows"] == 3
    assert report["sampleLimitApplied"] is False
