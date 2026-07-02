"""内容安全审核引擎(#4,自研,不走 dj-process)。

逐行扫描被审版本的文本字段,四路 source 并集成命中(findings),并把每行汇总成
safety 字段(供打标版本写盘),最后聚合成审核报告(report)。

四路:
- rules:用户 customWords(子串,大小写不敏感) + customRegex(命名正则,逐条 try,
  坏正则跳过并记 warning) + 规则库条目 ruleWords/ruleRegex(带各自 category/
  severity,见 models.review_rule) + 内置 sensitive_words.json(source=flagged_words)。
- pii:pii.detect_pii(当 config.usePii)。source=pii,category=pii。
- llm:当 config.useLlm,调 provider.moderate_texts;失败整体跳过(降级)。

sampleLimit(默认 500):只扫前 N 行,report.sampleLimitApplied=true(不静默截断);
未扫行 safety.scanned=false、flagged=false。

设计见 docs/plan/07-内容安全设计.md §3。
"""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.services import pii as pii_mod
from app.services.ai.base import AIProvider

logger = logging.getLogger(__name__)

# 默认样本上限(UI 可调)
_DEFAULT_SAMPLE_LIMIT = 500
# 命中文本片段最大长度(与 review_finding.snippet 语义一致)
_SNIPPET_MAX = 200
# 单行进入扫描的文本上限:超长字段会让 PII 正则退化为 O(n²)、自定义正则更易回溯,
# 统一截断(远大于正常文本,仍足够命中结构化 PII 与敏感词)
_MAX_SCAN_CHARS = 20_000
# 严重度优先级(取每行最高时用)
_SEVERITY_RANK = {"low": 1, "medium": 2, "high": 3}
# 上传前置审核拦截阈值:违规占比(flaggedRows/scannedRows) ≥ 此值即拦截
_BLOCK_RATIO = 0.10
# 各 source 命中的默认严重度
_KEYWORD_SEVERITY = "medium"
_FLAGGED_SEVERITY = "medium"
_REGEX_SEVERITY = "medium"
_PII_SEVERITY = "low"

_SENSITIVE_WORDS_PATH = (
    Path(__file__).resolve().parent.parent / "data" / "sensitive_words.json"
)


