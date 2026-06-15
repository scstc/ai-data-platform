"""认证原语单测(无 DB):密码哈希 + 签名令牌往返与失败路径。

测试意图(为何重要):
- 口令哈希必须可逆向验证(否则种子用户永远登不上)、错误口令必须拒绝(防越权);
- 令牌签名/过期是会话安全的根:篡改或过期必须判定无效(返回 None,绝不放行)。
"""

from __future__ import annotations

import time

from app.services import auth


def test_hash_verify_roundtrip() -> None:
    """同一口令哈希后可验证通过,且存储格式为 pbkdf2$iter$salt$hash。"""
    stored = auth.hash_password("ant.design")
    assert stored.startswith("pbkdf2$200000$")
    assert stored.count("$") == 3
    assert auth.verify_password("ant.design", stored) is True


def test_verify_wrong_password_fails() -> None:
    """错误口令必须验证失败(防止凭据绕过)。"""
    stored = auth.hash_password("ant.design")
    assert auth.verify_password("wrong-pw", stored) is False


def test_verify_malformed_stored_returns_false() -> None:
    """stored 格式损坏(非 pbkdf2 四段)时安全失败,不抛异常。"""
    assert auth.verify_password("x", "not-a-valid-hash") is False
    assert auth.verify_password("x", "") is False


def test_hash_uses_random_salt() -> None:
    """两次哈希同一口令应得到不同结果(随机 salt),均可验证通过。"""
    a = auth.hash_password("same")
    b = auth.hash_password("same")
    assert a != b
    assert auth.verify_password("same", a)
    assert auth.verify_password("same", b)


def test_sign_parse_roundtrip() -> None:
    """签发的令牌可解析回原 username。"""
    token = auth.sign_token("admin")
    assert auth.parse_token(token) == "admin"


def test_parse_tampered_token_returns_none() -> None:
    """篡改签名段后验签失败 → None。"""
    token = auth.sign_token("admin")
    username, exp, _sig = token.split(".")
    tampered = f"{username}.{exp}.deadbeefdeadbeef"
    assert auth.parse_token(tampered) is None


def test_parse_tampered_username_returns_none() -> None:
    """改 username 而不更新签名 → 验签失败 → None(防止冒充)。"""
    token = auth.sign_token("user")
    _username, exp, sig = token.split(".")
    forged = f"admin.{exp}.{sig}"
    assert auth.parse_token(forged) is None


def test_parse_expired_token_returns_none() -> None:
    """已过期令牌(ttl 取负数构造)→ None。"""
    token = auth.sign_token("admin", ttl=-10)
    assert auth.parse_token(token) is None


def test_parse_garbage_returns_none() -> None:
    """非法格式字符串一律安全失败。"""
    assert auth.parse_token("garbage") is None
    assert auth.parse_token("") is None


def test_token_not_expired_within_ttl() -> None:
    """正常 ttl 内的令牌当下有效。"""
    token = auth.sign_token("admin", ttl=60)
    # 留出余量,避免边界抖动
    assert int(time.time()) < int(token.split(".")[1])
    assert auth.parse_token(token) == "admin"
