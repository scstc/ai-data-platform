"""PII 模式识别(Presidio 思路的 pattern recognizer 子集,纯正则,零第三方依赖)。

覆盖结构化 PII:身份证(18 位)、手机号、邮箱、银行卡(13–19 位)、IPv4。
完整 Presidio spaCy NER(姓名/地址等非结构化)因服务器资源/外网受限延后(见设计 §6)。

正则均为有界匹配、无嵌套量词;超长输入由调用方(review.py)截断到上限,避免
超长串上的 O(n²) 退化。bank_card 命中后再做 Luhn 校验,显著降低长数字串误报。
"""

from __future__ import annotations

import re
from typing import Any

# 身份证:18 位,前 17 位数字 + 末位数字或 X;用单词边界(数字/字母)避免切到更长串
_ID_CARD = re.compile(r"(?<![0-9Xx])\d{17}[0-9Xx](?![0-9Xx])")
# 手机号:1[3-9] 开头 11 位;前后不接数字,避免从更长数字串里误截
_PHONE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
# 邮箱:常见局部规则,字符类有界,无回溯风险
_EMAIL = re.compile(
    r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}"
)
# 银行卡:13–19 位连续数字;前后不接数字
_BANK_CARD = re.compile(r"(?<!\d)\d{13,19}(?!\d)")
# IPv4:四段 0–255 的粗匹配(范围校验在匹配后做,避免回溯)
_IPV4 = re.compile(
    r"(?<![\d.])"
    r"(?:\d{1,3})\.(?:\d{1,3})\.(?:\d{1,3})\.(?:\d{1,3})"
    r"(?![\d.])"
)

# (PII 类型, 已编译正则);顺序即检测优先级,身份证先于银行卡先于手机号,
# 避免 18 位身份证被银行卡规则、11 位手机被银行卡/身份证规则重复或错判。
_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("email", _EMAIL),
    ("id_card", _ID_CARD),
    ("phone", _PHONE),
    ("bank_card", _BANK_CARD),
    ("ipv4", _IPV4),
]


def _valid_ipv4(text: str) -> bool:
    """四段均 0–255 才算合法 IPv4(正则只保证形态)。"""
    parts = text.split(".")
    return len(parts) == 4 and all(p.isdigit() and int(p) <= 255 for p in parts)


def _luhn_ok(digits: str) -> bool:
    """Luhn 校验(银行卡/信用卡);用于过滤恰好 13–19 位的非卡号数字串。"""
    total = 0
    for idx, ch in enumerate(reversed(digits)):
        d = ord(ch) - 48
        if idx % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def detect_pii(text: str) -> list[dict[str, Any]]:
    """识别文本中的结构化 PII,返回 [{type, snippet, span}]。

    - type:email | id_card | phone | bank_card | ipv4
    - snippet:命中的原文片段
    - span:[start, end) 字符偏移
    重叠去重:同一字符区间已被更高优先级规则占用则跳过(身份证不再被银行卡重计)。
    """
    if not text:
        return []
    findings: list[dict[str, Any]] = []
    occupied: list[tuple[int, int]] = []  # 已命中区间,用于跨规则去重

    for pii_type, pattern in _PATTERNS:
        for m in pattern.finditer(text):
            start, end = m.start(), m.end()
            if pii_type == "ipv4" and not _valid_ipv4(m.group()):
                continue
            if pii_type == "bank_card" and not _luhn_ok(m.group()):
                continue
            # 与已占用区间有重叠则跳过(高优先级规则先占位)
            if any(start < oe and os < end for os, oe in occupied):
                continue
            occupied.append((start, end))
            findings.append(
                {"type": pii_type, "snippet": m.group(), "span": [start, end]}
            )

    # 按出现位置排序,输出稳定
    findings.sort(key=lambda f: f["span"][0])
    return findings