@lru_cache(maxsize=1)
def _load_flagged_words() -> dict[str, list[str]]:
    """加载内置敏感词表(category -> [词]),只读且进程内缓存。

    词表小且静态,lru_cache 避免每次审核重读磁盘。_meta 等非列表项跳过。
    """
    try:
        raw = json.loads(_SENSITIVE_WORDS_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:  # 词表缺失/损坏不应让审核崩
        logger.warning("内置敏感词表加载失败,flagged_words 跳过:%s", exc)
        return {}
    return {
        cat: [str(w) for w in words]
        for cat, words in raw.items()
        if isinstance(words, list)
    }


def rules_to_config(
    rules: list[Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """把 review_rules 行(或同形对象)转成 config 的 (ruleWords, ruleRegex) 片段。

    word 规则 → {word, category, severity};regex 规则 → {name, pattern, category,
    severity}。未知 kind 跳过。纯转换不查库,供建任务与上传预检共用。
    """
    words: list[dict[str, Any]] = []
    regexes: list[dict[str, Any]] = []
    for r in rules:
        entry = {"category": r.category, "severity": r.severity}
        if r.kind == "word":
            words.append({"word": r.pattern, **entry})
        elif r.kind == "regex":
            regexes.append({"name": r.name, "pattern": r.pattern, **entry})
    return words, regexes


def _pick_text(row: dict[str, Any]) -> str:
    """取行文本字段:优先 'text',否则首个 str 值;都没有返回空串。"""
    value = row.get("text")
    if isinstance(value, str):
        return value
    for val in row.values():
        if isinstance(val, str):
            return val
    return ""


def _pick_texts(
    row: dict[str, Any], fields: list[str] | None
) -> list[tuple[str | None, str]]:
    """取行待扫文本:fields 给定时逐字段取(仅字符串值参扫),否则退回默认单文本。

    返回 [(字段名, 文本)];默认路径字段名为 None(命中不落 field,与旧数据一致)。
    """
    if not fields:
        return [(None, _pick_text(row))]
    return [
        (f, val) for f in fields if isinstance((val := row.get(f)), str)
    ]


def _snippet(text: str, start: int, end: int) -> str:
    """截取命中片段(含少量上下文),长度上限 _SNIPPET_MAX。"""
    lo = max(0, start - 20)
    hi = min(len(text), end + 20)
    return text[lo:hi][:_SNIPPET_MAX]


def _compile_custom_regex(
    specs: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[str]]:
    """编译自定义/规则库正则,坏正则跳过并记 warning。

    每条 spec 可带 category/severity(规则库条目),缺省沿用旧默认
    (other / _REGEX_SEVERITY)。返回 ([{name, pattern, category, severity}], warnings)。
    """
    compiled: list[dict[str, Any]] = []
    warnings: list[str] = []
    for spec in specs:
        name = str(spec.get("name") or "regex")
        pattern = spec.get("pattern")
        if not isinstance(pattern, str) or not pattern:
            warnings.append(f"正则「{name}」缺少 pattern,已跳过")
            continue
        try:
            compiled.append(
                {
                    "name": name,
                    "pattern": re.compile(pattern),
                    "category": str(spec.get("category") or "other"),
                    "severity": str(spec.get("severity") or _REGEX_SEVERITY),
                }
            )
        except re.error as exc:
            warnings.append(f"正则「{name}」无效({exc}),已跳过")
            logger.warning("自定义正则编译失败 name=%s: %s", name, exc)
    return compiled, warnings


def _scan_rules(
    text: str,
    *,
    word_specs: list[dict[str, Any]],
    regex_specs: list[dict[str, Any]],
    flagged_words: dict[str, list[str]],
    use_flagged: bool,
) -> list[dict[str, Any]]:
    """规则路:敏感词(keyword) + 正则(regex) + 内置词表(flagged_words)。

    word_specs/regex_specs 已把「任务临时自定义」与「规则库条目」归一
    (每条带 category/severity;临时项为旧默认值)。
    """
    hits: list[dict[str, Any]] = []
    lower = text.lower()

    # 敏感词:子串、大小写不敏感
    for spec in word_specs:
        w = str(spec["word"]).strip()
        if not w:
            continue
        pos = lower.find(w.lower())
        if pos >= 0:
            hits.append(
                {
                    "source": "keyword",
                    "category": spec["category"],
                    "severity": spec["severity"],
                    "detail": w,
                    "snippet": _snippet(text, pos, pos + len(w)),
                }
            )

    # 正则:逐条 search(坏正则编译期已剔除;运行期异常再兜一层跳过该条)
    for spec in regex_specs:
        try:
            m = spec["pattern"].search(text)
        except Exception:  # noqa: BLE001 — 单条正则运行期异常不应中断整行审核
            logger.warning("自定义正则运行期异常 name=%s,跳过该条", spec["name"])
            continue
        if m:
            hits.append(
                {
                    "source": "regex",
                    "category": spec["category"],
                    "severity": spec["severity"],
                    "detail": spec["name"],
                    "snippet": _snippet(text, m.start(), m.end()),
                }
            )

    # 内置敏感词表:子串、大小写不敏感,category 取自词表
    if use_flagged:
        for category, words in flagged_words.items():
            for word in words:
                pos = lower.find(word.lower())
                if pos >= 0:
                    hits.append(
                        {
                            "source": "flagged_words",
                            "category": category,
                            "severity": _FLAGGED_SEVERITY,
                            "detail": word,
                            "snippet": _snippet(text, pos, pos + len(word)),
                        }
                    )
    return hits


def _scan_pii(text: str) -> list[dict[str, Any]]:
    """PII 路:调 pii.detect_pii,统一 source=pii、category=pii。"""
    hits: list[dict[str, Any]] = []
    for f in pii_mod.detect_pii(text):
        hits.append(
            {
                "source": "pii",
                "category": "pii",
                "severity": _PII_SEVERITY,
                "detail": f["type"],
                "snippet": f["snippet"][:_SNIPPET_MAX],
            }
        )
    return hits


def _summarize_row(hits: list[dict[str, Any]], scanned: bool) -> dict[str, Any]:
    """把一行的命中列表汇总成 safety 字段。"""
    if not scanned:
        return {
            "scanned": False,
            "flagged": False,
            "categories": [],
            "maxSeverity": None,
            "sources": [],
            "hits": [],
        }
    if not hits:
        return {
            "scanned": True,
            "flagged": False,
            "categories": [],
            "maxSeverity": None,
            "sources": [],
            "hits": [],
        }
    # 去重保序:categories / sources
    categories = list(dict.fromkeys(h["category"] for h in hits))
    sources = list(dict.fromkeys(h["source"] for h in hits))
    max_sev = max(hits, key=lambda h: _SEVERITY_RANK.get(h["severity"], 0))[
        "severity"
    ]
    return {
        "scanned": True,
        "flagged": True,
        "categories": categories,
        "maxSeverity": max_sev,
        "sources": sources,
        "hits": [
            {
                "source": h["source"],
                "category": h["category"],
                "detail": h["detail"],
                "field": h.get("field"),
            }
            for h in hits
        ],
    }


async def scan_version(
    rows: list[dict[str, Any]],
    config: dict[str, Any],
    *,
    provider: AIProvider | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """审核一批行,返回 (findings, taggedRows, report)。

    - findings:逐条命中 [{rowIndex, category, severity, source, detail,
      snippet, field}](与 ReviewFinding 字段对齐,行号为相对 rows 的下标;
      field 为命中字段名,默认扫描/LLM 行级命中为 None)。
    - taggedRows:每个 row 的浅拷贝 + safety 字段(未扫行 scanned=false)。
    - report:{totalRows, scannedRows, flaggedRows, sampleLimitApplied,
      byCategory, bySeverity, bySource, warnings}。

    config 键:customWords[], customRegex[{name,pattern}], useLlm, usePii,
    useFlaggedWords, sampleLimit, scanFields[](本批次的扫描字段列表,由上游
    按成员表解析注入;缺省 = _pick_text 默认取文本)。LLM 失败时整体跳过
    llm source(降级)。
    """
    total = len(rows)
    # 同时容忍 camelCase (sampleLimit) 与 snake_case (sample_limit):
    # 上游链路(Pydantic model_dump)是否 by_alias 决定落库形态,任一形式都应生效
    raw_limit = config.get("sampleLimit")
    if not isinstance(raw_limit, int) or raw_limit <= 0:
        raw_limit = config.get("sample_limit")
    sample_limit = (
        raw_limit
        if isinstance(raw_limit, int) and raw_limit > 0
        else _DEFAULT_SAMPLE_LIMIT
    )
    scanned_count = min(total, sample_limit)
    sample_applied = total > sample_limit

    def _cfg(camel: str, snake: str) -> list[Any]:
        """容忍 camelCase / snake_case 两形态取列表配置(同 sampleLimit 的兼容口径)。"""
        val = config.get(camel)
        if not isinstance(val, list):
            val = config.get(snake)
        return val if isinstance(val, list) else []

    # 任务临时自定义词 + 规则库词条 → 统一 word_specs(临时项用旧默认 category/severity)
    word_specs: list[dict[str, Any]] = [
        {"word": str(w), "category": "other", "severity": _KEYWORD_SEVERITY}
        for w in _cfg("customWords", "custom_words")
    ]
    for spec in _cfg("ruleWords", "rule_words"):
        if isinstance(spec, dict) and spec.get("word"):
            word_specs.append(
                {
                    "word": str(spec["word"]),
                    "category": str(spec.get("category") or "other"),
                    "severity": str(spec.get("severity") or _KEYWORD_SEVERITY),
                }
            )
    regex_specs, warnings = _compile_custom_regex(
        [
            s
            for s in (
                _cfg("customRegex", "custom_regex") + _cfg("ruleRegex", "rule_regex")
            )
            if isinstance(s, dict)
        ]
    )
    use_flagged = bool(config.get("useFlaggedWords", True))
    use_pii = bool(config.get("usePii"))
    use_llm = bool(config.get("useLlm"))
    flagged_words = _load_flagged_words() if use_flagged else {}

    # scanFields:本批次(单成员)的扫描字段列表;非 list(缺省/整 dict 形态)= 默认取文本
    raw_fields = config.get("scanFields")
    if not isinstance(raw_fields, list):
        raw_fields = config.get("scan_fields")
    scan_fields = (
        [str(f) for f in raw_fields if str(f)]
        if isinstance(raw_fields, list)
        else None
    ) or None

    # 仅扫前 scanned_count 行;其文本一次性取出供规则/PII/LLM 复用
    scan_rows = rows[:scanned_count]
    # 逐行 (字段名, 文本) 对;截断超长文本(见 _MAX_SCAN_CHARS):防拖垮正则/PII 扫描
    field_texts = [
        [(f, t[:_MAX_SCAN_CHARS]) for f, t in _pick_texts(r, scan_fields)]
        for r in scan_rows
    ]
    # LLM 行级送审文本:多字段拼成 "字段: 值" 多行;默认单文本与旧行为一致
    texts = [
        "\n".join(f"{f}: {t}" if f else t for f, t in pairs)
        for pairs in field_texts
    ]
    # 配置的字段在已扫行中从未出现(或均非文本)→ 显式告警,不静默
    if scan_fields and scan_rows:
        seen_fields = {f for pairs in field_texts for f, _ in pairs}
        warnings.extend(
            f"扫描字段「{f}」在已扫描行中不存在或非文本,已跳过"
            for f in scan_fields
            if f not in seen_fields
        )

    # LLM 路:整批 moderate;任一环节失败 → 整体跳过(降级),不影响其余 source
    llm_verdicts: list[dict[str, Any]] | None = None
    if use_llm and provider is not None and texts:
        try:
            verdicts = await provider.moderate_texts(texts)
            if len(verdicts) == len(texts):
                llm_verdicts = verdicts
            else:
                warnings.append("LLM 审核返回条数与样本不一致,已跳过 LLM 检测")
                logger.warning(
                    "moderate_texts 返回 %d 条,期望 %d 条,跳过 llm",
                    len(verdicts),
                    len(texts),
                )
        except Exception as exc:  # noqa: BLE001 — LLM 失败必须降级,绝不让审核 500
            warnings.append(f"LLM 审核不可用,已降级(其余检测正常):{exc}")
            logger.warning("moderate_texts 失败,跳过 llm source:%s", exc)

    findings: list[dict[str, Any]] = []
    tagged_rows: list[dict[str, Any]] = []
    flagged_count = 0
    by_category: dict[str, int] = {}
    by_severity: dict[str, int] = {}
    by_source: dict[str, int] = {}

    for i, row in enumerate(rows):
        if i >= scanned_count:
            # 未扫行:原样保留 + scanned=false
            tagged = dict(row)
            tagged["safety"] = _summarize_row([], scanned=False)
            tagged_rows.append(tagged)
            continue

        # 规则/PII 逐字段扫描,命中带字段名;默认路径字段名为 None
        hits: list[dict[str, Any]] = []
        for field, text in field_texts[i]:
            sub = _scan_rules(
                text,
                word_specs=word_specs,
                regex_specs=regex_specs,
                flagged_words=flagged_words,
                use_flagged=use_flagged,
            )
            if use_pii:
                sub.extend(_scan_pii(text))
            for h in sub:
                h["field"] = field
            hits.extend(sub)
        if llm_verdicts is not None:
            verdict = llm_verdicts[i]
            if verdict.get("flagged"):
                hits.append(
                    {
                        "source": "llm",
                        "category": verdict.get("category") or "other",
                        "severity": verdict.get("severity") or "medium",
                        "detail": verdict.get("reason") or "",
                        "snippet": texts[i][:_SNIPPET_MAX],
                        # LLM 是行级结论(多字段拼接送审),不归属单一字段
                        "field": None,
                    }
                )

        for h in hits:
            findings.append(
                {
                    "rowIndex": i,
                    "category": h["category"],
                    "severity": h["severity"],
                    "source": h["source"],
                    "detail": h["detail"],
                    "snippet": h["snippet"],
                    "field": h.get("field"),
                }
            )
            by_category[h["category"]] = by_category.get(h["category"], 0) + 1
            by_severity[h["severity"]] = by_severity.get(h["severity"], 0) + 1
            by_source[h["source"]] = by_source.get(h["source"], 0) + 1

        safety = _summarize_row(hits, scanned=True)
        if safety["flagged"]:
            flagged_count += 1
        tagged = dict(row)
        tagged["safety"] = safety
        tagged_rows.append(tagged)

    report = {
        "totalRows": total,
        "scannedRows": scanned_count,
        "flaggedRows": flagged_count,
        "sampleLimitApplied": sample_applied,
        "byCategory": by_category,
        "bySeverity": by_severity,
        "bySource": by_source,
        "warnings": warnings,
    }
    return findings, tagged_rows, report


async def precheck_records(
    records: list[dict[str, Any]],
    config: dict[str, Any],
    *,
    provider: AIProvider | None = None,
) -> dict[str, Any]:
    """上传前置内容安全预检:对已解析 records 跑 scan_version,按阈值判定是否放行。

    纯判定、无副作用、不碰 DB/存储,供上传 handler 在数据集落库前调用。
    拦截口径(阈值 _BLOCK_RATIO):
      - 高危命中(bySeverity.high > 0):severity=high 的违规即拦;
      - 违规占比(flaggedRows / scannedRows ≥ _BLOCK_RATIO):量大也拦。
    满足任一即 blocked=True。

    返回 {blocked, report, flaggedRows, highSeverity, ratio, findings_sample}。
    findings_sample 取前 20 条供前端展示。本判定基于采样(默认前 500 行)+ 阈值,
    通过仅代表"未触发拦截",不等同整版安全——完整结论仍由后续正式审核给出。
    """
    findings, _tagged, report = await scan_version(
        records, config, provider=provider
    )
    flagged = int(report.get("flaggedRows", 0))
    scanned = int(report.get("scannedRows", 0)) or 1
    high = int(report.get("bySeverity", {}).get("high", 0))
    ratio = flagged / scanned
    return {
        "blocked": high > 0 or ratio >= _BLOCK_RATIO,
        "report": report,
        "flaggedRows": flagged,
        "highSeverity": high,
        "ratio": round(ratio, 4),
        "findings_sample": findings[:20],
    }
