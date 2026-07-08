"""run_preview 的纯逻辑助手:前 N 行非空行读取 + 列并集保序。

不依赖 DB / dj-process,可独立单测;预览端到端由服务器 E2E 验证。
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from app.services.engine import (
    _column_union,
    _read_jsonl_head,
    config_yaml_for_display,
)


def _write(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_read_head_skips_blank_and_caps_at_limit(tmp_path: Path) -> None:
    p = tmp_path / "data.jsonl"
    _write(
        p,
        [
            json.dumps({"id": 1}),
            "",  # 空行应跳过,不计入 limit
            "   ",  # 仅空白也跳过
            json.dumps({"id": 2}),
            json.dumps({"id": 3}),
        ],
    )
    # limit=2 只取前两个非空行
    rows = _read_jsonl_head(p, 2)
    assert [r["id"] for r in rows] == [1, 2]


def test_read_head_zero_limit_reads_all(tmp_path: Path) -> None:
    p = tmp_path / "data.jsonl"
    _write(p, [json.dumps({"id": i}) for i in range(5)])
    # limit<=0 时读全部,用于统计产出总行数场景
    rows = _read_jsonl_head(p, 0)
    assert [r["id"] for r in rows] == [0, 1, 2, 3, 4]


def test_column_union_preserves_first_seen_order() -> None:
    before = [{"text": "a", "meta": 1}]
    # after 引入新键 score,且重复键不应改变既有顺序
    after = [{"text": "b", "score": 0.9}, {"text": "c", "meta": 2}]
    assert _column_union(before, after) == ["text", "meta", "score"]


def test_column_union_empty_inputs() -> None:
    assert _column_union([], []) == []


def test_display_yaml_strips_custom_operator_dirs() -> None:
    # custom_operator_paths 由 build_config 按服务器 upload_dir 现拼绝对路径,
    # 展示版必须只留文件名——否则把开发机/生产机目录结构泄漏给前端
    raw = yaml.safe_dump(
        {
            "project_name": "job-x",
            "np": 4,
            "process": [{"generate_cot_mapper": None}],
            "custom_operator_paths": [
                "C:/Users/dev/ai-data-platform/backend/var/uploads/custom_operators/generate_cot_mapper.py",
                "/data/uploads/custom_operators/generate_sft_mapper.py",
            ],
        }
    )
    shown = yaml.safe_load(config_yaml_for_display(raw))
    assert shown["custom_operator_paths"] == [
        "generate_cot_mapper.py",
        "generate_sft_mapper.py",
    ]
    # 既有行为不回归:内部运行期键仍被剥掉,process 原样保留
    assert "project_name" not in shown and "np" not in shown
    assert shown["process"] == [{"generate_cot_mapper": None}]
