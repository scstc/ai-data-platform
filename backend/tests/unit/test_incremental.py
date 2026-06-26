"""增量采集纯单测(切片 C / Task 5)—— DB WHERE 拼接 + 文件 key 过滤 + 水位推进 helper。

锁的意图(对齐 spec):
- **DB 整表 + 增量水位** → ``_build_queries(extract, incremental=..., watermark=...)``
  拼出 ``SELECT cols FROM "tbl" WHERE "增量列" > <literal>``(timestamp 字面值带引号、
  integer 纯数字),首跑(无 watermark)不拼 WHERE(等价全量)。
- **无 incremental 的任务零回归** → ``_build_queries`` 默认参数为 None,
  原所有调用点(_build_queries(task.extract))行为不变。
- **文件过滤** → ``_filter_keys_by_watermark(keys, all_objects, incremental, watermark)``
  by=mtime 用 ``lastModified`` 过滤;by=name 用 key 字典序;首跑/无 incremental 不过滤。
- **水位推进** → ``compute_db_watermark(records, column)`` 取本批最大值;
  ``_compute_file_watermark(keys, all_objects, incremental)`` 同理;空批返回 None(不推进)。
- **SQL 注入安全** → 列名经 ``_quote_ident``(双引号转义);水位值按类型严格校验
  (integer 必须 int-like,timestamp 单引号转义)。

不依赖真 PG / 真 S3:全部为纯函数测试。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from app.services.connectors.base import (
    IngestError,
    _build_queries,
    _quote_ident,
    _sql_compare_literal,
    compute_db_watermark,
)
from app.services.connectors.mysql import MysqlConnector
from app.services.connectors.objectstore import (
    S3Connector,
    _compute_file_watermark,
    _filter_keys_by_watermark,
)


# ===========================================================================
# §1 _build_queries 增量 WHERE 拼接(DB 整表模式)
# ===========================================================================


class TestBuildQueriesIncrementalWhere:
    def test_table_mode_with_timestamp_watermark_appends_where(self) -> None:
        """mode=table + incremental(column=updated_at, type=timestamp)+ watermark
        → SQL 拼出 ``WHERE "updated_at" > '2026-01-01 00:00:00'``(单引号字面值)。"""
        extract = {"mode": "table", "tables": ["public.orders"]}
        incremental = {"column": "updated_at", "type": "timestamp"}
        watermark = {"value": "2026-01-01 00:00:00", "updatedAt": "2026-01-02 00:00:00"}
        out = _build_queries(extract, incremental=incremental, watermark=watermark)
        assert len(out) == 1
        suffix, sql = out[0]
        assert suffix == "public.orders"
        assert 'FROM "public"."orders"' in sql
        assert 'WHERE "updated_at" > ' in sql
        # timestamp 字面值必须带单引号,防 PG 把它当列名解析
        assert "'2026-01-01 00:00:00'" in sql

    def test_table_mode_with_integer_watermark_appends_unquoted_literal(self) -> None:
        """mode=table + incremental(column=id, type=integer)+ watermark
        → SQL 拼出 ``WHERE "id" > 12345``(纯数字字面值,无引号)。"""
        extract = {"mode": "table", "tables": ["events"]}
        incremental = {"column": "id", "type": "integer"}
        watermark = {"value": 12345}
        out = _build_queries(extract, incremental=incremental, watermark=watermark)
        _, sql = out[0]
        assert 'WHERE "id" > 12345' in sql
        # 整数字面值不应带引号(否则 PG 可能做文本比较,语义错)
        assert "'12345'" not in sql

    def test_table_mode_with_columns_projection_and_watermark_combines(self) -> None:
        """columns 投影 + incremental WHERE 共存:SELECT "id","name" FROM ... WHERE "id" > 100。"""
        extract = {
            "mode": "table",
            "tables": ["public.users"],
            "columns": ["id", "name"],
        }
        incremental = {"column": "id", "type": "integer"}
        watermark = {"value": 100}
        out = _build_queries(extract, incremental=incremental, watermark=watermark)
        _, sql = out[0]
        assert sql.startswith('SELECT "id", "name" FROM "public"."users"')
        assert 'WHERE "id" > 100' in sql

    def test_table_mode_multi_table_each_gets_where(self) -> None:
        """多表 + 增量 → 每张表 SELECT 都拼 WHERE(各自独立推进)。"""
        extract = {"mode": "table", "tables": ["t1", "t2"]}
        incremental = {"column": "id", "type": "integer"}
        watermark = {"value": 50}
        out = _build_queries(extract, incremental=incremental, watermark=watermark)
        assert len(out) == 2
        assert 'WHERE "id" > 50' in out[0][1]
        assert 'WHERE "id" > 50' in out[1][1]

    def test_first_run_no_watermark_no_where(self) -> None:
        """首跑(incremental 已配但 watermark=None)→ 不拼 WHERE(等价全量)。"""
        extract = {"mode": "table", "tables": ["t1"]}
        incremental = {"column": "id", "type": "integer"}
        out = _build_queries(extract, incremental=incremental, watermark=None)
        _, sql = out[0]
        assert "WHERE" not in sql, "首跑 watermark=None → 必须 SELECT * FROM (全量,不拼 WHERE)"

    def test_watermark_without_value_treated_as_first_run(self) -> None:
        """watermark 是空 dict / 缺 value 键 → 视为首跑,不拼 WHERE(防御性)。"""
        extract = {"mode": "table", "tables": ["t1"]}
        incremental = {"column": "id", "type": "integer"}
        for bad_wm in ({}, {"updatedAt": "2026-01-01"}, None):
            out = _build_queries(
                extract, incremental=incremental, watermark=bad_wm
            )
            _, sql = out[0]
            assert "WHERE" not in sql, f"watermark={bad_wm!r} 不应拼 WHERE"

    def test_no_incremental_no_where_zero_regression(self) -> None:
        """incremental=None → 完全不拼 WHERE,等价既有行为(零回归)。"""
        extract = {"mode": "table", "tables": ["t1"]}
        out = _build_queries(extract, incremental=None, watermark=None)
        _, sql = out[0]
        assert sql == 'SELECT * FROM "t1"'

    def test_no_incremental_args_default_kwargs_backward_compatible(self) -> None:
        """不传 incremental/watermark(老调用点)→ 行为零变化(纯签名向后兼容)。"""
        out = _build_queries({"mode": "table", "tables": ["t1"]})
        assert out == [("t1", 'SELECT * FROM "t1"')]

    def test_sql_mode_ignores_incremental(self) -> None:
        """sql 模式:用户自己控制 SQL → 不自动拼 WHERE(避免破坏用户语义)。"""
        extract = {"mode": "sql", "sql": "SELECT * FROM t1"}
        incremental = {"column": "id", "type": "integer"}
        watermark = {"value": 100}
        out = _build_queries(extract, incremental=incremental, watermark=watermark)
        assert out == [(None, "SELECT * FROM t1")], "sql 模式不应改动用户 SQL"


# ===========================================================================
# §2 SQL 注入安全(_quote_ident 列名 + _sql_compare_literal 值)
# ===========================================================================


class TestSqlInjectionSafety:
    def test_column_with_double_quote_is_escaped(self) -> None:
        """增量列名含双引号 → ``_quote_ident`` 转义防注入(标识符不能拼出注入)。"""
        extract = {"mode": "table", "tables": ["t1"]}
        # 列名带双引号 → 必须转义为 ""
        incremental = {"column": 'ev"il', "type": "integer"}
        watermark = {"value": 1}
        out = _build_queries(extract, incremental=incremental, watermark=watermark)
        _, sql = out[0]
        # 转义后列标识符为 "ev""il",杜绝突破双引号上下文
        assert '"ev""il"' in sql

    def test_integer_value_rejects_non_numeric(self) -> None:
        """integer 水位值必须 int-like;恶意/脏数据(如 ``1; DROP--``)→ IngestError。"""
        with pytest.raises(IngestError):
            _sql_compare_literal("1; DROP TABLE t--", "integer")
        with pytest.raises(IngestError):
            _sql_compare_literal("not-a-number", "integer")
        with pytest.raises(IngestError):
            _sql_compare_literal(None, "integer")

    def test_integer_value_accepts_int_and_numeric_str(self) -> None:
        """正例:整型值(int / 纯数字 str)→ 纯数字字面值。"""
        assert _sql_compare_literal(42, "integer") == "42"
        assert _sql_compare_literal("42", "integer") == "42"
        assert _sql_compare_literal(-1, "integer") == "-1"

    def test_timestamp_value_escapes_single_quotes(self) -> None:
        """timestamp 值里的单引号必须被 '' 转义(防突破字符串字面值上下文)。"""
        rendered = _sql_compare_literal("2026-01-01 'OR' 1=1", "timestamp")
        # 所有单引号被翻倍 → 无法结束字符串字面值上下文
        assert "''" in rendered
        assert rendered.startswith("'") and rendered.endswith("'")
        # 翻倍后,字符串里不再有未配对的单引号
        body = rendered[1:-1]
        assert body.count("'") % 2 == 0, "单引号必须成对(已翻倍)"

    def test_timestamp_value_iso_format_rendered_as_quoted_literal(self) -> None:
        """正例:ISO 时间戳 → 'YYYY-MM-DDTHH:MM:SS'(带单引号)。"""
        rendered = _sql_compare_literal("2026-01-01T00:00:00", "timestamp")
        assert rendered == "'2026-01-01T00:00:00'"

    def test_unknown_type_raises(self) -> None:
        """未知 type(非 timestamp/integer)→ IngestError(诚实失败,不猜)。"""
        with pytest.raises(IngestError):
            _sql_compare_literal("x", "varchar")  # type: ignore[arg-type]


# ===========================================================================
# §3 文件 key 过滤(S3 mtime / name)
# ===========================================================================


def _s3_obj(key: str, size: int = 1, last_modified: str | None = None) -> dict:
    """造 S3 list_objects 形态的条目。"""
    return {"key": key, "size": size, "lastModified": last_modified}


class TestFilterKeysByWatermark:
    def test_by_mtime_keeps_only_newer(self) -> None:
        """by=mtime → 只保留 lastModified > 水位的 key(老的不再重复采)。"""
        keys = ["a.csv", "b.csv", "c.csv"]
        all_objects = [
            _s3_obj("a.csv", last_modified="2026-01-01T00:00:00"),
            _s3_obj("b.csv", last_modified="2026-02-01T00:00:00"),
            _s3_obj("c.csv", last_modified="2026-03-01T00:00:00"),
        ]
        incremental = {"by": "mtime"}
        watermark = {"value": "2026-01-15T00:00:00"}
        kept = _filter_keys_by_watermark(keys, all_objects, incremental, watermark)
        assert set(kept) == {"b.csv", "c.csv"}, "a.csv mtime 早于水位 → 应被过滤"

    def test_by_name_keeps_only_greater(self) -> None:
        """by=name → key 字典序 > 水位 才保留(适合按日期/序号命名的批次)。"""
        keys = ["batch-001.jsonl", "batch-002.jsonl", "batch-003.jsonl"]
        all_objects = [_s3_obj(k) for k in keys]
        incremental = {"by": "name"}
        watermark = {"value": "batch-002.jsonl"}
        kept = _filter_keys_by_watermark(keys, all_objects, incremental, watermark)
        assert set(kept) == {"batch-003.jsonl"}, "字典序 <= 水位的 key 都被过滤"

    def test_first_run_no_watermark_returns_all(self) -> None:
        """首跑(watermark=None)→ 所有 keys 原样返回(全量采)。"""
        keys = ["a", "b", "c"]
        all_objects = [_s3_obj(k) for k in keys]
        incremental = {"by": "mtime"}
        kept = _filter_keys_by_watermark(
            keys, all_objects, incremental, watermark=None
        )
        assert kept == keys

    def test_no_incremental_returns_all_zero_regression(self) -> None:
        """无 incremental → 不过滤(零回归:既有任务行为不变)。"""
        keys = ["a", "b"]
        all_objects = [_s3_obj(k) for k in keys]
        kept = _filter_keys_by_watermark(
            keys, all_objects, incremental=None, watermark={"value": "x"}
        )
        assert kept == keys

    def test_watermark_without_value_returns_all(self) -> None:
        """watermark={} / 缺 value → 视为首跑(防御性:不丢数据)。"""
        keys = ["a", "b"]
        all_objects = [_s3_obj(k) for k in keys]
        incremental = {"by": "name"}
        for bad_wm in ({}, {"updatedAt": "x"}):
            kept = _filter_keys_by_watermark(
                keys, all_objects, incremental, watermark=bad_wm
            )
            assert kept == keys

    def test_by_mtime_skips_objects_without_lastmodified(self) -> None:
        """by=mtime 但对象缺 lastModified → 那个 key 被跳过(无据可判断 newer-than)。"""
        keys = ["a", "b"]
        all_objects = [
            _s3_obj("a", last_modified="2026-05-01T00:00:00"),
            _s3_obj("b", last_modified=None),  # 缺 mtime
        ]
        incremental = {"by": "mtime"}
        watermark = {"value": "2026-01-01T00:00:00"}
        kept = _filter_keys_by_watermark(keys, all_objects, incremental, watermark)
        assert set(kept) == {"a"}, "缺 mtime 的 b 无法判断 → 不采(避免重复)"

    def test_filter_preserves_input_order(self) -> None:
        """过滤后保序(便于调试 + 与 _keys_from_extract 的去重保序契约一致)。"""
        keys = ["keep1", "skip", "keep2"]
        all_objects = [
            _s3_obj("keep1", last_modified="2026-05-01T00:00:00"),
            _s3_obj("skip", last_modified="2026-01-01T00:00:00"),
            _s3_obj("keep2", last_modified="2026-06-01T00:00:00"),
        ]
        incremental = {"by": "mtime"}
        watermark = {"value": "2026-02-01T00:00:00"}
        kept = _filter_keys_by_watermark(keys, all_objects, incremental, watermark)
        assert kept == ["keep1", "keep2"]

    def test_unknown_by_returns_all_unfiltered(self) -> None:
        """incremental.by 不是 mtime/name → 不过滤(诚实降级,不丢数据)。"""
        keys = ["a", "b"]
        all_objects = [_s3_obj(k) for k in keys]
        incremental = {"by": "unknown"}  # type: ignore[dict-item]
        watermark = {"value": "x"}
        kept = _filter_keys_by_watermark(keys, all_objects, incremental, watermark)
        assert kept == keys


# ===========================================================================
# §4 水位推进 helper(DB 列 max + 文件 max)
# ===========================================================================


class TestComputeDbWatermark:
    def test_max_integer_column(self) -> None:
        """integer 列:取本批最大值作为下一轮水位。"""
        records = [{"id": 1}, {"id": 5}, {"id": 3}]
        assert compute_db_watermark(records, "id") == 5

    def test_max_timestamp_column(self) -> None:
        """timestamp 列:取本批最大 datetime(后续序列化为 ISO 存 JSONB)。"""
        records = [
            {"updated_at": datetime(2026, 1, 1)},
            {"updated_at": datetime(2026, 3, 15)},
            {"updated_at": datetime(2026, 2, 1)},
        ]
        assert compute_db_watermark(records, "updated_at") == datetime(2026, 3, 15)

    def test_empty_batch_returns_none(self) -> None:
        """空批 → None(调用方据此不动 watermark,不推进)。"""
        assert compute_db_watermark([], "id") is None

    def test_all_nulls_returns_none(self) -> None:
        """记录非空但列值全为 None → None(无可推进水位)。"""
        records = [{"id": None}, {"id": None}]
        assert compute_db_watermark(records, "id") is None

    def test_missing_column_returns_none(self) -> None:
        """记录没有该列 → None(连接器/列配置不一致时不抛、不推进)。"""
        records = [{"other": 1}, {"other": 2}]
        assert compute_db_watermark(records, "id") is None

    def test_partial_nulls_takes_max_of_present(self) -> None:
        """部分行缺值/为 None → 取非空值的 max(不因个别行 None 而丢弃整个水位)。"""
        records = [{"id": 1}, {"id": None}, {"id": 9}]
        assert compute_db_watermark(records, "id") == 9

    def test_single_record_returns_its_value(self) -> None:
        """单行 → 该行的列值即 max。"""
        records = [{"id": 42}]
        assert compute_db_watermark(records, "id") == 42


class TestComputeFileWatermark:
    def test_by_mtime_returns_max_lastmodified(self) -> None:
        """by=mtime → 取所有匹配 key 的 lastModified 的 max。"""
        keys = ["a", "b", "c"]
        all_objects = [
            _s3_obj("a", last_modified="2026-01-01T00:00:00"),
            _s3_obj("b", last_modified="2026-03-01T00:00:00"),
            _s3_obj("c", last_modified="2026-02-01T00:00:00"),
        ]
        incremental = {"by": "mtime"}
        assert _compute_file_watermark(keys, all_objects, incremental) == (
            "2026-03-01T00:00:00"
        )

    def test_by_name_returns_max_key(self) -> None:
        """by=name → 字典序最大的 key(下一轮从这个 key 之后开始采)。"""
        keys = ["batch-001", "batch-009", "batch-005"]
        all_objects = [_s3_obj(k) for k in keys]
        incremental = {"by": "name"}
        assert _compute_file_watermark(keys, all_objects, incremental) == "batch-009"

    def test_empty_keys_returns_none(self) -> None:
        """空批(无可采对象)→ None(不推进)。"""
        assert (
            _compute_file_watermark([], [_s3_obj("x")], {"by": "name"}) is None
        )

    def test_no_incremental_returns_none(self) -> None:
        """无 incremental(全量任务)→ 不需要推进水位。"""
        keys = ["a", "b"]
        all_objects = [_s3_obj(k) for k in keys]
        assert _compute_file_watermark(keys, all_objects, None) is None

    def test_by_mtime_skips_null_lastmodified(self) -> None:
        """by=mtime 但部分对象缺 lastModified → 取有 mtime 的 max(忽略 None)。"""
        keys = ["a", "b"]
        all_objects = [
            _s3_obj("a", last_modified="2026-05-01T00:00:00"),
            _s3_obj("b", last_modified=None),
        ]
        incremental = {"by": "mtime"}
        assert _compute_file_watermark(keys, all_objects, incremental) == (
            "2026-05-01T00:00:00"
        )

    def test_by_mtime_all_null_returns_none(self) -> None:
        """所有对象 lastModified=None → 无法计算 → None(不推进)。"""
        keys = ["a", "b"]
        all_objects = [_s3_obj("a", last_modified=None), _s3_obj("b", last_modified=None)]
        incremental = {"by": "mtime"}
        assert _compute_file_watermark(keys, all_objects, incremental) is None

    def test_unknown_by_returns_none(self) -> None:
        """未知 by → None(诚实降级,不推进未知语义的水位)。"""
        keys = ["a"]
        all_objects = [_s3_obj("a")]
        incremental = {"by": "unknown"}  # type: ignore[dict-item]
        assert _compute_file_watermark(keys, all_objects, incremental) is None


# ===========================================================================
# §5 端到端契约:filter → compute 串起来(模拟连接器内部流转)
# ===========================================================================


class TestFilterComputeRoundtrip:
    """模拟「先过滤、再算新水位」的连接器内部流程,锁住两端契约对齐。"""

    def test_filter_then_compute_yields_next_watermark(self) -> None:
        """过滤后剩下的 keys 的 max 即下一轮水位(by=name)。"""
        keys = ["b-001", "b-002", "b-003", "b-004"]
        all_objects = [_s3_obj(k) for k in keys]
        incremental = {"by": "name"}
        watermark = {"value": "b-002"}
        kept = _filter_keys_by_watermark(keys, all_objects, incremental, watermark)
        # 本轮采到了 b-003, b-004 → 下一轮水位 = b-004
        assert set(kept) == {"b-003", "b-004"}
        new_wm = _compute_file_watermark(kept, all_objects, incremental)
        assert new_wm == "b-004"

    def test_empty_after_filter_yields_none_no_advance(self) -> None:
        """过滤后为空 → 不推进水位(下一轮仍按原水位采,无新增不浪费)。"""
        keys = ["b-001", "b-002"]
        all_objects = [_s3_obj(k) for k in keys]
        incremental = {"by": "name"}
        watermark = {"value": "b-009"}  # 比所有 key 都大 → 全过滤掉
        kept = _filter_keys_by_watermark(keys, all_objects, incremental, watermark)
        assert kept == []
        new_wm = _compute_file_watermark(kept, all_objects, incremental)
        assert new_wm is None, "空批不推进(否则下轮水位会变成 None,误成首跑全量)"


# ===========================================================================
# §6 部分失败水位推进(C5 评审 Finding 1:DATA LOSS 修复)
#
# 锁的意图:水位推进必须在 land_records **成功之后**(running max of landed),
# 绝不能在落地前一次性取 max(ALL keys)——否则中途失败会把未落地的 key 也跳过。
# ===========================================================================


class _FakeTmpPath:
    """S3 download_to_temp 返回的替身:够 run_ingest 读 suffix / read_bytes / unlink。"""

    def __init__(self, suffix: str = ".jsonl") -> None:
        self.suffix = suffix

    def read_bytes(self) -> bytes:
        return b'{"x": 1}\n'

    def unlink(self, missing_ok: bool = False) -> None:  # noqa: ARG002
        pass


@pytest.mark.asyncio
async def test_s3_partial_failure_watermark_reflects_only_landed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """land_records 在 k1 失败 → task.watermark 只反映 k0(running max of landed),
    NOT max(ALL keys)。未落地的 k1/k2 在重试时仍可被采(防 DATA LOSS)。

    C5 评审 Finding 1 的核心断言:部分失败后水位 = max(已成功落地 keys),
    不能取 max(全量 keys)——否则 k1/k2 被 `_filter_keys_by_watermark` 过滤掉。
    """
    all_objects = [
        {"key": "a.jsonl", "size": 10, "lastModified": None},
        {"key": "b.jsonl", "size": 10, "lastModified": None},
        {"key": "c.jsonl", "size": 10, "lastModified": None},
    ]

    class _IncTask:
        name = "S3增量采集"
        extract = {"mode": "path", "paths": ["a.jsonl", "b.jsonl", "c.jsonl"]}
        incremental = {"by": "name"}
        watermark: Any | None = None
        category_id: str | None = None

    class _S3Ds:
        name = "S3源"
        config = {"bucket": "test-bucket"}

    async def _fake_list_objects(cfg: Any, bucket: str, prefix: str) -> list[dict]:  # noqa: ANN401
        return all_objects

    monkeypatch.setattr(
        "app.services.connectors.objectstore.list_objects", _fake_list_objects
    )

    async def _fake_download(cfg: Any, bucket: str, key: str) -> _FakeTmpPath:  # noqa: ANN401
        return _FakeTmpPath()

    monkeypatch.setattr(
        "app.services.connectors.objectstore.download_to_temp", _fake_download
    )

    def _fake_normalize(content: bytes, ext: str) -> list[dict]:  # noqa: ARG001
        return [{"x": 1}]

    monkeypatch.setattr(
        "app.services.connectors.objectstore.normalize_to_records",
        _fake_normalize,
    )

    landed_names: list[str] = []

    async def _fake_land(session: Any, records: list[dict], **kwargs: Any) -> tuple:  # noqa: ANN401
        name = kwargs.get("dataset_name", "")
        landed_names.append(name)
        if name == "b":  # Path("b.jsonl").stem == "b"
            raise RuntimeError("simulated landing failure on b.jsonl")
        return ("DS", "VER")

    monkeypatch.setattr(
        "app.services.connectors.objectstore.land_records", _fake_land
    )

    conn = S3Connector()
    task = _IncTask()
    ds = _S3Ds()

    # land_records 在 b.jsonl 处抛 → run_ingest 传播异常
    with pytest.raises(RuntimeError, match="simulated"):
        await conn.run_ingest(object(), task, ds, job_id="job-pf")

    # k0 (a.jsonl) 成功落地, k1 (b.jsonl) 失败 → 水位只反映 k0
    assert task.watermark is not None, "至少 a.jsonl 落地成功, 水位应被推进"
    assert task.watermark["value"] == "a.jsonl", (
        "部分失败后水位只能反映已成功落地的 a.jsonl;"
        "若取 max(ALL)=c.jsonl, 则 b/c 在重试时被过滤 → DATA LOSS"
    )
    # k0 落地, k1 尝试后失败, k2 未被尝试
    assert landed_names == ["a", "b"]
    # 回归断言:k1/k2 仍可通过过滤(重试可采)——水位 a.jsonl < b.jsonl < c.jsonl
    retry_kept = _filter_keys_by_watermark(
        ["a.jsonl", "b.jsonl", "c.jsonl"],
        all_objects,
        {"by": "name"},
        task.watermark,
    )
    assert set(retry_kept) == {"b.jsonl", "c.jsonl"}, (
        "重试时 b/c 应仍可被采(水位=a.jsonl, 字典序更大)"  # noqa: FLY002
    )


@pytest.mark.asyncio
async def test_s3_all_keys_land_advances_to_max() -> None:
    """全部 key 成功落地 → 水位 = max(ALL keys)(正常路径,与部分失败对照)。"""
    all_objects = [
        {"key": "a.jsonl", "size": 10, "lastModified": None},
        {"key": "b.jsonl", "size": 10, "lastModified": None},
        {"key": "c.jsonl", "size": 10, "lastModified": None},
    ]

    class _IncTask:
        name = "S3增量采集"
        extract = {"mode": "path", "paths": ["a.jsonl", "b.jsonl", "c.jsonl"]}
        incremental = {"by": "name"}
        watermark: Any | None = None
        category_id: str | None = None

    class _S3Ds:
        name = "S3源"
        config = {"bucket": "test-bucket"}

    async def _fake_list_objects(cfg: Any, bucket: str, prefix: str) -> list[dict]:  # noqa: ANN401
        return all_objects

    monkeypatch_proxy = pytest.MonkeyPatch()

    async def _fake_download(cfg: Any, bucket: str, key: str) -> _FakeTmpPath:  # noqa: ANN401
        return _FakeTmpPath()

    def _fake_normalize(content: bytes, ext: str) -> list[dict]:  # noqa: ARG001
        return [{"x": 1}]

    async def _fake_land(session: Any, records: list[dict], **kwargs: Any) -> tuple:  # noqa: ANN401
        return ("DS", "VER")

    monkeypatch_proxy.setattr(
        "app.services.connectors.objectstore.list_objects", _fake_list_objects
    )
    monkeypatch_proxy.setattr(
        "app.services.connectors.objectstore.download_to_temp", _fake_download
    )
    monkeypatch_proxy.setattr(
        "app.services.connectors.objectstore.normalize_to_records",
        _fake_normalize,
    )
    monkeypatch_proxy.setattr(
        "app.services.connectors.objectstore.land_records", _fake_land
    )

    try:
        conn = S3Connector()
        task = _IncTask()
        ds = _S3Ds()
        await conn.run_ingest(object(), task, ds, job_id="job-ok")
        assert task.watermark is not None
        assert task.watermark["value"] == "c.jsonl", (
            "全部成功落地 → 水位 = max(ALL) = c.jsonl"
        )
    finally:
        monkeypatch_proxy.undo()


# ===========================================================================
# §7 MySQL(goldendb)增量 fail-loud(C5 评审 Finding 2:silent wrong behavior)
# ===========================================================================


@pytest.mark.asyncio
async def test_mysql_run_ingest_rejects_incremental() -> None:
    """task.incremental 设置 → IngestError(fail loud,不静默全量采)。

    C5 评审 Finding 2:MySQL 连接器没接增量 WHERE / 水位推进,配置 incremental
    却静默走全量 = silent wrong behavior。Rule 12 要求显式报错。
    """
    conn = MysqlConnector()
    task = type(
        "_IncMysqlTask",
        (),
        {
            "name": "goldendb增量",
            "extract": {"mode": "sql", "sql": "SELECT 1"},
            "incremental": {"column": "id", "type": "integer"},
        },
    )()
    ds = type(
        "_MysqlDs", (), {"name": "goldendb源", "config": {"host": "h"}}
    )()
    with pytest.raises(IngestError, match="暂不支持增量采集"):
        await conn.run_ingest(object(), task, ds, job_id="job-x")


@pytest.mark.asyncio
async def test_mysql_run_ingest_no_incremental_still_works(monkeypatch: pytest.MonkeyPatch) -> None:
    """task.incremental=None → 正常编排(零回归:非增量任务行为不变)。"""
    import sys
    import types

    fake = types.ModuleType("asyncmy")

    class _FakeCursor:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def execute(self, sql):
            pass

        description = [("id",), ("name",)]

        async def fetchall(self):
            return [(1, "alice")]

    class _FakeConn:
        def cursor(self):  # asyncmy: 同步方法返回 async context manager
            return _FakeCursor()

        def close(self):
            pass

    async def _connect(**kwargs):  # noqa: ANN003
        return _FakeConn()

    fake.connect = _connect
    monkeypatch.setitem(sys.modules, "asyncmy", fake)

    async def _fake_land(session, records, **kwargs):  # noqa: ANN001, ANN003
        return ("DS", "VER")

    monkeypatch.setattr(
        "app.services.landing.land_records", _fake_land
    )

    conn = MysqlConnector()
    task = type(
        "_MysqlTask",
        (),
        {
            "name": "goldendb全量",
            "extract": {"mode": "sql", "sql": "SELECT id, name FROM users"},
        },
    )()
    ds = type(
        "_MysqlDs", (), {"name": "goldendb源", "config": {"host": "h"}}
    )()
    results = await conn.run_ingest(object(), task, ds, job_id="job-7")
    assert results == [("DS", "VER")]
