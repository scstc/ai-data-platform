"""操作审计中间件:对 /api/v1 的写请求,响应后落一条 audit_logs。

设计要点(docs/plan/06 §2.4):
- 只对 method ∈ {POST,PUT,PATCH,DELETE} 且 path 以 /api/v1 开头的请求记录;
  读请求 / OPTIONS 预检不记(天然按 method 过滤,不影响 CORS)。
- 绝不消费 request body(只读 cookie/method/path/url + response.status_code),
  避免破坏请求流。
- username 由 adp_session cookie 解析(失败记 "anonymous")。
- action 由资源段 + method 推导(如 DELETE /api/v1/datasets/xx → "dataset.delete")。
- target 取路径末段(形如 id 时)。
- 用独立会话写入并 commit,整段 try/except 吞掉异常——审计失败绝不影响主请求。
"""

from __future__ import annotations

import secrets

from starlette.requests import Request
from starlette.responses import Response

from app.core.db import async_session_factory
from app.models.audit_log import AuditLog
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
    "ingest-tasks": "ingestTask",
    "jobs": "job",
    "operators": "operator",
    "uploads": "upload",
}


def _resource_and_target(path: str) -> tuple[str, str | None]:
    """从 /api/v1 之后的路径段推导资源名与目标末段。

    取 /api/v1/ 之后的第一段作资源。target 取「被操作对象的 id」:
    - /resource(无 id,如 POST /api/v1/datasets)→ None
    - /resource/{id}(如 DELETE /api/v1/datasets/xx)→ id
    - /resource/{id}/{action}(如 POST /api/v1/dataset-versions/xx/verdict)→ {id}
      (动作语义已落在 action 字段;target 记 id 才能审计"操作了哪个对象")
    """
    rest = path[len(_API_PREFIX):].strip("/")
    segments = [s for s in rest.split("/") if s]
    if not segments:
        return "api", None
    resource = segments[0]
    if len(segments) >= 3:
        target = segments[1]
    else:
        target = segments[-1] if len(segments) > 1 else None
    return resource, target


def _derive_action(method: str, resource: str) -> str:
    """资源段 + method → 动作语义,如 "dataset.delete"。"""
    name = _RESOURCE_ALIAS.get(resource, resource)
    verb = _VERB.get(method, method.lower())
    return f"{name}.{verb}"


async def audit_middleware(request: Request, call_next) -> Response:
    """HTTP 中间件:放行主请求,响应后对写请求异步写审计(失败静默)。"""
    response = await call_next(request)

    method = request.method
    path = request.url.path
    if method in _AUDIT_METHODS and path.startswith(_API_PREFIX):
        # 审计写入与主请求解耦:整段吞异常,任何失败都不影响已生成的响应。
        try:
            token = request.cookies.get("adp_session")
            username = (parse_token(token) if token else None) or "anonymous"
            resource, target = _resource_and_target(path)
            action = _derive_action(method, resource)
            async with async_session_factory() as session:
                session.add(
                    AuditLog(
                        id=f"aud-{secrets.token_hex(3)}",
                        username=username,
                        action=action,
                        method=method,
                        path=path,
                        target=target,
                        status_code=response.status_code,
                    )
                )
                await session.commit()
        except Exception:  # noqa: BLE001 审计失败绝不能影响主请求
            pass

    return response
