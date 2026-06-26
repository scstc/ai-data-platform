"""连接器层(L1)纯单测 —— 数据接入重构 §4 / §9(docs/plan/14)。

不依赖 DB / 网络 / 驱动。锁的是接入重构的几条核心意图:

1. **注册表派发**(§4.3):resolve 把 (type, db_kind) 准确映射到连接器实例,
   未命中诚实返回 None(由调用方降级,而非崩溃)。
2. **诚实失败,绝不伪造 success**(§4.2 / §4.6 / Rule 12):专有库/HDFS 在
   驱动/集群缺位时 probe → (False, ...) + 明确文案,list/run → ConnectorNotReady;
   全程不崩、不 500、不伪成功。这正是要替换掉现状 randint 假成功的反面。
3. **查询编排不变**(§4.4):_build_queries 的 table/sql/path/空 四类分支行为
   字节级稳定(搬入 base.py 后须与原 ingest_runner 一致)。
4. **HDFS 可测中间档**(§4.6 评审点):_build_webhdfs_url / _parse_liststatus
   纯函数可测,无需集群。
5. **MySQL 编排**(§4.5):用 fake asyncmy 走通 _connect→SELECT→land_records,
   断言落地参数 data_type='sql' + semantic_type='structured',涉及真落库部分
   mock 掉 land_records(留给集成测)。
"""

from __future__ import annotations

import sys
import types

import pytest

from app.services.connectors import REGISTRY, resolve
from app.services.connectors.base import (
    Connector,
    ConnectorNotReady,
    IngestError,
    StructuralStub,
    _build_queries,
    _quote_ident,
)
from app.services.connectors.hdfs import (
    HdfsConnector,
    _build_webhdfs_url,
    _parse_liststatus,
)
from app.services.connectors.mysql import MysqlConnector
from app.services.connectors.objectstore import (
    S3Connector,
    _ext,
    _keys_from_extract,
    _media_manifest_row,
)
from app.services.connectors.pg import PgConnector
from app.services.connectors.proprietary import (
    DamengConnector,
    DorisConnector,
    HiveConnector,
    SequoiaConnector,
)
from app.services.connectors.push import PushConnector


# ===========================================================================
# §4.3 注册表派发:命中 / 未命中
# ===========================================================================
@pytest.mark.parametrize(
    "type_, db_kind, expected_cls",
    [
        ("database", "postgresql", PgConnector),
        ("database", "hologres", PgConnector),  # PG 线协议复用
        ("database", "kingbase", PgConnector),
        ("database", "gaussdb", PgConnector),
        ("database", "goldendb", MysqlConnector),
        ("database", "doris", DorisConnector),
        ("database", "dameng", DamengConnector),
        ("database", "sequoiadb", SequoiaConnector),
        ("database", "hive", HiveConnector),
        ("s3", None, S3Connector),
        ("hdfs", None, HdfsConnector),
        ("api", None, PushConnector),
    ],
)
def test_resolve_hits_expected_connector(type_, db_kind, expected_cls):
    """每个 (type, db_kind) 派发到设计指定的连接器类(§4.3 注册表)。"""
    conn = resolve(type_, db_kind)
    assert isinstance(conn, expected_cls)


def test_resolve_ignores_db_kind_for_object_protocols():
    """s3/hdfs/api 忽略 db_kind:传任意 db_kind 仍命中同一实例。"""
    assert isinstance(resolve("s3", "anything"), S3Connector)
    assert isinstance(resolve("hdfs", "x"), HdfsConnector)
    assert isinstance(resolve("api", "y"), PushConnector)


def test_resolve_unknown_returns_none_not_crash():
    """未知类型 / 未知 db_kind / db_kind 缺失 → None(调用方诚实降级,不崩)。"""
    assert resolve("database", "oracle") is None  # 不在注册表
    assert resolve("database", None) is None  # database 必须带 db_kind
    assert resolve("ftp", None) is None  # 整类不支持


def test_registry_values_satisfy_connector_protocol():
    """注册表里每个实例都满足 Connector 运行时协议(结构契约一致)。"""
    for inst in REGISTRY.values():
        assert isinstance(inst, Connector)


