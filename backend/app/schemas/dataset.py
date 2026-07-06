"""数据集相关 schema(读模型),对照领域模型 docs/plan/03-架构设计.md §1。"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import computed_field

from app.schemas.common import CamelModel, UtcDateTime, format_version_label
from app.services.semantic_registry import SemanticType


class DatasetTableRead(CamelModel):
    """版本内的一个表成员读模型(多 parquet:一行一表/文件)。"""

    table_name: str
    storage_uri: str
    format: str
    rows: int | None = None
    size: int | None = None
    schema_variant: str | None = None


class DatasetVersionRead(CamelModel):
    """数据集版本读模型(不可变快照)。"""

    id: str
    dataset_id: str
    version_no: int
    storage_uri: str
    stats_uri: str | None = None
    format: str
    rows: int | None = None
    size: int | None = None
    # 语义类型快照(与 data_type 正交,#1/#2/#8);读模型宽松为 str(防御历史值)
    semantic_type: str | None = None
    # 训练用途元数据(治理整改 G1):训练平台据此过滤。读模型宽松为 str(防御历史值)。
    train_type: str | None = None
    schema_variant: str | None = None
    # 多模态模态快照(images/audios/videos/text 子集);仅 multimodal 版本有值
    modalities: list[str] | None = None
    origin: str
    # hosted 版本指向的数据源 id(S3 凭证来源);受管版本为空(#18)
    source_datasource_id: str | None = None
    produced_by_job_id: str | None = None
    note: str | None = None
    # 安全扫描结论 + 发布状态(#4 发布门,docs/plan/11)
    scan_verdict: str = "unscanned"
    verdict_source: str | None = None
    verdict_note: str | None = None
    publish_status: str = "draft"
    published_at: UtcDateTime | None = None
    created_at: UtcDateTime
    # 表成员数组(数据集优先/多表):非 ORM 字段,由路由按 dataset_version_tables 填充。
    # 单表数据集 = 恰好一个成员(回填后的存量版本亦然);多表 = 各表一个成员。
    tables: list[DatasetTableRead] = []

    @computed_field  # 展示标签 versionLabel:v2026.6.16 (#5)
    @property
    def version_label(self) -> str:
        return format_version_label(self.version_no, self.created_at)

    # 样本条数(G1):复用 rows 列;显式钉 alias=recordCount(属性名与 ORM 列名不一致,
    # 不能靠 alias_generator 对 computed_field 自动生效)。供训练平台预检(如 eval≥300)。
    @computed_field(alias="recordCount")
    @property
    def record_count(self) -> int | None:
        return self.rows


class DatasetRead(CamelModel):
    """数据集读模型(元信息)。"""

    id: str
    name: str
    description: str | None = None
    # 接入/格式功能键(free-string,分栏过滤用,不收紧)
    data_type: str | None = None
    # 语义类型(与 data_type 正交);读模型宽松为 str(防御历史值)
    semantic_type: str | None = None
    # 三轴拆分:来源/接入方式(database|object_store|hdfs|local_upload|api_push)+ 原始格式
    source_kind: str | None = None
    source_format: str | None = None
    # 分类(#15):受控分类库引用 id + 回填名(category_name 由路由批量取名填充)
    category_id: str | None = None
    category_name: str | None = None
    owner: str
    creator: str
    last_modifier: str | None = None
    valid_until: datetime | None = None
    created_at: UtcDateTime
    updated_at: UtcDateTime
    # 数据集是否含 hosted 版本(供前端「S3 托管」徽标/删除门控,#18)。
    # 由路由按版本聚合填充,非 ORM 字段,默认 False。
    hosted: bool = False
    # 当前展示版本标签(如 v2026.6.16 (#5)):优先已发布版本(publish 不变量保证
    # 同数据集至多一个 published),无已发布版本时回退最新版本;无版本时 None。
    # 非 ORM 字段,由路由批量聚合填充。
    latest_version_label: str | None = None
    # 展示版本(优先 published,否则最新)的多模态模态集合;非 ORM,路由聚合填充。
    # 前端按其分类显示"图片/视频/音频/跨模态"子标签 + 列表筛选。非多模态/存量为 None。
    modalities: list[str] | None = None
    # 展示版本的训练用途元数据(治理整改 G1);非 ORM,路由聚合填充。
    train_type: str | None = None
    schema_variant: str | None = None
    # 标签名列表(多对多);非 ORM 字段,由路由批量聚合填充。
    tags: list[str] = []


class ExpiringDatasetOut(CamelModel):
    """即将到期(或已过期)数据集提醒项:登录后弹窗用的精简读模型。"""

    id: str
    name: str
    valid_until: UtcDateTime
    # 距到期天数(按自然日):今天到期=0,明天=1,已过期为负数
    days_left: int
    expired: bool


class HostS3Request(CamelModel):
    """外部 S3 数据托管登记入参(#18):把若干 S3 对象登记为受管数据集版本(不下载)。"""

    datasource_id: str
    bucket: str
    keys: list[str]
    name: str | None = None
    data_type: str | None = None
    # 分类(#15):受控分类库引用 id,可空,挂到创建的数据集上
    category_id: str | None = None


class PlatformHostRequest(CamelModel):
    """文件管理零拷贝接入入参:把平台 MinIO 对象登记为受管数据集版本(不下载)。"""

    bucket: str
    keys: list[str]
    name: str | None = None
    data_type: str | None = None
    category_id: str | None = None


class ExportS3Request(CamelModel):
    """导出已发布版本到外部 S3 数据源入参(下载/导出至 S3)。

    把版本各成员上传到目标 s3 数据源的 bucket[/prefix]——
    读源、写目标,绝不回写托管源对象。
    """

    datasource_id: str
    bucket: str
    prefix: str | None = None


class DatasetMemberRead(CamelModel):
    """数据集成员文件(manifest 数据集的一个媒体对象)读模型。"""

    name: str
    key: str
    bucket: str
    format: str
    size: int | None = None
    # 行数:结构化表成员/单文件版本有值;originals 原件与 manifest 媒体对象为 None
    rows: int | None = None
    # 湖→集血缘:该成员抽取自哪个湖快照(data_lake_snapshots.id);非湖来源为空
    source_snapshot_id: str | None = None


class DatasetDetailRead(DatasetRead):
    """数据集详情:元信息 + 版本列表。"""

    versions: list[DatasetVersionRead] = []
    # 当前用户对该数据集的生效级别(view/edit/admin/None),供前端按钮门控
    my_level: str | None = None


class DatasetCreate(CamelModel):
    """新建空数据集入参(数据集优先流程):建集后再由上传/采集往里加表成员。"""

    name: str
    # 分类(#15):受控分类库引用 id,可空
    category_id: str | None = None
    # data_type 保持 free-string,不收紧(与列表分栏过滤一致)
    data_type: str | None = None
    # semantic_type 校验为枚举:非法值 422;None 放行
    semantic_type: SemanticType | None = None
    # 训练用途元数据(G1)默认模板:落首个成员时写入版本级(版本不可变)
    train_type: str | None = None
    schema_variant: str | None = None
    # 标签名列表(多对多);建集时一并写入
    tags: list[str] = []


class DatasetUpdate(CamelModel):
    """数据集元数据编辑入参:全部可选,只更新传入字段。"""

    name: str | None = None
    description: str | None = None
    # data_type 保持 free-string,不收紧(避免编辑存量数据集 422,见 docs/plan/14)
    data_type: str | None = None
    # semantic_type 写入路径校验为枚举:非法值 422;None 放行(向后兼容)
    semantic_type: SemanticType | None = None
    # 多模态子类型(image|video|audio|cross):反写展示版本 modalities(合成代表值,
    # round-trip 经 classify_modalities 还原);仅 semantic_type=multimodal 时有意义。
    modality_subtype: Literal["image", "video", "audio", "cross"] | None = None
    # 分类(#15):受控分类库引用 id;显式传 null 清空分类
    category_id: str | None = None
    valid_until: datetime | None = None
    # 标签名列表(多对多);传入(含空 list)即全量替换,不传不动。
    tags: list[str] | None = None
