"""采集质量纯函数测试(切片 B / Task 2)。

覆盖:
- compute_quality_stats: 空值率(缺 key / None / 空串均计)、类型推断
  (bool/int/float/str → boolean/integer/float/text;全空列→text)、
  列首见序、空记录、数值 0 非空。
- schema_snapshot: 由 stats 投影 name+type(丢弃 null_rate)。
- drift_diff: added/removed/type_changed + prev=None 首版无漂移。
- evaluate_policy: 4 分支(skipped / failed-空值率 / failed-漂移 / passed)
  + 边界(null_rate 恰等于阈值 → 通过,严格 > )+ 缺键容错。
"""

from __future__ import annotations

from app.services.ingest_quality import (
    compute_quality_stats,
    drift_diff,
    evaluate_policy,
    schema_snapshot,
)


# ---------------- compute_quality_stats ----------------


def test_compute_stats_empty_records():
    """空列表(零行)→ rows=0/columns=[]。"""
    assert compute_quality_stats([]) == {"rows": 0, "columns": []}


def test_compute_stats_single_empty_dict_row():
    """[{}] → rows=1/columns=[](无键即无列)。"""
    assert compute_quality_stats([{}]) == {"rows": 1, "columns": []}


def test_compute_stats_null_rate_missing_key_counts():
    """缺 key 的行计入分子(null_rate=1/2)。"""
    stats = compute_quality_stats([{"a": 1}, {}])
    assert stats["rows"] == 2
    assert stats["columns"] == [
        {"name": "a", "type": "integer", "null_rate": 0.5}
    ]


def test_compute_stats_null_rate_none_counts():
    """值为 None 计入分子(null_rate=1/2)。"""
    stats = compute_quality_stats([{"a": None}, {"a": 1}])
    assert stats["columns"][0]["null_rate"] == 0.5


def test_compute_stats_null_rate_empty_string_counts():
    """空串计入分子(仅 str 走此分支);类型仍由首非空值推断为 text。"""
    stats = compute_quality_stats([{"a": ""}, {"a": "x"}])
    assert stats["columns"][0]["null_rate"] == 0.5
    assert stats["columns"][0]["type"] == "text"


def test_compute_stats_zero_is_not_null():
    """数值 0 不是 null(空值率不受影响)。"""
    stats = compute_quality_stats([{"a": 0}, {"a": 0}])
    assert stats["columns"][0]["null_rate"] == 0.0


def test_compute_stats_type_inference_all_types():
    """bool/int/float/str 由首非空值推断 → boolean/integer/float/text。"""
    rows = [{"b": True, "i": 5, "f": 1.5, "s": "x"}]
    cols = {c["name"]: c["type"] for c in compute_quality_stats(rows)["columns"]}
    assert cols == {"b": "boolean", "i": "integer", "f": "float", "s": "text"}


def test_compute_stats_bool_before_int():
    """bool 是 int 子类,必须按真实类型识别为 boolean(避免 True→integer)。"""
    rows = [{"flag": True}]
    assert compute_quality_stats(rows)["columns"][0]["type"] == "boolean"


def test_compute_stats_all_null_column_type_text():
    """全空列(全 None)兜底为 text;null_rate=1.0。"""
    rows = [{"a": None}, {"a": None}]
    cols = compute_quality_stats(rows)["columns"]
    assert cols == [{"name": "a", "type": "text", "null_rate": 1.0}]


def test_compute_stats_preserves_first_seen_order():
    """列序=跨行首见序(不按字典序)。"""
    rows = [{"b": 1, "a": 2}, {"c": 3}]
    names = [c["name"] for c in compute_quality_stats(rows)["columns"]]
    assert names == ["b", "a", "c"]


def test_compute_stats_null_rate_full_denominator():
    """分母=总行数;分子=三种空(missing/None/空串)合计。"""
    rows = [
        {"a": "x"},  # 非空
        {"a": None},  # None
        {},  # missing key
        {"a": ""},  # 空串
        {"a": "y"},  # 非空
    ]
    stats = compute_quality_stats(rows)
    assert stats["rows"] == 5
    assert stats["columns"][0]["null_rate"] == 3 / 5