# ===========================================================================
# §4.2 / §4.6 诚实失败:StructuralStub + 专有库/HDFS 未装驱动 not-ready
# ===========================================================================
@pytest.mark.asyncio
async def test_structural_stub_probe_is_honest_failure():
    """StructuralStub.probe 返回 (False, 0, 明确文案),绝不伪造 success。"""
    stub = StructuralStub(brand="某库", driver_pkg="some_driver")
    ok, latency, msg = await stub.probe({})
    assert ok is False
    assert latency == 0
    assert "某库" in msg and "some_driver" in msg


@pytest.mark.asyncio
async def test_structural_stub_list_and_run_raise_not_ready():
    """StructuralStub.list_tables / run_ingest 抛 ConnectorNotReady(非 bug)。"""
    stub = StructuralStub(brand="某库", driver_pkg="some_driver")
    with pytest.raises(ConnectorNotReady):
        await stub.list_tables({})
    with pytest.raises(ConnectorNotReady):
        await stub.run_ingest(None, None, None, job_id="job-x")


# 本环境未装 dmPython / pysequoiadb / pyhive / asyncmy(doris) → 走 not-ready 路径。
# 这正是"驱动未装→明确 not-ready,不崩、不伪成功"的核心断言(§4.6 / Rule 12)。
@pytest.mark.parametrize(
    "factory, driver_mod",
    [
        (DamengConnector, "dmPython"),
        (SequoiaConnector, "pysequoiadb"),
        (HiveConnector, "pyhive"),
        (DorisConnector, "asyncmy"),
    ],
)
@pytest.mark.asyncio
async def test_proprietary_probe_not_ready_when_driver_absent(
    factory, driver_mod, monkeypatch
):
    """专有库驱动未装:probe 诚实返回失败(不抛、不崩),文案非空。"""
    # 确保驱动确实"未装":即使本机偶然装了也屏蔽,锁定 not-ready 分支
    monkeypatch.setitem(sys.modules, driver_mod, None)
    conn = factory()
    ok, latency, msg = await conn.probe(
        {"host": "h", "port": 1, "username": "u", "password": "p"}
    )
    assert ok is False
    assert isinstance(msg, str) and msg  # 明确文案,不为空


@pytest.mark.parametrize(
    "factory, driver_mod",
    [
        (DamengConnector, "dmPython"),
        (SequoiaConnector, "pysequoiadb"),
        (HiveConnector, "pyhive"),
        (DorisConnector, "asyncmy"),
    ],
)
@pytest.mark.asyncio
async def test_proprietary_list_tables_not_ready_when_driver_absent(
    factory, driver_mod, monkeypatch
):
    """专有库驱动未装:list_tables 抛 ConnectorNotReady(不伪造空表成功)。"""
    monkeypatch.setitem(sys.modules, driver_mod, None)
    conn = factory()
    with pytest.raises(ConnectorNotReady):
        await conn.list_tables({"host": "h"})


# ===========================================================================
# §4.4 / §4.2 查询编排 _build_queries:table / sql / path(空) / 空 四类分支
# ===========================================================================
def test_build_queries_sql_mode():
    """sql 模式:单条,后缀为空。"""
    out = _build_queries({"mode": "sql", "sql": "SELECT 1"})
    assert out == [(None, "SELECT 1")]


def test_build_queries_sql_strips_whitespace():
    """sql 前后空白被 strip。"""
    out = _build_queries({"mode": "sql", "sql": "  SELECT 2  "})
    assert out == [(None, "SELECT 2")]


def test_build_queries_sql_empty_raises():
    """sql 模式但 SQL 为空 → IngestError(诚实拒绝,不产空查询)。"""
    with pytest.raises(IngestError):
        _build_queries({"mode": "sql", "sql": "   "})


def test_build_queries_table_mode_quotes_idents():
    """table 模式:每张表一条 SELECT *,后缀为表名,标识符被安全引用。"""
    out = _build_queries({"mode": "table", "tables": ["t1", "public.t2"]})
    assert out == [
        ("t1", 'SELECT * FROM "t1"'),
        ("public.t2", 'SELECT * FROM "public"."t2"'),
    ]


