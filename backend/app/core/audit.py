"""操作审计中间件:对 /api/v1 的写请求,响应后落一条 audit_logs。

设计要点(docs/plan/06 §2.4):
- 只对 method ∈ {POST,PUT,PATCH,DELETE} 且 path 以 /api/v1 开头的请求记录;
  读请求 / OPTIONS 预检不记(天然按 method 过滤,不影响 CORS)。
- 绝不消费 request body(只读 cookie/method/path/url + response.status_code),
  避免破坏请求流。
- username 由 adp_session cookie 解析(失败记 "anonymous")。
- action 由资源段 + method 推导(如 DELETE /api/v1/datasets/xx → "dataset.delete");
  子动作路径取末段语义(POST /dataset-versions/xx/publish → "datasetVersion.publish")。
- target 取被操作对象的 id;target_name 按资源类型反查名称快照
  (DELETE 在请求前解析——响应后行已删;其余响应后解析)。
- ip 取 X-Forwarded-For 首跳(nginx 代理场景),无代理时取对端地址。
- 用独立会话写入并 commit,整段 try/except 吞掉异常——审计失败绝不影响主请求。
"""

from __future__ import annotations

import secrets

from starlette.requests import Request
from starlette.responses import Response

from app.core.db import async_session_factory
from app.models.audit_log import AuditLog
from app.models.category import Category
from app.models.data_lake import DataLake
from app.models.dataset import Dataset
from app.models.dataset_version import DatasetVersion
from app.models.datasource import DataSource
from app.models.department import Department
from app.models.ingest_task import IngestTask
from app.models.job import Job
from app.models.menu import Menu
from app.models.pipeline import Pipeline
from app.models.role import Role
from app.models.tag import Tag
from app.models.user import User
from app.services.auth import parse_token

_AUDIT_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_API_PREFIX = "/api/v1/"

# method → 动作动词
_VERB = {
    "POST": "create",
    "PUT": "update",
    "PATCH": "update",
    "DELETE": "delete",
}

# 资源段(复数)→ 动作前缀(单数语义)
_RESOURCE_ALIAS = {
    "datasets": "dataset",
    "datasources": "datasource",
    "dataset-versions": "datasetVersion",
    "data-lakes": "dataLake",
    "ingest-tasks": "ingestTask",
    "jobs": "job",
    "pipelines": "pipeline",
    "operators": "operator",
    "uploads": "upload",
    "tags": "tag",
    "categories": "category",
    "content-safety": "reviewJob",
    "system/users": "user",
    "system/roles": "role",
    "system/menus": "menu",
    "system/depts": "dept",
    "system/permissions": "permission",
}

# 资源段 → (模型, 名称属性):target id 反查对象名称用
_NAME_LOOKUP: dict[str, tuple[type, str]] = {
    "datasets": (Dataset, "name"),
    "datasources": (DataSource, "name"),
    "data-lakes": (DataLake, "name"),
    "ingest-tasks": (IngestTask, "name"),
    "jobs": (Job, "name"),
    "pipelines": (Pipeline, "name"),
    "tags": (Tag, "name"),
    "categories": (Category, "name"),
    "system/users": (User, "username"),
    "system/roles": (Role, "name"),
    "system/menus": (Menu, "name"),
    "system/depts": (Department, "name"),
}


def _resource_and_target(path: str) -> tuple[str, str | None]:
    """从 /api/v1 之后的路径段推导资源名与目标 id。

    取 /api/v1/ 之后的第一段作资源(system 子路由取前两段,如 "system/users",
    否则 DELETE /system/users/{id} 会丢掉真实 id)。target 取「被操作对象的 id」:
    - /resource(无 id,如 POST /api/v1/datasets)→ None
    - /resource/{id}(如 DELETE /api/v1/datasets/xx)→ id
    - /resource/{id}/{action}(如 POST /api/v1/dataset-versions/xx/verdict)→ {id}
      (动作语义已落在 action 字段;target 记 id 才能审计"操作了哪个对象")
    """
    rest = path[len(_API_PREFIX):].strip("/")
    segments = [s for s in rest.split("/") if s]
    if not segments:
        return "api", None
    if segments[0] == "system" and len(segments) >= 2:
        resource = f"system/{segments[1]}"
        segments = segments[1:]
    else:
        resource = segments[0]
    if len(segments) >= 3:
        target = segments[1]
    else:
        target = segments[-1] if len(segments) > 1 else None
    return resource, target


