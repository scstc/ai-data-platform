"""连接器契约与共享工具(数据接入重构 §4.2)。

所有连接器(pg/mysql/proprietary/objectstore/hdfs/push)与 wiring 注册表共享:

- ``IngestError`` —— 采集执行失败(配置缺失 / 连接 / 查询错误)。原在 ingest_runner,
  随 ``_build_queries``/``_quote_ident`` 一并搬到此处供 PG/MySQL 族共用;ingest_runner 与
  connectors.pg 后续从这里再导出(§4.4 / §12.5),现有 import 不断。
- ``ConnectorNotReady`` —— 连接器结构就绪但环境未就绪(缺驱动 / 无集群),用于诚实降级
  (纠正现状 randint 假成功,Rule 12)。
- ``Connector`` Protocol —— 函数式协议(非 ABC 继承),签名与现状
  ``ingest_runner.run_pg_ingest(session, task, ds, *, job_id)`` 零冲突。
- ``StructuralStub`` —— 可复用的 not-ready 连接器:probe 诚实返回「结构就绪未装驱动」,
  list_tables / run_ingest 抛 ``ConnectorNotReady``。
- ``_build_queries`` / ``_quote_ident`` —— 由 ingest_runner 原样搬入的共享查询编排工具
  (行为与签名不变),PG/MySQL 族复用,方言差异各 connector 覆盖。
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

    from app.models.dataset import Dataset
    from app.models.dataset_version import DatasetVersion
    from app.models.datasource import DataSource
    from app.models.ingest_task import IngestTask


class IngestError(RuntimeError):
    """采集执行失败(配置缺失 / 连接 / 查询错误)。"""


class ConnectorNotReady(RuntimeError):
    """连接器结构就绪但环境未就绪(缺驱动 / 无集群),诚实降级用,非 bug。"""


@runtime_checkable
class Connector(Protocol):
    """来源连接器协议(§4.2):任意来源 → 字节/记录 → land_records。

    与现状 ``run_pg_ingest(session, task, ds, *, job_id)`` 签名零冲突。
    """

    async def probe(self, config: dict) -> tuple[bool, int, str]:
        """测连接。返回 (是否成功, 耗时毫秒, 文案)。"""
        ...

    async def list_tables(self, config: dict) -> list:
        """列表/列对象/列路径(表名 str 或对象 dict)。"""
        ...

    async def run_ingest(
        self,
        session: AsyncSession,
        task: IngestTask,
        datasource: DataSource,
        *,
        job_id: str,
    ) -> list[tuple[Dataset, DatasetVersion]]:
        """真实拉取 → 每张表/查询/对象各落地一个受管版本。"""
        ...


class StructuralStub:
    """结构就绪但环境未就绪的连接器(缺驱动 / 无集群)。

    ``probe`` 诚实返回失败 + 明确文案(不崩、不 500、绝不伪造 success);
    ``list_tables`` / ``run_ingest`` 抛 ``ConnectorNotReady``。
    达梦 / 巨杉 / hive / doris / hdfs 等在驱动/集群就位前用它兜底(§4.6)。
    """

    def __init__(self, brand: str, driver_pkg: str) -> None:
        self.brand = brand
        self.driver_pkg = driver_pkg

    @property
    def _not_ready_msg(self) -> str:
        return (
            f"{self.brand} 连接器结构就绪,本环境未装驱动 {self.driver_pkg},暂不可真连"
        )

    async def probe(self, config: dict) -> tuple[bool, int, str]:
        return (False, 0, self._not_ready_msg)

    async def list_tables(self, config: dict) -> list:
        raise ConnectorNotReady(self._not_ready_msg)

    async def run_ingest(
        self,
        session: AsyncSession,
        task: IngestTask,
        datasource: DataSource,
        *,
        job_id: str,
    ) -> list[tuple[Dataset, DatasetVersion]]:
        raise ConnectorNotReady(self._not_ready_msg)


def _quote_ident(name: str) -> str:
    """安全引用 SQL 标识符(支持 schema.table),双引号转义防注入。"""
    parts = [p for p in name.split(".") if p]
    return ".".join('"' + p.replace('"', '""') + '"' for p in parts)


def _sql_compare_literal(value: Any, type_: str) -> str:
    """把 Python 水位值渲染为 SQL 比较字面值(``WHERE col > <literal>`` 用)。

    - ``type_='integer'``:严格 int-like(int 或纯数字 str)→ 纯数字字面值(无引号);
      非 int-like(如 ``"1; DROP TABLE--"``)→ ``IngestError``(防注入 + 防脏数据)。
    - ``type_='timestamp'``:ISO 时间戳字符串 → 单引号字符串字面值,内部单引号
      翻倍转义(防御性:正常 ISO 不含单引号,但恶意/脏数据可能含)。
    - 其他 type_ → ``IngestError``(诚实失败,不猜语义)。

    列名走 ``_quote_ident``(双引号转义);**值**走本函数——两端都防注入,
    杜绝 ``column='"id); DROP..."`` 之类的字面值注入路径。
    """
    if type_ == "integer":
        try:
            n = int(value)
        except (TypeError, ValueError) as exc:
            raise IngestError(
                f"增量水位值 {value!r} 不是整数(type=integer)"
            ) from exc
        return str(n)
    if type_ == "timestamp":
        # ISO 时间戳不含单引号,但用户/脏数据可能塞进来——翻倍转义即可防御。
        s = str(value)
        return "'" + s.replace("'", "''") + "'"
    raise IngestError(
        f"不支持的增量列类型 {type_!r}(仅 'timestamp' / 'integer')"
    )


def _build_queries(
    extract: dict[str, Any] | None,
    *,
    incremental: dict[str, Any] | None = None,
    watermark: dict[str, Any] | None = None,
) -> list[tuple[str | None, str]]:
    """由采集对象生成 [(数据集名后缀, 查询)] 列表。

    - sql 模式:单条,后缀为空(不消费 incremental/watermark——用户自己控制 SQL,
      自动拼 WHERE 会破坏用户语义;spec 文档化「SQL 模式自行控制增量」)。
    - table 模式:勾选的每张表一条(后缀=表名),各产一个数据集。
      切片 C / Task 5:若同时给 ``incremental``(``{column, type}``)与
      ``watermark``(``{value: ...}``),每张表 SELECT 自动追加
      ``WHERE "增量列" > <字面值>``(类型经 ``_sql_compare_literal`` 严格渲染),
      推进式增量采集。``incremental`` 缺 / ``watermark`` 缺 value → 不拼 WHERE
      (首跑全量,行为零回归)。

    其余参数(签名关键字参数,**向后兼容**):``_build_queries(task.extract)``
    等既有调用点行为不变(无 incremental → 全量 SELECT *)。
    """
    extract = extract or {}
    mode = extract.get("mode")
    if mode == "sql":
        sql = (extract.get("sql") or "").strip()
        if not sql:
            raise IngestError("采集对象为 SQL,但 SQL 为空")
        return [(None, sql)]
    if mode == "table":
        tables = [t.strip() for t in (extract.get("tables") or []) if t.strip()]
        if not tables:
            raise IngestError("采集对象为表,但未选择任何表")
        columns = [c.strip() for c in (extract.get("columns") or []) if c.strip()]
        if columns:
            col_list = ", ".join(_quote_ident(c) for c in columns)
            base_queries = [
                (t, f"SELECT {col_list} FROM {_quote_ident(t)}") for t in tables
            ]
        else:
            base_queries = [
                (t, f"SELECT * FROM {_quote_ident(t)}") for t in tables
            ]
        return [
            (suffix, _apply_incremental_where(q, incremental, watermark))
            for suffix, q in base_queries
        ]
    raise IngestError("未配置采集对象(请选择表或填写 SQL)")


def _apply_incremental_where(
    base_sql: str,
    incremental: dict[str, Any] | None,
    watermark: dict[str, Any] | None,
) -> str:
    """若 ``incremental`` + ``watermark.value`` 均就绪 → 在 ``base_sql`` 后拼
    ``WHERE "列名" > <字面值>``;否则原样返回(零回归,首跑全量)。

    抽出此纯函数是为单测可断言(避免 ``_build_queries`` 把列裁剪 + 增量 WHERE
    两件事缠在一起测),并在 PG/MySQL 族连接器间共享相同拼装语义。
    """
    if not incremental:
        return base_sql
    column = incremental.get("column")
    type_ = incremental.get("type")
    if not column or not type_:
        return base_sql
    wm_value = (watermark or {}).get("value")
    if wm_value is None:
        return base_sql  # 首跑(无水位)→ 全量
    literal = _sql_compare_literal(wm_value, type_)
    return f"{base_sql} WHERE {_quote_ident(column)} > {literal}"


def compute_db_watermark(
    records: list[dict[str, Any]], column: str
) -> Any | None:
    """取本批记录中 ``column`` 列的最大值(下一轮增量的高水位)。

    - 空批 → ``None``(调用方据此**不推进** watermark:无新增不动水位)。
    - 部分行缺列 / 列为 ``None`` → 忽略,取非空值的 max;
      全部为 None/缺失 → ``None``。

    返回值类型由记录里的列决定(timestamp 列 → ``datetime``;
    integer 列 → ``int``)。调用方负责把 ``datetime`` 序列化为 ISO 字符串再存 JSONB。
    """
    values = [
        r.get(column)
        for r in records
        if r.get(column) is not None
    ]
    if not values:
        return None
    return max(values)


async def apply_filter_operators(
    task: IngestTask, records: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """采集落地前的算子过滤:按 ``task.extract.operators`` 跑 data-juicer 算子流水线。

    供 PG/MySQL/专有库等 DB 连接器在 fetch 之后、``land_records`` 之前内联调用:
    每张表/查询取回的 records 先过算子流水线,存活记录再落地为受管版本。
    extract 未配 operators 或为空 → 原样返回(零回归)。dj-process 失败转
    IngestError(诚实失败,中止本次采集,已落地的早表保留)。
    """
    operators = (task.extract or {}).get("operators") or []
    if not operators:
        return records
    # 延迟导入避免连接器层在 import 期拉起 engine(及其 DJ 子进程相关依赖)
    from app.services.engine import EngineError, filter_records  # noqa: PLC0415

    try:
        kept, _log = await filter_records(records, operators)
    except EngineError as exc:
        raise IngestError(f"采集算子过滤失败:{exc}") from exc
    return kept
