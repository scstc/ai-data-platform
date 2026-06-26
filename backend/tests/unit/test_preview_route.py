"""POST /ingest-tasks/preview 路由派发测试(切片 A)。

策略:用 FAKE session + monkeypatch 采样器,免真实 DB/MinIO。
- 自建最小 FastAPI 应用,仅挂 ingest_tasks router
- override get_session 依赖,返回 _FakeSession(其 .get(DataSource, id) 返回 canned 实体或 None)
- monkeypatch 替换 app.services.preview.preview_db / preview_file(路由内惰性 from 导入,会拾取补丁)

仅锁派发与错误映射;采样器本身的纯函数逻辑由 Task 3 覆盖。
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.api.v1.ingest_tasks import router
from app.core.db import get_session
from app.models.datasource import DataSource
from app.services.connectors.base import ConnectorNotReady, IngestError


class _FakeSession:
    """最小化 async session:.get(DataSource, id) 返回字典里 canned 实体或 None。"""

    def __init__(self, by_id: dict[str, DataSource] | None = None) -> None:
        self._by_id: dict[str, DataSource] = by_id or {}

    async def get(self, model_cls: type, id_: str) -> Any:  # noqa: ANN401
        if model_cls is DataSource:
            return self._by_id.get(id_)
        return None


def _make_ds(ds_id: str, **kwargs: Any) -> DataSource:
    """构造内存 DataSource(不落库),仅用于路由派发测试。"""
    defaults: dict[str, Any] = {
        "id": ds_id,
        "name": f"ds-{ds_id}",
        "type": "database",
        "status": "connected",
        "config": {},
        "creator": "admin",
    }
    defaults.update(kwargs)
    return DataSource(**defaults)


@pytest.fixture
def fake_session() -> _FakeSession:
    """默认空 session(任何 id → 404);用例中改写 _by_id 注入数据源。"""
    return _FakeSession()


@pytest.fixture
def app(fake_session: _FakeSession) -> FastAPI:
    """最小 app:仅挂 ingest_tasks router,override get_session 返回 fake_session。"""
    f = FastAPI()
    f.include_router(router, prefix="/api/v1")

    async def _override():
        yield fake_session

    f.dependency_overrides[get_session] = _override
    return f


async def _post(app: FastAPI, body: dict) -> Any:  # noqa: ANN401
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://t") as c:
        return await c.post("/api/v1/ingest-tasks/preview", json=body)


# ---------------------------------------------------------------------------
# 1. 数据源不存在 → 404
# ---------------------------------------------------------------------------
async def test_missing_datasource_returns_404(app: FastAPI) -> None:
    resp = await _post(app, {"datasourceId": "ds-nope", "extract": {}})
    assert resp.status_code == 404
    body = resp.json()
    assert body["success"] is False
    assert "不存在" in body["message"]


# ---------------------------------------------------------------------------
# 2. 不支持的数据源类型 → 400(api / 其它)
# ---------------------------------------------------------------------------
async def test_api_type_unsupported_returns_400(
    app: FastAPI, fake_session: _FakeSession
) -> None:
    fake_session._by_id["ds-api"] = _make_ds("ds-api", type="api")
    resp = await _post(app, {"datasourceId": "ds-api", "extract": {}})
    assert resp.status_code == 400
    body = resp.json()
    assert body["success"] is False
    assert "暂不支持" in body["message"]


# ---------------------------------------------------------------------------
# 3. database 类型但 db_kind 非支持品牌 → 400
# ---------------------------------------------------------------------------
async def test_db_unsupported_kind_returns_400(
    app: FastAPI, fake_session: _FakeSession
) -> None:
    fake_session._by_id["ds-oracle"] = _make_ds(
        "ds-oracle", type="database", db_kind="oracle"
    )
    resp = await _post(app, {"datasourceId": "ds-oracle", "extract": {}})
    assert resp.status_code == 400
    assert "暂不支持" in resp.json()["message"]


# ---------------------------------------------------------------------------
# 4. PG 族 db_kind → 派发 pg._connect + preview_db → 200
# ---------------------------------------------------------------------------
async def test_db_pg_kind_dispatches_pg_connect_and_returns_200(
    app: FastAPI, fake_session: _FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_session._by_id["ds-pg"] = _make_ds(
        "ds-pg", type="database", db_kind="postgresql"
    )
    captured: dict[str, Any] = {}

    async def _fake_preview_db(connect_fn: Any, cfg: dict, extract: dict) -> dict:
        captured["connect_fn"] = connect_fn
        captured["cfg"] = cfg
        captured["extract"] = extract
        return {
            "columns": [{"name": "id", "type": "integer"}],
            "rows": [{"id": 1}],
            "truncated": False,
            "sampledFrom": "<sql>",
        }

    monkeypatch.setattr("app.services.preview.preview_db", _fake_preview_db)

    resp = await _post(
        app,
        {"datasourceId": "ds-pg", "extract": {"mode": "sql", "sql": "SELECT 1"}},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    # 响应 data 形状
    assert set(body["data"].keys()) == {"columns", "rows", "truncated", "sampledFrom"}
    assert body["data"]["columns"] == [{"name": "id", "type": "integer"}]
    assert body["data"]["rows"] == [{"id": 1}]
    assert body["data"]["truncated"] is False
    assert body["data"]["sampledFrom"] == "<sql>"
    # 派发到 pg._connect(不是 mysql._connect)
    from app.services.connectors.pg import _connect as pg_connect

    assert captured["connect_fn"] is pg_connect
    # config/extract 透传
    assert captured["extract"] == {"mode": "sql", "sql": "SELECT 1"}


# ---------------------------------------------------------------------------
# 5. goldendb → 派发 mysql._connect
# ---------------------------------------------------------------------------
async def test_db_goldendb_dispatches_mysql_connect(
    app: FastAPI, fake_session: _FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_session._by_id["ds-gd"] = _make_ds(
        "ds-gd", type="database", db_kind="goldendb"
    )
    captured: dict[str, Any] = {}

    async def _fake_preview_db(connect_fn: Any, cfg: dict, extract: dict) -> dict:
        captured["connect_fn"] = connect_fn
        return {"columns": [], "rows": [], "truncated": False, "sampledFrom": "t"}

    monkeypatch.setattr("app.services.preview.preview_db", _fake_preview_db)
    resp = await _post(app, {"datasourceId": "ds-gd", "extract": {}})
    assert resp.status_code == 200, resp.text

    from app.services.connectors.mysql import _connect as mysql_connect

    assert captured["connect_fn"] is mysql_connect


# ---------------------------------------------------------------------------
# 6. s3 → 派发 preview_file(datasource 整对象透传)→ 200
# ---------------------------------------------------------------------------
async def test_s3_dispatches_preview_file(
    app: FastAPI, fake_session: _FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_session._by_id["ds-s3"] = _make_ds(
        "ds-s3", type="s3", config={"bucket": "b"}
    )
    captured: dict[str, Any] = {}

    async def _fake_preview_file(ds: Any, extract: dict) -> dict:
        captured["ds"] = ds
        captured["extract"] = extract
        return {"columns": [], "rows": [], "truncated": False, "sampledFrom": "k"}

    monkeypatch.setattr("app.services.preview.preview_file", _fake_preview_file)
    resp = await _post(app, {"datasourceId": "ds-s3", "extract": {"paths": ["x"]}})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["sampledFrom"] == "k"
    # 整对象透传
    assert captured["ds"].id == "ds-s3"
    assert captured["extract"] == {"paths": ["x"]}


# ---------------------------------------------------------------------------
# 7. hdfs 也走 preview_file
# ---------------------------------------------------------------------------
async def test_hdfs_dispatches_preview_file(
    app: FastAPI, fake_session: _FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_session._by_id["ds-hdfs"] = _make_ds("ds-hdfs", type="hdfs")

    async def _fake_preview_file(ds: Any, extract: dict) -> dict:
        return {"columns": [], "rows": [], "truncated": False, "sampledFrom": "p"}

    monkeypatch.setattr("app.services.preview.preview_file", _fake_preview_file)
    resp = await _post(app, {"datasourceId": "ds-hdfs", "extract": {}})
    assert resp.status_code == 200, resp.text


# ---------------------------------------------------------------------------
# 8. 三类异常 → 400 + message
# ---------------------------------------------------------------------------
async def test_ingest_error_maps_to_400(
    app: FastAPI, fake_session: _FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """IngestError(典型:未配置采集对象)→ 400。"""
    fake_session._by_id["ds-pg"] = _make_ds(
        "ds-pg", type="database", db_kind="postgresql"
    )

    async def _raise(*a: Any, **k: Any) -> None:
        raise IngestError("未配置采集对象")

    monkeypatch.setattr("app.services.preview.preview_db", _raise)
    resp = await _post(app, {"datasourceId": "ds-pg", "extract": {}})
    assert resp.status_code == 400
    body = resp.json()
    assert body["success"] is False
    assert "未配置采集对象" in body["message"]


async def test_value_error_maps_to_400(
    app: FastAPI, fake_session: _FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ValueError(典型:文件格式不支持)→ 400。"""
    fake_session._by_id["ds-s3"] = _make_ds("ds-s3", type="s3")

    async def _raise(*a: Any, **k: Any) -> None:
        raise ValueError("暂不支持预览的文件格式:xml")

    monkeypatch.setattr("app.services.preview.preview_file", _raise)
    resp = await _post(app, {"datasourceId": "ds-s3", "extract": {}})
    assert resp.status_code == 400
    assert "xml" in resp.json()["message"]


