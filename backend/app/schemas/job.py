"""加工任务(Job)相关 schema。"""

from __future__ import annotations

from typing import Any

from pydantic import computed_field

from app.schemas.common import CamelModel, UtcDateTime


class OperatorSpec(CamelModel):
    """编排里的一个算子:名称 + 参数。"""

    name: str
    params: dict[str, Any] | None = None


class JobCreate(CamelModel):
    """新建加工任务入参:对某个数据集版本跑一串算子。"""

    name: str
    type: str = "process"
    dataset_version_id: str
    operators: list[OperatorSpec]
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
    operators: list[OperatorSpec]


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
    # 产物概要：{datasetId, datasetName, versionId, versionNo, rows}
    output: dict[str, Any] | None = None
    # 输入版本概要(经 job_inputs 反查)：{datasetId, datasetName, versionId, versionNo}
    input: dict[str, Any] | None = None
    # 是否可重跑(存有原始执行规格 spec;早于重跑特性的任务为 False)
    can_rerun: bool = False

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
