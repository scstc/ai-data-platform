"""加工任务(Job)相关 schema。"""

from __future__ import annotations

from typing import Any

from pydantic import computed_field

from app.schemas.common import CamelModel, UtcDateTime


class OperatorSpec(CamelModel):
    """编排里的一个算子:名称 + 参数。"""

    name: str
    params: dict[str, Any] | None = None


class MemberOperatorConfig(CamelModel):
    """单个成员的算子配置。"""

    member_name: str
    operators: list[OperatorSpec]
    # 可选：成员级别的清洗字段
    text_keys: list[str] | None = None


class JobCreate(CamelModel):
    """新建加工任务入参:对某个数据集版本跑一串算子。"""

    name: str
    type: str = "process"
    dataset_version_id: str
    # 治理工场:经流水线一键执行时回指来源(pipelines.id);手工建任务留空
    pipeline_id: str | None = None

    # 新版：成员级独立配置（优先）
    member_configs: list[MemberOperatorConfig] | None = None

    # 旧版：统一配置（向后兼容）
    operators: list[OperatorSpec] | None = None
    target_members: list[str] | None = None
    # 清洗作用字段(DJ text_keys):留空则后端按字段名优先级自动探测;
    # 显式指定则原样用(可多字段),用于脏字符不在标准字段(如 task)的场景。
    text_keys: list[str] | None = None

    # G6 分布式:切 DJ ray executor;仅在 capabilities.ray 就绪时放行(_start_job 校验)。
    use_ray: bool = False
    # G7 多模态:自定义媒体字段键(默认 images/audios/videos);仅 manifest 输入注入。
    image_key: str | None = None
    audio_key: str | None = None
    video_key: str | None = None


class QualityJobCreate(CamelModel):
    """新建质量评估任务入参(#6):对某个数据集版本逐条算 filter stats。"""

    name: str
    dataset_version_id: str

    # 新版：成员级独立配置（优先）
    member_configs: list[MemberOperatorConfig] | None = None

    # 旧版：统一配置（向后兼容）
    operators: list[OperatorSpec] | None = None
    target_members: list[str] | None = None
    # DJ text_keys:算子作用的主文本字段;留空则后端按字段名优先级自动探测。
    # 用于数据无 text 字段的场景(如蒸馏 instruction、GIS address)。
    text_keys: list[str] | None = None
    # 评分并入数据产新版本:评估默认只回写 stats 不产版本;开启后把逐条评分列
    # (llm_quality_score 等)合并进每条记录,按治理任务同款事务落一个新版本。
    produce_version: bool = False


class JobRead(CamelModel):
    """加工任务读模型。"""

    id: str
    name: str
    type: str
    state: str
    progress: int
    error: str | None = None
    config_yaml: str | None = None
    created_at: UtcDateTime
    started_at: UtcDateTime | None = None
    finished_at: UtcDateTime | None = None
    # 可复现凭证(G18);执行前 / 早期 job 为空。无数字字段,to_camel 安全。
    dj_version: str | None = None
    image_tag: str | None = None
    executor_type: str | None = None
    # 治理工场:经流水线一键执行时回指来源(pipelines.id);手工建任务为空
    pipeline_id: str | None = None
    # 产物概要：{datasetId, datasetName, versionId, versionNo, rows}
    output: dict[str, Any] | None = None
    # 输入版本概要(经 job_inputs 反查)：{datasetId, datasetName, versionId, versionNo}
    input: dict[str, Any] | None = None
    # 是否可重跑(存有原始执行规格 spec;早于重跑特性的任务为 False)
    can_rerun: bool = False
    # 原始执行规格(camelCase),供编辑器回填;仅详情端点(_item)填充,列表不带。
    # 字段名与 ORM 的 spec(snake_case 原始存储)错开,避免 from_attributes 自动
    # 把 snake 键的原始 dict 带进所有列表响应。
    edit_spec: dict[str, Any] | None = None
    # 非致命告警(0069 新列,list[str] 语义):字段名与 ORM 同名,from_attributes
    # 自动带出,详情/列表均可见。无数字字段,to_camel 安全(不触发 title() 数字坑)。
    warnings: list[str] | None = None

    # 以下三个控制位由 state 派生,供「数据任务」统一控制台按状态渲染操作按钮。
    # computed_field + to_camel 别名 → 序列化为 canPause / canResume / canStop,
    # 任何构建 JobRead 的端点(list/detail/per-type)都自动带上,无需逐处手填。
    @computed_field
    @property
    def can_pause(self) -> bool:
        """可暂停:运行中或排队中。"""
        return self.state in ("pending", "running")

    @computed_field
    @property
    def can_resume(self) -> bool:
        """可继续:已暂停(继续=按 spec 从头重跑)。"""
        return self.state == "paused"

    @computed_field
    @property
    def can_stop(self) -> bool:
        """可停止:运行中/排队中/已暂停。"""
        return self.state in ("pending", "running", "paused")
