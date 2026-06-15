"""sanitize_pipeline:只保留存在且 runnable==ready 的算子,并裁掉非法参数键。"""

from __future__ import annotations

from app.services import operator_catalog as oc


def test_sanitize_drops_unknown_and_non_ready() -> None:
    steps = [
        {"name": "__nope__", "params": {}},          # 不存在 → 丢弃
        {"name": "document_deduplicator", "params": {}},  # ready → 保留
    ]
    out = oc.sanitize_pipeline(steps)
    names = [s["name"] for s in out]
    assert "__nope__" not in names
    assert "document_deduplicator" in names


def test_sanitize_strips_invalid_param_keys() -> None:
    # 非法键 BOGUS 与 args/kwargs 变长占位项都应被裁掉,只留合法的 min_len
    steps = [
        {
            "name": "text_length_filter",
            "params": {"min_len": 10, "BOGUS": 1, "args": [], "kwargs": {}},
        }
    ]
    out = oc.sanitize_pipeline(steps)
    assert out and out[0]["name"] == "text_length_filter"
    assert "BOGUS" not in out[0]["params"]
    # args/kwargs 不是可配置参数,放行会让 dj-process 运行期报错,必须裁掉
    assert "args" not in out[0]["params"]
    assert "kwargs" not in out[0]["params"]
    assert out[0]["params"].get("min_len") == 10


def test_ready_operator_context_only_ready() -> None:
    ctx = oc.ready_operator_context()
    assert ctx and all(c["name"] for c in ctx)
    # 上下文里出现的算子必须都是 ready(用 runnable_reason 反查:ready 时为 None)
    assert oc.runnable_reason(ctx[0]["name"]) is None