def _sub_action(path: str) -> str | None:
    """路径末段若是动作词(纯字母,如 publish/verdict/acl)则返回之,否则 None。

    id 段(ds-xxx / uuid / 数字)含数字或连字符,借此与动作词区分。
    """
    rest = path[len(_API_PREFIX):].strip("/")
    segments = [s for s in rest.split("/") if s]
    if segments and segments[0] == "system":
        segments = segments[1:]
    if len(segments) >= 3 and segments[-1].isalpha():
        return segments[-1]
    return None


def _derive_action(method: str, path: str, resource: str) -> str:
    """资源段 + method → 动作语义,如 "dataset.delete"。

    子动作路径用末段语义(如 "datasetVersion.publish"),比笼统的
    create/update 更能回答"某个人执行了什么动作"。
    """
    name = _RESOURCE_ALIAS.get(resource, resource)
    verb = _sub_action(path) or _VERB.get(method, method.lower())
    return f"{name}.{verb}"


def _client_ip(request: Request) -> str | None:
    """客户端 IP:优先 X-Forwarded-For 首跳(nginx 代理),否则对端地址。"""
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip() or None
    return request.client.host if request.client else None


async def _lookup_target_name(resource: str, target: str | None) -> str | None:
    """按资源类型把 target id 反查成对象名称;未知资源/查不到返回 None。"""
    if not target:
        return None
    if resource == "dataset-versions":
        # 版本本身无名称:拼「数据集名 v版本号」
        async with async_session_factory() as session:
            version = await session.get(DatasetVersion, target)
            if version is None:
                return None
            dataset = await session.get(Dataset, version.dataset_id)
            label = f"v{version.version_no}"
            return f"{dataset.name} {label}" if dataset else label
    entry = _NAME_LOOKUP.get(resource)
    if entry is None:
        return None
    model, attr = entry
    async with async_session_factory() as session:
        obj = await session.get(model, target)
        return getattr(obj, attr) if obj else None


async def audit_middleware(request: Request, call_next) -> Response:
    """HTTP 中间件:放行主请求,响应后对写请求异步写审计(失败静默)。"""
    method = request.method
    path = request.url.path
    should_audit = method in _AUDIT_METHODS and path.startswith(_API_PREFIX)

    # DELETE 的对象名必须在请求前解析——响应后行已删,查不到了
    target_name: str | None = None
    if should_audit and method == "DELETE":
        try:
            resource, target = _resource_and_target(path)
            target_name = await _lookup_target_name(resource, target)
        except Exception:  # noqa: BLE001 审计失败绝不能影响主请求
            pass

    response = await call_next(request)

    if should_audit:
        # 审计写入与主请求解耦:整段吞异常,任何失败都不影响已生成的响应。
        try:
            token = request.cookies.get("adp_session")
            username = (parse_token(token) if token else None) or "anonymous"
            resource, target = _resource_and_target(path)
            action = _derive_action(method, path, resource)
            if target_name is None and method != "DELETE":
                target_name = await _lookup_target_name(resource, target)
            async with async_session_factory() as session:
                session.add(
                    AuditLog(
                        id=f"aud-{secrets.token_hex(3)}",
                        username=username,
                        action=action,
                        method=method,
                        path=path,
                        target=target,
                        target_name=target_name,
                        ip=_client_ip(request),
                        status_code=response.status_code,
                    )
                )
                await session.commit()
        except Exception:  # noqa: BLE001 审计失败绝不能影响主请求
            pass

    return response
