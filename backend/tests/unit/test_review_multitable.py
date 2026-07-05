"""内容安全多文件/双模式/规则库纯单测(#4 需求贴合改造)。

不依赖 DB / 网络。覆盖:
- 规则库条目(ruleWords/ruleRegex)按各自 category/severity 命中,与临时自定义并存。
- rules_to_config:review_rules 行 → config 片段转换(word/regex/未知 kind)。
- _split_action:tag 全保留;delete 切分净化行/被删行,行数守恒。
- delete 按 sampleLimit 扫描:只删已扫命中,未扫行原样结转进净化版。
- _verdicts:被审/产出版本 verdict 三态口径(含部分成员、delete 净化语义)。
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from app.services.review import rules_to_config, scan_version
from app.services.review_runner import _split_action, _verdicts


def _run(rows: list[dict[str, Any]], config: dict[str, Any]):
    return asyncio.run(scan_version(rows, config, provider=None))


# ---- 规则库条目参与扫描 ----


def test_rule_words_hit_with_own_category_severity() -> None:
    """规则库词条命中:source=keyword,category/severity 取条目自身。"""
    rows = [{"text": "内含违禁品Ω字样"}]
    findings, _tagged, report = _run(
        rows,
        {
            "useFlaggedWords": False,
            "ruleWords": [
                {"word": "违禁品Ω", "category": "drugs", "severity": "high"}
            ],
        },
    )
    assert len(findings) == 1
    assert findings[0]["source"] == "keyword"
    assert findings[0]["category"] == "drugs"
    assert findings[0]["severity"] == "high"
    assert report["bySeverity"] == {"high": 1}


def test_rule_regex_hit_and_coexists_with_custom() -> None:
    """规则库正则与临时自定义正则并存:各自命中,category/severity 互不串。"""
    rows = [{"text": "卡号 6222020200112233445 订单 ORDER-9"}]
    findings, _tagged, _report = _run(
        rows,
        {
            "useFlaggedWords": False,
            "customRegex": [{"name": "order", "pattern": r"ORDER-\d+"}],
            "ruleRegex": [
                {
                    "name": "bankcard",
                    "pattern": r"62\d{17}",
                    "category": "pii",
                    "severity": "high",
                }
            ],
        },
    )
    by_detail = {f["detail"]: f for f in findings}
    assert by_detail["order"]["category"] == "other"
    assert by_detail["order"]["severity"] == "medium"
    assert by_detail["bankcard"]["category"] == "pii"
    assert by_detail["bankcard"]["severity"] == "high"


def test_rule_words_snake_case_tolerated() -> None:
    """config 以 snake_case(rule_words)传入同样生效(与 sampleLimit 兼容口径一致)。"""
    rows = [{"text": "触发词ABC"}]
    findings, _tagged, _report = _run(
        rows,
        {
            "useFlaggedWords": False,
            "rule_words": [{"word": "触发词ABC", "category": "politics"}],
        },
    )
    assert len(findings) == 1
    assert findings[0]["category"] == "politics"


# ---- rules_to_config ----


@dataclass
class _Rule:
    name: str
    kind: str
    pattern: str
    category: str
    severity: str


def test_rules_to_config_split_word_regex_skip_unknown() -> None:
    words, regexes = rules_to_config(
        [
            _Rule("w1", "word", "敏感词", "porn", "high"),
            _Rule("r1", "regex", r"\d+", "other", "low"),
            _Rule("x", "unknown", "y", "other", "low"),
        ]
    )
    assert words == [{"word": "敏感词", "category": "porn", "severity": "high"}]
    assert regexes == [
        {"name": "r1", "pattern": r"\d+", "category": "other", "severity": "low"}
    ]


# ---- 删除模式切分 / 按样本量扫描 / verdict ----


def _tagged(flagged: bool) -> dict[str, Any]:
    return {"text": "x", "safety": {"flagged": flagged, "scanned": True}}


def test_split_action_tag_keeps_all() -> None:
    rows = [_tagged(True), _tagged(False)]
    kept, removed = _split_action(rows, "tag")
    assert kept == rows and removed == []


def test_split_action_delete_conserves_rows() -> None:
    """delete:净化行 + 被删行 = 原行数;flagged 全进 removed。"""
    rows = [_tagged(True), _tagged(False), _tagged(True), _tagged(False)]
    kept, removed = _split_action(rows, "delete")
    assert len(kept) + len(removed) == len(rows)
    assert all(not r["safety"]["flagged"] for r in kept)
    assert all(r["safety"]["flagged"] for r in removed)


def test_verdicts_matrix() -> None:
    """被审/产出 verdict 口径:命中→failed;采样或部分成员→unscanned;
    delete 已剔命中→仅全量全成员才 passed,采样/部分成员→unscanned。"""
    # 有命中,tag:双方 failed
    assert _verdicts(
        flagged_rows=1, sample_applied=False, audited_all=True, action="tag"
    ) == ("failed", "failed")
    # 有命中,delete 全量全成员:被审 failed,产出净化后 passed
    assert _verdicts(
        flagged_rows=1, sample_applied=False, audited_all=True, action="delete"
    ) == ("failed", "passed")
    # delete 但只扫了样本:产出仍 unscanned(未扫行原样留在净化版)
    assert _verdicts(
        flagged_rows=1, sample_applied=True, audited_all=True, action="delete"
    ) == ("failed", "unscanned")
    # delete 但只审了部分成员:产出 unscanned
    assert _verdicts(
        flagged_rows=0, sample_applied=False, audited_all=False, action="delete"
    ) == ("unscanned", "unscanned")
    # 零命中但采样:unscanned(不能据样本 certify 整版)
    assert _verdicts(
        flagged_rows=0, sample_applied=True, audited_all=True, action="tag"
    ) == ("unscanned", "unscanned")
    # 零命中但只审了部分成员:unscanned
    assert _verdicts(
        flagged_rows=0, sample_applied=False, audited_all=False, action="tag"
    ) == ("unscanned", "unscanned")
    # 零命中且全量全成员:passed
    assert _verdicts(
        flagged_rows=0, sample_applied=False, audited_all=True, action="tag"
    ) == ("passed", "passed")


def test_scan_delete_flow_honors_sample_limit() -> None:
    """delete 按 sampleLimit 扫描:只扫前 N 行,只删已扫命中,未扫行原样留净化版。"""
    rows = [{"text": "赌博网站"}, {"text": "正常内容"}, {"text": "赌博推广"}]
    findings, tagged, report = _run(
        rows, {"useFlaggedWords": True, "sampleLimit": 1}
    )
    assert report["sampleLimitApplied"] is True  # 只扫 1/3
    assert report["flaggedRows"] == 1
    kept, removed = _split_action(tagged, "delete")
    assert len(removed) == 1  # 仅已扫命中(row0)
    assert len(kept) == 2  # 未扫的 row1/row2 原样结转
    assert {f["rowIndex"] for f in findings} == {0}