# ---------------- schema_snapshot ----------------


def test_schema_snapshot_projects_name_type():
    """从 stats 投影列名+类型,丢弃 null_rate。"""
    stats = {
        "rows": 3,
        "columns": [
            {"name": "a", "type": "integer", "null_rate": 0.0},
            {"name": "b", "type": "text", "null_rate": 0.5},
        ],
    }
    assert schema_snapshot(stats) == [
        {"name": "a", "type": "integer"},
        {"name": "b", "type": "text"},
    ]


def test_schema_snapshot_empty():
    """空 stats → 空快照。"""
    assert schema_snapshot({"rows": 0, "columns": []}) == []


# ---------------- drift_diff ----------------


def test_drift_diff_prev_none_no_drift():
    """首版(prev=None)无漂移,三个桶都空。"""
    curr = [{"name": "a", "type": "integer"}]
    diff = drift_diff(None, curr)
    assert diff == {"added": [], "removed": [], "type_changed": []}


def test_drift_diff_added():
    diff = drift_diff(
        prev=[{"name": "a", "type": "integer"}],
        curr=[
            {"name": "a", "type": "integer"},
            {"name": "b", "type": "text"},
        ],
    )
    assert diff == {"added": ["b"], "removed": [], "type_changed": []}


def test_drift_diff_removed():
    diff = drift_diff(
        prev=[{"name": "a", "type": "integer"}, {"name": "b", "type": "text"}],
        curr=[{"name": "a", "type": "integer"}],
    )
    assert diff == {"added": [], "removed": ["b"], "type_changed": []}


def test_drift_diff_type_changed():
    diff = drift_diff(
        prev=[{"name": "a", "type": "integer"}],
        curr=[{"name": "a", "type": "text"}],
    )
    assert diff == {
        "added": [],
        "removed": [],
        "type_changed": [{"name": "a", "from": "integer", "to": "text"}],
    }


def test_drift_diff_mixed():
    """同时存在 added/removed/type_changed。"""
    diff = drift_diff(
        prev=[{"name": "a", "type": "integer"}, {"name": "old", "type": "text"}],
        curr=[{"name": "a", "type": "float"}, {"name": "new", "type": "boolean"}],
    )
    assert diff["added"] == ["new"]
    assert diff["removed"] == ["old"]
    assert diff["type_changed"] == [
        {"name": "a", "from": "integer", "to": "float"}
    ]


def test_drift_diff_no_change():
    """同名同类型 → 三个桶全空。"""
    prev = [{"name": "a", "type": "integer"}]
    diff = drift_diff(prev, prev)
    assert diff == {"added": [], "removed": [], "type_changed": []}


# ---------------- evaluate_policy ----------------


def test_evaluate_policy_none_skipped():
    """policy=None → skipped。"""
    assert evaluate_policy(None, {"rows": 0, "columns": []}, None) == (
        "skipped",
        None,
    )


def test_evaluate_policy_empty_dict_skipped():
    """policy={} 视为缺省 → skipped(容错:JSONB 缺 key)。"""
    assert evaluate_policy({}, {"rows": 0, "columns": []}, None) == (
        "skipped",
        None,
    )


def test_evaluate_policy_no_violation_passed():
    """有 max_null_rate 但所有列均未超 → passed。"""
    stats = {
        "rows": 2,
        "columns": [{"name": "a", "type": "integer", "null_rate": 0.5}],
    }
    verdict, reason = evaluate_policy({"max_null_rate": 0.6}, stats, None)
    assert verdict == "passed"
    assert reason is None