def test_build_queries_table_empty_raises():
    """table 模式但未选任何表(含全空白)→ IngestError。"""
    with pytest.raises(IngestError):
        _build_queries({"mode": "table", "tables": ["  ", ""]})


def test_build_queries_table_columns_projected():
    """勾选列 → SELECT 投影到选中列(单点裁剪,rerun 与生成数据集双路径受益)。"""
    out = _build_queries(
        {"mode": "table", "tables": ["public.users"], "columns": ["id", "name"]}
    )
    assert out == [("public.users", 'SELECT "id", "name" FROM "public"."users"')]


def test_build_queries_table_no_columns_keeps_star():
    """未勾列 → 维持 SELECT *(向后兼容存量任务)。"""
    out = _build_queries({"mode": "table", "tables": ["t1"]})
    assert out == [("t1", 'SELECT * FROM "t1"')]


def test_build_queries_table_empty_columns_keeps_star():
    """columns 显式空数组 → 等价未勾,维持 SELECT *。"""
    out = _build_queries({"mode": "table", "tables": ["t1"], "columns": []})
    assert out == [("t1", 'SELECT * FROM "t1"')]


def test_build_queries_path_mode_raises_not_a_query_source():
    """path 模式(S3/HDFS)不走 SQL 编排 → 落到「未配置采集对象」分支抛错。

    _build_queries 仅认 sql/table;path 由 objectstore/hdfs 各自处理。
    """
    with pytest.raises(IngestError):
        _build_queries({"mode": "path", "paths": ["a/b.jsonl"]})


def test_build_queries_empty_or_none_raises():
    """extract 为 None / {} / 无 mode → 明确 IngestError(未配置采集对象)。"""
    for bad in (None, {}, {"mode": "unknown"}):
        with pytest.raises(IngestError):
            _build_queries(bad)


def test_quote_ident_escapes_double_quotes():
    """_quote_ident 双引号转义防注入,支持 schema.table。"""
    assert _quote_ident("tab") == '"tab"'
    assert _quote_ident("sch.tab") == '"sch"."tab"'
    assert _quote_ident('we"ird') == '"we""ird"'


# ===========================================================================
# §4.6 HDFS 可测中间档:_build_webhdfs_url / _parse_liststatus 纯函数
# ===========================================================================
def test_build_webhdfs_url_bare_host_adds_scheme_and_default_port():
    """裸 host:自动补 http:// 与默认端口 50070,拼出 WebHDFS 路径。"""
    url = _build_webhdfs_url("nn", "/user/data", "LISTSTATUS")
    assert url.startswith("http://nn:50070/webhdfs/v1/user/data?")
    assert "op=LISTSTATUS" in url


def test_build_webhdfs_url_host_with_port_preserved():
    """host:port 形式:端口原样保留,不被默认端口覆盖。"""
    url = _build_webhdfs_url("nn:8020", "/d", "OPEN")
    assert "http://nn:8020/webhdfs/v1/d?" in url
    assert "op=OPEN" in url


def test_build_webhdfs_url_path_normalized_and_op_uppercased():
    """path 不以 / 开头时自动补;op 统一大写。"""
    url = _build_webhdfs_url("http://nn:50070", "rel/path", "getfilestatus")
    assert "/webhdfs/v1/rel/path?" in url
    assert "op=GETFILESTATUS" in url


def test_build_webhdfs_url_extra_params_encoded():
    """额外参数追加进查询串并 URL 编码。"""
    url = _build_webhdfs_url("nn", "/d", "OPEN", offset=0, length=4096)
    assert "offset=0" in url
    assert "length=4096" in url


def test_parse_liststatus_extracts_entries():
    """LISTSTATUS 响应解析为 [{name,type,length}],type 原样保留。"""
    body = {
        "FileStatuses": {
            "FileStatus": [
                {"pathSuffix": "a.jsonl", "type": "FILE", "length": 12},
                {"pathSuffix": "sub", "type": "DIRECTORY", "length": 0},
            ]
        }
    }
    out = _parse_liststatus(body)
    assert out == [
        {"name": "a.jsonl", "type": "FILE", "length": 12},
        {"name": "sub", "type": "DIRECTORY", "length": 0},
    ]


