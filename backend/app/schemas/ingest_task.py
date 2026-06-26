"""采集任务相关 schema，对照 frontend typings.d.ts 的 IngestTask 系列类型。"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from app.schemas.common import CamelModel, UtcDateTime

IngestTaskStatus = Literal["pending", "running", "success", "failed"]


class IngestSchedule(CamelModel):
    """调度配置。

    cron 调度本期未启用(§4.10):mode 模型层仍接受 'cron'(兼容存量 schedule
    透传/读取),但在 IngestTaskCreate/Update 校验器里拒绝创建 cron 任务,杜绝
    「UI 可建 cron 却永不触发」的误导面。
    """

    mode: Literal["once", "cron"]
    cron: str | None = None


class PipelineStep(CamelModel):
    """流水线算子步骤:算子名 + 参数(与前端 opCart / DataPlatform.PipelineStep 同形)。"""

    name: str
    params: dict[str, Any] = {}


class QualityPolicy(CamelModel):
    """任务级采集质量策略(切片 B)。

    采集落地时据此对版本数据做质量门检查,失败可阻断发布或仅记 verdict。
    - maxNullRate:单列最大允许 null 率,闭区间 [0, 1];None 表示不做该检查。
    - blockOnSchemaDrift:与历史 schema_snapshot 比较出现 drift 时是否阻断
      (True 阻断/标 failed,False 仅记录不阻断)。默认 False。
    """

    max_null_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    block_on_schema_drift: bool = False


class Incremental(CamelModel):
    """增量采集配置(切片 C / Task 1)。

    两种互斥形,二选一(model_validator 拒绝混合或全空):
    - 库形(DB 列水位):``{column:str, type:Literal['timestamp','integer']}``
      按 DB 某列(时间戳/自增主键)的高水位推进;由运行期根据 type 解析 value。
    - 文件形(mtime/name):``{by:Literal['mtime','name']}``
      按 S3/HDFS 对象的 mtime 或文件名排序推进。

    与模型层 ingest_tasks.incremental JSONB 同形;未配置(整个 incremental=None)
    等价全量采集。
    """

    # 库形字段
    column: str | None = None
    type: Literal["timestamp", "integer"] | None = None
    # 文件形字段
    by: Literal["mtime", "name"] | None = None

    @model_validator(mode="after")
    def _check_one_form(self) -> Incremental:
        """二选一:库形(column + type 全填)/ 文件形(by 填)任选其一,
        混合或全空拒绝。"""
        has_db = self.column is not None and self.type is not None
        has_db_partial = (self.column is not None) ^ (self.type is not None)
        has_file = self.by is not None

        if has_db_partial:
            raise ValueError(
                "incremental 库形必须同时给 column + type"
            )
        if has_db and has_file:
            raise ValueError(
                "incremental 不能同时指定库形(column+type)与文件形(by)"
            )
        if not has_db and not has_file:
            raise ValueError(
                "incremental 必须二选一:库形 {column,type} 或 文件形 {by}"
            )
        return self


class IngestExtract(CamelModel):
    """采集对象(extract spec):拉什么 + 落地前怎么过滤。

    - mode=table:用 tables(勾选的表名列表,每张表各产一个数据集,支持 schema.table)。
    - mode=sql  :用 sql(单条查询语句,产一个数据集)。
    - mode=path :用 paths(显式对象键/路径列表)和/或 glob(通配符),供 S3/HDFS 采集。
    - operators:可选 data-juicer 算子流水线,记录落地前依次过滤/清洗
      (DB 连接器 fetch 之后、land 之前内联跑,见 base.apply_filter_operators)。
    """

    mode: Literal["table", "sql", "path"]
    tables: list[str] | None = None
    sql: str | None = None
    paths: list[str] | None = None
    glob: str | None = None
    # table 模式下列裁剪(仅 mode=table 有效,Task 1 的 _build_queries 已消费此字段)
    columns: list[str] | None = None
    operators: list[PipelineStep] | None = None

    @model_validator(mode="after")
    def _check_mode_fields(self) -> IngestExtract:
        """校验 mode 与对应字段一致(空值不在此强制,留给连接器运行期诚实失败)。"""
        if self.mode == "table" and self.sql:
            raise ValueError("extract.mode=table 时不应携带 sql")
        if self.mode == "sql" and self.tables:
            raise ValueError("extract.mode=sql 时不应携带 tables")
        if self.mode == "path" and (self.tables or self.sql):
            raise ValueError("extract.mode=path 时不应携带 tables/sql")
        if self.mode != "path" and (self.paths or self.glob):
            raise ValueError("paths/glob 仅在 extract.mode=path 时有效")
        if self.columns and self.mode != "table":
            raise ValueError(
                "extract.columns 仅在 mode=table 时有效"
                "(sql 由语句决定列,path 为半结构化记录)"
            )
        return self


def _reject_cron(schedule: IngestSchedule | None) -> None:
    """拒绝 cron 调度创建/更新(§4.10 兜底,本期不做 cron 真跑)。"""
    if schedule is not None and schedule.mode == "cron":
        raise ValueError("cron 调度尚未启用")


class IngestTaskRead(CamelModel):
    """采集任务读模型（响应）。"""

    id: str
    name: str
    datasource_id: str
    datasource_name: str
    schedule: IngestSchedule
    extract: IngestExtract | None = None
    status: IngestTaskStatus
    progress: int
    run_count: int = 0
    # 分类(#15):受控分类库引用 id + 回填名(category_name 由路由批量取名填充)
    category_id: str | None = None
    category_name: str | None = None
    created_at: UtcDateTime
    last_run_at: UtcDateTime | None = None
    logs: list[str] | None = None
    # 产物概要列表(仅详情填充):每项含 datasetId/datasetName/versionId/versionNo/rows
    output: list[dict[str, Any]] | None = None
    # 任务级质量策略(切片 B):透传出前端展示/编辑,可空
    quality_policy: QualityPolicy | None = None
    # 增量采集配置(切片 C):透传出前端展示/编辑,可空(None=全量采集)
    incremental: Incremental | None = None


class IngestRunRead(CamelModel):
    """采集运行记录读模型(一次运行明细,数据来自 jobs 表 type=ingest)。"""

    id: str
    task_id: str
    # running 仅在进程中途崩溃遗留时出现(正常路径同步跑完即终态)
    status: Literal["success", "failed", "running"]
    rows: int
    dataset_count: int
    outputs: list[dict[str, Any]] | None = None
    error: str | None = None
    started_at: UtcDateTime
    finished_at: UtcDateTime | None = None


class IngestTaskCreate(CamelModel):
    """新建采集任务入参。"""

    name: str
    datasource_id: str
    schedule: IngestSchedule
    extract: IngestExtract | None = None
    # 分类(#15):受控分类库引用 id,可空
    category_id: str | None = None
    # 任务级质量策略(切片 B):可选,None 表示不做质量门检查
    quality_policy: QualityPolicy | None = None
    # 增量采集配置(切片 C):可选,None 表示全量采集(无增量)
    incremental: Incremental | None = None

    @model_validator(mode="after")
    def _reject_cron_schedule(self) -> IngestTaskCreate:
        """cron 调度尚未启用(§4.10):创建端拒绝,避免建了永不触发的任务。"""
        _reject_cron(self.schedule)
        return self


class IngestTaskUpdate(CamelModel):
    """编辑采集任务入参:全部可选,仅更新显式传入的字段。"""

    name: str | None = None
    datasource_id: str | None = None
    schedule: IngestSchedule | None = None
    extract: IngestExtract | None = None
    # 分类(#15):受控分类库引用 id
    category_id: str | None = None
    # 任务级质量策略(切片 B):可选,传 None/缺省 = 不变(由路由侧区分)
    quality_policy: QualityPolicy | None = None
    # 增量采集配置(切片 C):可选,传 None/缺省 = 不变(由路由侧区分)
    incremental: Incremental | None = None

    @model_validator(mode="after")
    def _reject_cron_schedule(self) -> IngestTaskUpdate:
        """cron 调度尚未启用(§4.10):更新端同样拒绝。"""
        _reject_cron(self.schedule)
        return self
