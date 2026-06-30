"""阶段3 接线纯单测(G6/G7/G11/G18)—— 无 DB。

锁意图:build_config 按需注入 Ray/媒体键且【绝不】注入 export_stats(非法键会崩 DJ);
get_dj_version 缺 DJ venv 时安全返回 None 且缓存。
"""

from __future__ import annotations

from app.services import capabilities
from app.services.engine import build_config


def _cfg(**kw):
    return build_config(
        project_name="j",
        input_path="/a/in.jsonl",
        output_path="/a/out.jsonl",
        operators=[],
        **kw,
    )


def test_base_config_unchanged():
    c = _cfg()
    assert set(c) == {"project_name", "dataset_path", "np", "export_path", "process"}


def test_ray_keys_injected_only_when_ray():
    assert "executor_type" not in _cfg()
    assert "executor_type" not in _cfg(executor_type="default")  # default 不写
    ray = _cfg(executor_type="ray", ray_address="ray://h:1")
    assert ray["executor_type"] == "ray"
    assert ray["ray_address"] == "ray://h:1"
    # ray 但没给地址 → 默认 auto
    assert _cfg(executor_type="ray")["ray_address"] == "auto"


def test_media_keys_filtered():
    c = _cfg(media_keys={"image_key": "pics", "audio_key": None, "video_key": "vids"})
    assert c["image_key"] == "pics"
    assert c["video_key"] == "vids"
    assert "audio_key" not in c  # None 被过滤


def test_never_injects_export_stats():
    """export_stats 非 DJ 配置键,注入会崩 dj-process —— 守住绝不出现。"""
    for kw in ({}, {"executor_type": "ray"}, {"media_keys": {"image_key": "x"}}):
        assert "export_stats" not in _cfg(**kw)


def test_get_dj_version_no_python(monkeypatch):
    """DJ python 不存在 → None,不抛。"""
    from pathlib import Path

    capabilities._dj_version = capabilities._DJ_VERSION_SENTINEL
    monkeypatch.setattr(capabilities, "_dj_python", lambda: Path("/no/such/python"))
    assert capabilities.get_dj_version() is None


def test_get_dj_version_cached(monkeypatch):
    """探测一次后缓存:第二次不再跑子进程。"""
    capabilities._dj_version = capabilities._DJ_VERSION_SENTINEL
    calls = {"n": 0}

    class _FakePy:
        def exists(self):
            return True

    def _fake_run(*a, **k):
        calls["n"] += 1
        from types import SimpleNamespace

        return SimpleNamespace(returncode=0, stdout="1.2.3")

    monkeypatch.setattr(capabilities, "_dj_python", lambda: _FakePy())
    monkeypatch.setattr(capabilities.subprocess, "run", _fake_run)
    assert capabilities.get_dj_version() == "1.2.3"
    assert capabilities.get_dj_version() == "1.2.3"
    assert calls["n"] == 1  # 只跑一次
    capabilities._dj_version = capabilities._DJ_VERSION_SENTINEL  # 复位避免污染
