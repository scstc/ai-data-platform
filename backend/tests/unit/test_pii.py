"""PII 识别纯单测(#4)。

不依赖 DB / 网络。覆盖五类结构化 PII 命中与正常文本不误报:
身份证(18 位)、手机号、邮箱、银行卡(13–19 位)、IPv4。
"""

from __future__ import annotations

from app.services.pii import detect_pii


def _types(text: str) -> set[str]:
    return {f["type"] for f in detect_pii(text)}


def test_id_card_18_digits() -> None:
    """18 位身份证(末位可为 X)命中 id_card。"""
    assert _types("我的身份证是 11010519491231002X 请保密") == {"id_card"}
    assert "id_card" in _types("证件号110105194912310021。")


def test_phone_number() -> None:
    """1[3-9] 开头 11 位手机号命中 phone。"""
    found = detect_pii("联系电话 13812345678")
    assert [f["type"] for f in found] == ["phone"]
    assert found[0]["snippet"] == "13812345678"


def test_email() -> None:
    """邮箱命中 email,片段为完整地址。"""
    found = detect_pii("邮箱 alice.bob+tag@example.co.cn 收件")
    assert [f["type"] for f in found] == ["email"]
    assert found[0]["snippet"] == "alice.bob+tag@example.co.cn"


def test_bank_card() -> None:
    """Luhn 合法的 16 位卡号命中 bank_card;非 Luhn 数字串不误报。"""
    assert _types("卡号 4111111111111111 转账") == {"bank_card"}
    # 13–19 位但非 Luhn 的数字串(如订单号)不应被判为银行卡,降低误报
    assert "bank_card" not in _types("订单号 1234567890123456")


def test_ipv4() -> None:
    """合法 IPv4 命中;越界段(如 999)不误报。"""
    assert _types("服务器 192.168.1.100 在线") == {"ipv4"}
    assert _types("版本 999.999.0.1 不是 IP") == set()


def test_clean_text_no_false_positive() -> None:
    """正常中文文本与短数字不应误报任何 PII。"""
    assert detect_pii("今天天气不错,我们去公园散步吧。") == []
    assert detect_pii("价格 199 元,数量 3 件。") == []


def test_id_card_not_double_counted_as_bank_card() -> None:
    """18 位身份证不应被 13–19 位银行卡规则重复命中(跨规则去重)。"""
    found = detect_pii("身份证 11010519491231002X")
    assert [f["type"] for f in found] == ["id_card"]


def test_multiple_pii_in_one_text() -> None:
    """一行多类 PII 全部命中,按出现位置排序。"""
    text = "张三 手机13800001111 邮箱x@y.com IP 10.0.0.1"
    types = [f["type"] for f in detect_pii(text)]
    assert types == ["phone", "email", "ipv4"]
