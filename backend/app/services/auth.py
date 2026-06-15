"""认证原语:密码哈希(PBKDF2)与签名令牌(HMAC-SHA256)。

只用标准库(hashlib/hmac/secrets/base64/time),不引第三方依赖。

- 密码:存储格式 ``pbkdf2$<iter>$<salt_hex>$<hash_hex>``,验证常量时间比较。
- 令牌:``<username>.<exp_ts>.<b64url_sig>``,sig 对 ``<username>.<exp_ts>`` 签名;
  解析时校验签名(常量时间)且未过期,任何异常一律视为无效(返回 None)。

密钥统一取 ``settings.auth_secret``(env AUTH_SECRET,带 dev 默认值)。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time

from app.core.config import settings

# PBKDF2 参数(与迁移 0006 内联种子哈希保持完全一致)
_ALGO = "sha256"
_ITERATIONS = 200_000
_SALT_BYTES = 16

# 令牌默认有效期:7 天
_DEFAULT_TTL = 7 * 24 * 3600


def hash_password(pwd: str) -> str:
    """计算口令哈希,返回 ``pbkdf2$<iter>$<salt_hex>$<hash_hex>``。"""
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(_ALGO, pwd.encode("utf-8"), salt, _ITERATIONS)
    return f"pbkdf2${_ITERATIONS}${salt.hex()}${digest.hex()}"


def verify_password(pwd: str, stored: str) -> bool:
    """校验口令:从 stored 解析参数重算并常量时间比较;解析失败返回 False。"""
    try:
        scheme, iter_s, salt_hex, hash_hex = stored.split("$")
        if scheme != "pbkdf2":
            return False
        iterations = int(iter_s)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(hash_hex)
    except (ValueError, AttributeError):
        return False
    actual = hashlib.pbkdf2_hmac(_ALGO, pwd.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(actual, expected)


def _sign(message: str) -> str:
    """对 message 计算 HMAC-SHA256 签名,返回 url-safe base64(无填充)。"""
    raw = hmac.new(
        settings.auth_secret.encode("utf-8"),
        message.encode("utf-8"),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def sign_token(username: str, ttl: int = _DEFAULT_TTL) -> str:
    """签发令牌 ``<username>.<exp_ts>.<b64url_sig>``,exp_ts 为过期 unix 秒。"""
    exp_ts = int(time.time()) + ttl
    message = f"{username}.{exp_ts}"
    return f"{message}.{_sign(message)}"


def parse_token(token: str) -> str | None:
    """解析并校验令牌:签名正确(常量时间)且未过期→username,否则 None。

    任何格式/类型异常一律吞掉返回 None,绝不抛出。
    """
    try:
        username, exp_s, sig = token.split(".")
        message = f"{username}.{exp_s}"
        if not hmac.compare_digest(sig, _sign(message)):
            return None
        if int(exp_s) < int(time.time()):
            return None
        return username
    except (ValueError, AttributeError):
        return None
