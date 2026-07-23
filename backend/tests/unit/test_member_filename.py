"""成员落盘/打包文件名补后缀(_member_filename)。

显示名通常无后缀,裸名流出后下游无法识别格式;
zip 下载 / export-s3 / 交付同步三处共用该函数。
"""

from __future__ import annotations

from types import SimpleNamespace

from app.api.v1.datasets import _member_filename


def _m(name: str | None, key: str, fmt: str | None = "jsonl"):
    return SimpleNamespace(name=name, key=key, format=fmt)


def test_bare_display_name_gets_key_suffix() -> None:
    m = _m("银行业务QA_2500条", "uploads/ds1/v1/data.jsonl")
    assert _member_filename(m) == "银行业务QA_2500条.jsonl"


def test_key_suffix_wins_over_format() -> None:
    m = _m("样本表", "uploads/ds1/v1/part-0.parquet", fmt="jsonl")
    assert _member_filename(m) == "样本表.parquet"


def test_format_fallback_when_key_has_no_suffix() -> None:
    m = _m("样本表", "uploads/ds1/v1/blob", fmt="parquet")
    assert _member_filename(m) == "样本表.parquet"


def test_name_with_suffix_untouched() -> None:
    m = _m("data.csv", "uploads/ds1/v1/data.csv")
    assert _member_filename(m) == "data.csv"


def test_no_name_falls_back_to_key_basename() -> None:
    m = _m(None, "uploads/ds1/v1/data.jsonl")
    assert _member_filename(m) == "data.jsonl"


def test_weird_format_not_appended() -> None:
    # format 非纯字母数字(脏数据)时不硬拼后缀
    m = _m("样本表", "uploads/ds1/v1/blob", fmt="a/b")
    assert _member_filename(m) == "样本表"
    m2 = _m("样本表", "uploads/ds1/v1/blob", fmt=None)
    assert _member_filename(m2) == "样本表"