def test_parse_liststatus_malformed_returns_empty_not_crash():
    """响应结构不符预期 → 返回空列表(诚实降级,不抛)。"""
    assert _parse_liststatus({}) == []
    assert _parse_liststatus({"FileStatuses": None}) == []
    assert _parse_liststatus({"wrong": "shape"}) == []


# ===========================================================================
# §4.7 PushConnector:推送型本地就绪,但不走采集 rerun 路径
# ===========================================================================
@pytest.mark.asyncio
async def test_push_connector_probe_ready_and_no_tables():
    """probe 永远就绪(端点入站);list_tables 无表语义返回空。"""
    conn = PushConnector()
    ok, latency, msg = await conn.probe({})
    assert ok is True
    assert latency == 0
    assert isinstance(msg, str) and msg
    assert await conn.list_tables({}) == []


@pytest.mark.asyncio
async def test_push_connector_run_ingest_rejects_rerun():
    """run_ingest 抛 ConnectorNotReady:推送只经端点入站,不走 rerun(§4.7)。"""
    conn = PushConnector()
    with pytest.raises(ConnectorNotReady):
        await conn.run_ingest(None, None, None, job_id="job-x")


# ===========================================================================
# §4.5 MySQL 编排:fake asyncmy.connect → 固定行 → land_records 落地
# ===========================================================================
class _FakeCursor:
    """模拟 asyncmy cursor:execute 后给固定 description + 固定行。"""

    description = (("id",), ("name",))

    def __init__(self, rows):
        self._rows = rows
        self.executed: list[str] = []

    async def execute(self, query):
        self.executed.append(query)

    async def fetchall(self):
        return list(self._rows)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False


class _FakeConn:
    """模拟 asyncmy connection:返回固定 cursor,记录是否 close。"""

    def __init__(self, rows):
        self._rows = rows
        self.closed = False

    def cursor(self):
        return _FakeCursor(self._rows)

    def close(self):
        self.closed = True


def _install_fake_asyncmy(monkeypatch, rows):
    """把一个 fake asyncmy 模块塞进 sys.modules,connect 返回固定行的连接。"""
    fake = types.ModuleType("asyncmy")

    async def _connect(**kwargs):  # noqa: ANN003
        return _FakeConn(rows)

    fake.connect = _connect
    monkeypatch.setitem(sys.modules, "asyncmy", fake)
    return fake


class _Task:
    """最小 IngestTask 替身(只用到 name / extract)。"""

    def __init__(self, extract):
        self.name = "MySQL采集"
        self.extract = extract


class _Datasource:
    """最小 DataSource 替身(只用到 name / config)。"""

    def __init__(self, config):
        self.name = "GoldenDB源"
        self.config = config


@pytest.mark.asyncio
async def test_mysql_run_ingest_orchestrates_landing(monkeypatch):
    """fake asyncmy:run_ingest 取固定行 → land_records,断言落地编排参数。

    意图(§4.5):goldendb 经 MysqlConnector 把 SELECT 结果整形为 dict 行后,
    统一以 data_type='sql'(接入键不变)+ semantic_type='structured'(语义维度)
    落地。真落库由集成测覆盖,这里 mock land_records 只验编排,纯 no-DB。
    """
    _install_fake_asyncmy(monkeypatch, rows=[(1, "alice"), (2, "bob")])

    captured: dict = {}

    async def _fake_land_records(session, records, **kwargs):  # noqa: ANN003
        captured["records"] = records
        captured["kwargs"] = kwargs
        return ("DATASET", "VERSION")

    # land_records 在 run_ingest 内部 lazy import,patch 模块原符号即可拦截
    monkeypatch.setattr(
        "app.services.landing.land_records", _fake_land_records
    )

    conn = MysqlConnector()
    task = _Task({"mode": "sql", "sql": "SELECT id, name FROM users"})
    ds = _Datasource({"host": "h", "port": 3306, "database": "d"})

    results = await conn.run_ingest(object(), task, ds, job_id="job-7")

    # 编排产物:一条查询 → 一个 (dataset, version) 对
    assert results == [("DATASET", "VERSION")]
    # 固定行被整形为以列名为键的 dict
    assert captured["records"] == [
        {"id": 1, "name": "alice"},
        {"id": 2, "name": "bob"},
    ]
    # 接入键不动 + 语义维度正交(铁律:data_type 一字不改)
    assert captured["kwargs"]["data_type"] == "sql"
    assert captured["kwargs"]["semantic_type"] == "structured"
    assert captured["kwargs"]["produced_by_job_id"] == "job-7"


