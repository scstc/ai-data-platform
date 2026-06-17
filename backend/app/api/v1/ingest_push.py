"""API 推送入站端点(数据接入重构 §4.7 / §7)。

两个端点:
- ``POST /api/v1/ingest/push/{token}`` —— **无登录态**,token 即凭证。外部系统按
  数据源创建时回填的 ``config.url`` 推送数据。请求体支持:
    * JSON 对象 ``{"records": [...], "semanticType"?, "idempotencyKey"?}``;
    * JSON 数组 ``[{...}, {...}]``(裸记录数组,等价 records);
    * 裸 jsonl(``Content-Type: application/x-ndjson`` 或纯文本逐行 JSON)。
  归并到该数据源绑定的同一 dataset 产新版本(land_push_records);
  坏 token → 401;每 token 简单速率限制超限 → 429。
- ``POST /api/v1/datasources/{id}/rotate-push-token`` —— require_admin,轮换 token
  并回填 config.url(旧 token 立即失效)。

安全边界(§4.7):token_urlsafe(16) 是凭证强度下限;公网入站建议叠加网关层
鉴权 / IP 白名单 / mTLS。本端点不声称生产级安全。
"""

from __future__ import annotations

import json
import secrets
import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Path, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import require_admin
from app.core.db import get_session
from app.models.datasource import DataSource
from app.schemas.common import CamelModel
from app.services.connectors.push import land_push_records
from app.services.landing import LandingError

router = APIRouter(tags=["ingest-push"])

SessionDep = Annotated[AsyncSession, Depends(get_session)]

# ---------------------------------------------------------------------------
# 每 token 简单速率限制(内存令牌桶):60 次/60 秒窗口,超限 429。
# 本期内存版(单进程);多 worker / 生产化需共享存储(§11 后续增强)。
# ---------------------------------------------------------------------------
_RATE_LIMIT = 60  # 每窗口最大请求数
_RATE_WINDOW = 60.0  # 窗口秒数
_rate_state: dict[str, tuple[int, float]] = {}  # token → (计数, 窗口起始时刻)


def _rate_limited(token: str) -> bool:
    """返回 True 表示已超限(应 429);否则计数 +1 放行。"""
    now = time.monotonic()
    count, window_start = _rate_state.get(token, (0, now))
    if now - window_start >= _RATE_WINDOW:
        # 窗口已过,重置
        _rate_state[token] = (1, now)
        return False
    if count >= _RATE_LIMIT:
        return True
    _rate_state[token] = (count + 1, window_start)
    return False


def _bad_token() -> JSONResponse:
    """坏 token → 401(不泄漏数据源是否存在)。"""
    return JSONResponse(
        status_code=401,
        content={"success": False, "message": "无效的推送 token"},
    )


def _coerce_records(payload: Any) -> tuple[list[dict], str | None, str | None]:
    """把解析后的请求体收敛为 (records, semanticType, idempotencyKey)。

    支持三种形态:
    - dict 含 records:取 records + 可选 semanticType / idempotencyKey;
    - list:整体视为 records,无语义/幂等键;
    - 其他:空 records(由调用方拒绝)。
    """
    semantic_type: str | None = None
    idempotency_key: str | None = None
    if isinstance(payload, dict):
        raw = payload.get("records")
        records = raw if isinstance(raw, list) else []
        st = payload.get("semanticType") or payload.get("semantic_type")
        semantic_type = st if isinstance(st, str) else None
        ik = payload.get("idempotencyKey") or payload.get("idempotency_key")
        idempotency_key = ik if isinstance(ik, str) else None
    elif isinstance(payload, list):
        records = payload
    else:
        records = []
    # 仅保留 dict 行(裸标量行无法落地为记录)
    records = [r for r in records if isinstance(r, dict)]
    return records, semantic_type, idempotency_key


async def _parse_body(request: Request) -> Any:
    """解析请求体:优先按 JSON;失败则按 jsonl(逐行 JSON)。解析失败抛 ValueError。"""
    raw = await request.body()
    if not raw or not raw.strip():
        raise ValueError("请求体为空")
    text = raw.decode("utf-8", errors="replace").strip()
    # 先尝试整体 JSON(对象或数组)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 退回 jsonl:逐行 JSON,每行一条记录
    records: list[dict] = []
    for i, line in enumerate(text.splitlines()):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"第 {i + 1} 行不是合法 JSON:{exc}") from exc
        if isinstance(obj, dict):
            records.append(obj)
    return records


@router.post("/ingest/push/{token}")
async def push_records(
    token: Annotated[str, Path()],
    request: Request,
    session: SessionDep,
) -> JSONResponse:
    """API 推送入站:token 反查数据源 → 归并落地产新版本。

    坏 token → 401;超限 → 429;空/坏 body → 400;落地失败 → 500(诚实失败,不伪成功)。
    成功 → {data:{datasetId,versionId,versionNo,rows}, success}。
    """
    # --- 1. 限流(先于 DB 查询,防刷) ---
    if _rate_limited(token):
        return JSONResponse(
            status_code=429,
            content={"success": False, "message": "推送过于频繁,请稍后重试"},
        )

    # --- 2. token 反查数据源(type=api) ---
    ds = (
        await session.scalars(
            select(DataSource).where(
                DataSource.type == "api",
                DataSource.config["pushToken"].astext == token,
            )
        )
    ).first()
    if ds is None:
        return _bad_token()

    # --- 3. 解析请求体 ---
    try:
        payload = await _parse_body(request)
    except ValueError as exc:
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": f"请求体解析失败:{exc}"},
        )
    records, semantic_type, idempotency_key = _coerce_records(payload)
    if not records:
        return JSONResponse(
            status_code=400,
            content={
                "success": False,
                "message": "未解析到任何记录(records 为空或非对象行)",
            },
        )

    # --- 4. 归并落地 ---
    try:
        version = await land_push_records(
            session,
            ds,
            records,
            semantic_type=semantic_type,
            idempotency_key=idempotency_key,
        )
    except LandingError as exc:
        return JSONResponse(
            status_code=500,
            content={"success": False, "message": f"推送落地失败:{exc}"},
        )

    return JSONResponse(
        content={
            "data": {
                "datasetId": version.dataset_id,
                "versionId": version.id,
                "versionNo": version.version_no,
                "rows": version.rows,
            },
            "success": True,
        }
    )


class _RotateTokenResponse(CamelModel):
    """轮换 token 响应 data 体。"""

    push_token: str
    url: str


@router.post(
    "/datasources/{ds_id}/rotate-push-token",
    dependencies=[Depends(require_admin)],
)
async def rotate_push_token(
    ds_id: str,
    request: Request,
    session: SessionDep,
) -> JSONResponse:
    """轮换 API 数据源的推送 token(旧 token 立即失效)并回填 config.url。"""
    ds = await session.get(DataSource, ds_id)
    if ds is None:
        return JSONResponse(
            status_code=404,
            content={"success": False, "message": "数据源不存在"},
        )
    if ds.type != "api":
        return JSONResponse(
            status_code=400,
            content={"success": False, "message": "仅 API 推送数据源可轮换 token"},
        )

    token = secrets.token_urlsafe(16)
    base = str(request.base_url).rstrip("/")
    url = f"{base}/api/v1/ingest/push/{token}"
    # JSONB 整体替换才触发脏检测
    new_cfg = dict(ds.config or {})
    new_cfg["pushToken"] = token
    new_cfg["url"] = url
    ds.config = new_cfg
    await session.commit()

    return JSONResponse(
        content={
            "data": {"pushToken": token, "url": url},
            "success": True,
        }
    )
