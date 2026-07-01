"""UUIDv7(RFC 9562)十六进制 id 生成。

前 48 bit 是毫秒级 unix 时间戳,故字典序天然按创建时间递增——用作实体 id 的
后缀,能让对象存储(如 MinIO,前缀列举本身无时间戳)按创建顺序自然排序,
不必额外查数据库拿 createdAt 再排。
"""

from __future__ import annotations

import os
import time


def uuid7_hex() -> str:
    """生成一个 UUIDv7,返回 32 位十六进制串(不含连字符)。"""
    ts_ms = int(time.time() * 1000) & ((1 << 48) - 1)
    rand = int.from_bytes(os.urandom(10), "big")  # 74 bits 随机,够填 rand_a+rand_b
    rand_a = rand & 0xFFF  # 12 bit
    rand_b = (rand >> 12) & ((1 << 62) - 1)  # 62 bit
    value = (
        (ts_ms << 80)
        | (0x7 << 76)  # version
        | (rand_a << 64)
        | (0b10 << 62)  # variant
        | rand_b
    )
    return f"{value:032x}"