@pytest.mark.asyncio
async def test_mysql_run_ingest_not_ready_when_driver_absent(monkeypatch):
    """asyncmy 未装:run_ingest 抛 ConnectorNotReady(在连库前诚实失败)。"""
    monkeypatch.setitem(sys.modules, "asyncmy", None)
    conn = MysqlConnector()
    task = _Task({"mode": "sql", "sql": "SELECT 1"})
    ds = _Datasource({"host": "h"})
    with pytest.raises(ConnectorNotReady):
        await conn.run_ingest(object(), task, ds, job_id="job-x")


@pytest.mark.asyncio
async def test_mysql_probe_not_ready_when_driver_absent(monkeypatch):
    """asyncmy 未装:probe 诚实返回 (False, 0, not-ready 文案),不崩。"""
    monkeypatch.setitem(sys.modules, "asyncmy", None)
    conn = MysqlConnector()
    ok, latency, msg = await conn.probe({"host": "h"})
    assert ok is False
    assert latency == 0
    assert isinstance(msg, str) and msg


# ===========================================================================
# §4.5 / §4.9 S3 媒体清单:复制进内置 MinIO → manifest 行(逐行带 type 模态标注)
# ===========================================================================
@pytest.mark.parametrize(
    "fmt, field, token, kind",
    [
        ("png", "images", "<__dj__image>", "image"),
        ("JPG", "images", "<__dj__image>", "image"),  # 大写扩展名归一
        ("mp4", "videos", "<__dj__video>", "video"),
        ("wav", "audios", "<__dj__audio>", "audio"),
    ],
)
def test_media_manifest_row_carries_type_and_dj_contract(fmt, field, token, kind):
    """媒体清单一行:多模态字段 [member_key] + dj token + **type 模态标注** + __member。

    意图(本特性核心):S3 桶内多种媒体混装时,每行 json 用 type 显式说明该文件类型;
    同时 images/audios/videos 字段 + dj 特殊 token 维持 DJ 多模态契约(可直接物化加工)。
    """
    fmt_l = fmt.lower()
    member_key = f"dset-x/000000-a.{fmt_l}"
    row = _media_manifest_row(member_key, f"a.{fmt_l}", 123, fmt)

    assert row[field] == [member_key]  # 模态字段落对象 key 数组
    assert row["type"] == kind  # 逐行 type 说明文件模态
    assert row["text"] == token  # dj 特殊 token 与模态一致
    assert row["__member"]["key"] == member_key
    assert row["__member"]["name"] == f"a.{fmt_l}"
    assert row["__member"]["size"] == 123
    assert row["__member"]["format"] == fmt  # 原样保留传入扩展名


def test_media_manifest_rows_mixed_kinds_self_describe():
    """图/音/视频混装:同一清单逐行 type 各异,字段各落各模态——满足「多种文件」诉求。"""
    rows = [
        _media_manifest_row("d/0-a.png", "a.png", 1, "png"),
        _media_manifest_row("d/1-b.mp4", "b.mp4", 2, "mp4"),
        _media_manifest_row("d/2-c.mp3", "c.mp3", 3, "mp3"),
    ]
    assert [r["type"] for r in rows] == ["image", "video", "audio"]
    assert "images" in rows[0] and "videos" in rows[1] and "audios" in rows[2]