def test_evaluate_policy_null_rate_threshold_strict_gt_passes():
    """null_rate 恰等于阈值 → 通过(严格 >, == 不算超)。"""
    stats = {
        "rows": 2,
        "columns": [{"name": "a", "type": "integer", "null_rate": 0.5}],
    }
    verdict, _ = evaluate_policy({"max_null_rate": 0.5}, stats, None)
    assert verdict == "passed"


def test_evaluate_policy_null_rate_exceeds_fails_with_message():
    """null_rate > max_null_rate → failed,附 列名+实际率(2 位)+阈值(2 位)。"""
    stats = {
        "rows": 4,
        "columns": [
            {"name": "ok", "type": "integer", "null_rate": 0.0},
            {"name": "bad", "type": "text", "null_rate": 0.75},
        ],
    }
    verdict, reason = evaluate_policy({"max_null_rate": 0.5}, stats, None)
    assert verdict == "failed"
    # 首个违规格列;消息含列名、实际率(2 位)、阈值(2 位)
    assert reason is not None
    assert "bad" in reason
    assert "0.75" in reason
    assert "0.50" in reason


def test_evaluate_policy_null_rate_first_offending_column_wins():
    """多列违例时,取 stats 列序中首个违例(短路)。"""
    stats = {
        "rows": 4,
        "columns": [
            {"name": "first", "type": "text", "null_rate": 0.9},
            {"name": "second", "type": "text", "null_rate": 0.95},
        ],
    }
    _, reason = evaluate_policy({"max_null_rate": 0.5}, stats, None)
    assert reason is not None
    assert "first" in reason
    assert "second" not in reason


def test_evaluate_policy_schema_drift_blocked():
    """block_on_schema_drift=True 且存在漂移 → failed;消息含新增列名。"""
    drift = {"added": ["new"], "removed": [], "type_changed": []}
    stats = {
        "rows": 1,
        "columns": [{"name": "new", "type": "text", "null_rate": 0.0}],
    }
    verdict, reason = evaluate_policy(
        {"max_null_rate": None, "block_on_schema_drift": True}, stats, drift
    )
    assert verdict == "failed"
    assert reason is not None
    assert "new" in reason


def test_evaluate_policy_schema_drift_not_blocked_passed():
    """block_on_schema_drift=False(默认) → 即使有漂移也 passed。"""
    drift = {"added": ["x"], "removed": ["y"], "type_changed": []}
    stats = {"rows": 0, "columns": []}
    verdict, reason = evaluate_policy(
        {"block_on_schema_drift": False}, stats, drift
    )
    assert verdict == "passed"
    assert reason is None


def test_evaluate_policy_null_rate_takes_precedence_over_drift():
    """同时配置时,null 率违例优先于漂移(短路:first failing check wins)。"""
    stats = {
        "rows": 4,
        "columns": [{"name": "a", "type": "text", "null_rate": 0.9}],
    }
    drift = {"added": ["a"], "removed": [], "type_changed": []}
    verdict, reason = evaluate_policy(
        {"max_null_rate": 0.5, "block_on_schema_drift": True}, stats, drift
    )
    assert verdict == "failed"
    assert reason is not None
    assert "空值率" in reason  # 命中 null 率分支而非漂移分支


def test_evaluate_policy_missing_keys_tolerated():
    """policy 仅含 block_on_schema_drift 而 max_null_rate 缺 → 跳过该检查。"""
    stats = {
        "rows": 1,
        "columns": [{"name": "a", "type": "text", "null_rate": 0.99}],
    }
    verdict, _ = evaluate_policy({"block_on_schema_drift": False}, stats, None)
    assert verdict == "passed"


def test_evaluate_policy_drift_none_treated_as_no_drift():
    """drift=None(首版未传)→ 视为无漂移,不触发漂移分支。"""
    stats = {"rows": 1, "columns": [{"name": "a", "type": "text", "null_rate": 0.0}]}
    verdict, reason = evaluate_policy(
        {"max_null_rate": None, "block_on_schema_drift": True}, stats, None
    )
    assert verdict == "passed"
    assert reason is None