async def test_connector_not_ready_maps_to_400(
    app: FastAPI, fake_session: _FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ConnectorNotReady(典型:缺驱动/无集群)→ 400。"""
    fake_session._by_id["ds-hdfs"] = _make_ds("ds-hdfs", type="hdfs")

    async def _raise(*a: Any, **k: Any) -> None:
        raise ConnectorNotReady("hdfs 未配置 namenode")

    monkeypatch.setattr("app.services.preview.preview_file", _raise)
    resp = await _post(app, {"datasourceId": "ds-hdfs", "extract": {}})
    assert resp.status_code == 400
    assert "namenode" in resp.json()["message"]


async def test_preview_s3_external_store_error_maps_400(
    app: FastAPI, fake_session: _FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """S3 传输层错误(MinIO 不可达等)→ 400,不泄漏 500(诚实失败)。

    ExternalStoreError 由 list_objects / download_to_temp 等抛出(MinIO 连不上、
    桶缺失、凭证错误);它不是 IngestError/ValueError 的子类,未补进 except 链时
    会冒成 FastAPI 500。本测试锁定该回归。
    """
    from app.services.external_store import ExternalStoreError

    fake_session._by_id["ds-s3"] = _make_ds("ds-s3", type="s3")

    async def _raise(*a: Any, **k: Any) -> None:
        raise ExternalStoreError("MinIO 不可达")

    monkeypatch.setattr("app.services.preview.preview_file", _raise)
    resp = await _post(app, {"datasourceId": "ds-s3", "extract": {}})
    assert resp.status_code == 400
    body = resp.json()
    assert body["success"] is False
    assert "MinIO 不可达" in body["message"]


# ---------------------------------------------------------------------------
# 9. 兼容蛇形 datasource_id
# ---------------------------------------------------------------------------
async def test_snake_case_datasource_id_accepted(
    app: FastAPI, fake_session: _FakeSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """payload 也接受 datasource_id(蛇形)。"""
    fake_session._by_id["ds-pg"] = _make_ds(
        "ds-pg", type="database", db_kind="postgresql"
    )

    async def _fake(*a: Any, **k: Any) -> dict:
        return {"columns": [], "rows": [], "truncated": False, "sampledFrom": "x"}

    monkeypatch.setattr("app.services.preview.preview_db", _fake)
    resp = await _post(
        app,
        {"datasource_id": "ds-pg", "extract": {"mode": "sql", "sql": "SELECT 1"}},
    )
    assert resp.status_code == 200, resp.text