def test_ext_partitions_media_from_data():
    """_ext 决定分流:媒体扩展名 ∈ BINARY_FORMATS 走清单,其余走逐对象落地。"""
    from app.services.landing import BINARY_FORMATS

    assert _ext("a/b/pic.PNG") == "png"
    assert _ext("clip.MP4") in BINARY_FORMATS
    assert _ext("data.csv") not in BINARY_FORMATS
    assert _ext("notes.jsonl") not in BINARY_FORMATS
    assert _ext("noext") == ""  # 无扩展名 → 非媒体,走 data 路(可能被诚实跳过)


# ===========================================================================
# S3 _keys_from_extract —— 由 extract spec + 桶内对象列表计算采集 key 列表
# ---------------------------------------------------------------------------
# 锁的核心意图:
# 1. **glob 前导 / 必须被剥掉**:S3 对象键永不含前导 /,用户按 HDFS/POSIX 习惯
#    写了 ``/*.*`` 期望匹配全量,不剥则 fnmatch 要求 key 以 / 开头 → 匹配为空
#    → 误报「采集对象为空」(本测试即回归该线上问题)。
# 2. **诚实且可诊断的失败**:填了 glob 却没命中,报「未匹配 + 桶内对象数」,
#    而非误导性的「请填写通配符」(Rule 12)。
# 3. paths 与 glob 合并去重保序;裸 key / 带前导 / / s3:// URI 都归一为桶内键。
# ===========================================================================
def _obj(key: str, size: int = 1) -> dict:
    """造一个 list_objects 形态的对象条目。"""
    return {"key": key, "size": size, "lastModified": None}


def test_keys_from_extract_glob_leading_slash_stripped():
    """glob 前导 / 被剥掉 → 等价于不带 / 的同一通配(线上回归点)。"""
    objects = [
        _obj("train.csv"),
        _obj("raw/2026/data.jsonl"),
        _obj("pic.png"),
    ]
    # 带前导 / —— 修复前命中 0 个(误报采集对象为空),修复后等价于 *.*
    keys = _keys_from_extract({"mode": "path", "glob": "/*.*"}, objects)
    assert set(keys) == {"train.csv", "raw/2026/data.jsonl", "pic.png"}


def test_keys_from_extract_paths_and_glob_merge_dedup_normalized():
    """paths(显式键)+ glob(通配)合并去重保序;裸 key / 前导 / / s3:// URI 都归一。"""
    objects = [
        _obj("a.csv"),
        _obj("b.csv"),
        _obj("c.txt"),
        _obj("pic.png"),
    ]
    keys = _keys_from_extract(
        {
            "mode": "path",
            "paths": ["a.csv", "s3://bucket/b.csv", "/c.txt"],
            "glob": "*.png",
        },
        objects,
    )
    # paths → a.csv、b.csv(s3 URI 归一)、c.txt(去前导 /);glob *.png 追加 pic.png
    assert keys == ["a.csv", "b.csv", "c.txt", "pic.png"]


def test_keys_from_extract_both_empty_raises_clear_message():
    """既无 paths 也无 glob → 诚实失败,提示「请填写」(此场景下该提示正确)。"""
    with pytest.raises(IngestError) as exc:
        _keys_from_extract({"mode": "path"}, [_obj("a.csv")])
    assert "采集对象为空" in str(exc.value)


def test_keys_from_extract_glob_no_match_reports_count_not_fillin():
    """填了 glob 但没命中 → 报「未匹配 + 桶内对象数」,不是误导性的「请填写」。"""
    with pytest.raises(IngestError) as exc:
        _keys_from_extract(
            {"mode": "path", "glob": "*.nomatch"},
            [_obj("a.csv"), _obj("b.txt")],
        )
    msg = str(exc.value)
    assert "未匹配" in msg
    assert "2" in msg  # 桶内对象数,便于定位


def test_keys_from_extract_rejects_non_path_mode():
    """S3 连接器只吃 mode=path;table/sql 走数据库连接器,这里防御性拒绝。"""
    with pytest.raises(IngestError):
        _keys_from_extract({"mode": "table"}, [])
