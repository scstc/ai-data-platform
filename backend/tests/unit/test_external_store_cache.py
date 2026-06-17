"""外部 S3 托管(#18)物化缓存纯单测。

不依赖 DB / 真实 S3:monkeypatch external_store 的 stat_object / download_to_temp
模拟一个 S3 对象,验证 cached_bytes 的行为契约:
- 同一对象(etag 不变)二次取数命中缓存,**不重复下载**;
- 源对象变化(etag 变)自动作废重拉,拿到新内容;
- 缓存关闭(max_bytes<=0)退化为按需下载,不留缓存文件;
- 总量上限触发 LRU 淘汰,缓存总字节守在上限内。

这些断言锁的是"避免重复拉取、源变即更新、可丢弃"的需求意图,不是实现细节。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from app.services import external_store

pytestmark = pytest.mark.asyncio

_CFG = {"endpoint": "http://minio.test:9000", "accessKey": "k", "secretKey": "s"}


class _FakeS3:
    """内存里的假 S3:记录每个 key 的 (内容, etag) 与下载次数。"""

    def __init__(self) -> None:
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.download_calls = 0

    def put(self, key: str, content: bytes, etag: str) -> None:
        self.objects[key] = (content, etag)

    async def stat_object(self, config, bucket, key):
        content, etag = self.objects[key]
        return {"size": len(content), "etag": etag}

    async def download_to_temp(self, config, bucket, key) -> Path:
        self.download_calls += 1
        content, _etag = self.objects[key]
        fd, name = tempfile.mkstemp(prefix="fake-s3-")
        path = Path(name)
        with open(fd, "wb") as fp:
            fp.write(content)
        return path


@pytest.fixture
def fake_s3(monkeypatch, tmp_path):
    """装好假 S3 + 把缓存目录指向 tmp_path,默认放开总量上限。"""
    s3 = _FakeS3()
    monkeypatch.setattr(external_store, "stat_object", s3.stat_object)
    monkeypatch.setattr(external_store, "download_to_temp", s3.download_to_temp)
    monkeypatch.setattr(external_store.settings, "hosted_cache_dir", str(tmp_path))
    monkeypatch.setattr(
        external_store.settings, "hosted_cache_max_bytes", 10 * 1024 * 1024
    )
    return s3


async def test_second_call_hits_cache_no_redownload(fake_s3):
    """etag 不变:二次取数命中缓存,download 只发生一次,内容一致。"""
    fake_s3.put("a.jsonl", b'{"text":"hi"}\n', etag="etag-1")

    first = await external_store.cached_bytes(_CFG, "bkt", "a.jsonl")
    second = await external_store.cached_bytes(_CFG, "bkt", "a.jsonl")

    assert first == b'{"text":"hi"}\n'
    assert second == first
    assert fake_s3.download_calls == 1  # 第二次未再下载


async def test_etag_change_refetches(fake_s3):
    """源对象变化(etag 变):缓存自动作废,重新下载并拿到新内容。"""
    fake_s3.put("a.jsonl", b"v1", etag="etag-1")
    assert await external_store.cached_bytes(_CFG, "bkt", "a.jsonl") == b"v1"
    assert fake_s3.download_calls == 1

    fake_s3.put("a.jsonl", b"v2-changed", etag="etag-2")
    assert await external_store.cached_bytes(_CFG, "bkt", "a.jsonl") == b"v2-changed"
    assert fake_s3.download_calls == 2  # etag 变 → 重拉


async def test_cache_disabled_always_downloads(fake_s3, monkeypatch, tmp_path):
    """max_bytes<=0 关闭缓存:每次都下载,且缓存目录不落任何文件。"""
    monkeypatch.setattr(external_store.settings, "hosted_cache_max_bytes", 0)
    fake_s3.put("a.jsonl", b"data", etag="etag-1")

    await external_store.cached_bytes(_CFG, "bkt", "a.jsonl")
    await external_store.cached_bytes(_CFG, "bkt", "a.jsonl")

    assert fake_s3.download_calls == 2
    assert list(tmp_path.iterdir()) == []  # 关闭时不写缓存


async def test_eviction_keeps_total_under_cap(fake_s3, monkeypatch, tmp_path):
    """总量上限触发淘汰:塞入多个对象后,缓存总字节守在上限内。"""
    # 上限设为 ~2.5 个对象大小(每对象 1000 字节),迫使淘汰
    monkeypatch.setattr(external_store.settings, "hosted_cache_max_bytes", 2500)
    payload = b"x" * 1000
    for i in range(5):
        fake_s3.put(f"obj-{i}", payload, etag=f"etag-{i}")
        await external_store.cached_bytes(_CFG, "bkt", f"obj-{i}")

    total = sum(p.stat().st_size for p in tmp_path.iterdir() if p.is_file())
    assert total <= 2500
