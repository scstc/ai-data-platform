"""通用 schema 基类与分页响应。

约定：
- JSON 输出一律 camelCase（alias_generator=to_camel），但同时允许按字段名填充
  （populate_by_name=True），便于内部用 snake_case 构造。
- from_attributes=True 让 schema 能直接从 ORM 对象读取。
- 输出响应时务必 model_dump(by_alias=True) / response_model + by_alias。
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, PlainSerializer
from pydantic.alias_generators import to_camel


def _serialize_utc(value: datetime) -> str:
    """把时间戳统一序列化为带 ``Z`` 的 UTC ISO-8601。

    库内时间列均为 naive UTC(应用侧 ``datetime.now(UTC)`` / PG ``now()``);
    naive 一律按 UTC 解读再标 ``Z``,让前端能明确按 UTC 解析并转本地时区(北京)展示。
    """
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


# 对外读模型的时间戳字段统一标注它:运行期仍是 datetime,仅 JSON 输出带 Z 的 UTC。
UtcDateTime = Annotated[datetime, PlainSerializer(_serialize_utc, return_type=str)]


def format_version_label(version_no: int, created_at: datetime) -> str:
    """版本展示标签:``v{年}.{月}.{日} (#{内部版本号})``,如 ``v2026.6.16 (#5)``。

    日期取版本创建时间,不补零;括号内为不可变的内部递增版本号,用于消歧
    同一天的多个版本并保证唯一可定位。
    """
    return f"v{created_at.year}.{created_at.month}.{created_at.day} (#{version_no})"


class CamelModel(BaseModel):
    """所有对外 schema 的基类：snake_case 字段 ⇄ camelCase JSON。"""

    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        from_attributes=True,
    )


class PageResponse[T](CamelModel):
    """分页响应：{data:[...], total:int, success:true}。"""

    data: list[T]
    total: int
    success: bool = True
