"""数据源路由完整流程测试。

覆盖：建→列表可见→筛选(name/type)→改→create 的 connected/pending 两分支→
test 接口成功/失败两分支→删→删后 404。
"""

from __future__ import annotations

import pytest
import pytest_asyncio
from httpx import AsyncClient

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture(autouse=True)
async def _admin_session(client: AsyncClient, seed_users: None) -> None:
    """数据源写端点已加 require_admin 门控:本模块用例统一以 admin 身份请求。

    种子用户由 seed_users 注入,这里给共享 client 挂上 admin 签名令牌 cookie。
    """
    from app.services.auth import sign_token

    client.cookies.set("adp_session", sign_token("admin"))

# 一组字段齐全但不可路由的 s3 config(s3 改真探活后,默认走 failed 分支且快速失败)。
# endpoint 指向本地不可路由端口,确保 create/list 等用例不依赖外网、不挂起。
_VALID_S3_CONFIG = {
    "endpoint": "http://127.0.0.1:1",
    "bucket": "test-bucket",
    "accessKey": "AKIATEST",
    "secretKey": "secret",
}


async def _create(client: AsyncClient, **overrides: object) -> dict:
    """便捷新建一个 s3 数据源并返回 data 体。"""
    payload = {
        "name": "测试对象存储",
        "type": "s3",
        "config": dict(_VALID_S3_CONFIG),
        "description": "用于测试",
    }
    payload.update(overrides)
    resp = await client.post("/api/v1/datasources", json=payload)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    return body["data"]


async def test_create_s3_real_probe_branch(client: AsyncClient) -> None:
    """s3 改真探活后:不可达的假 endpoint → status=failed,但记录仍建成、camelCase。

    (#18:s3 分支从配置校验升级为 external_store.test_connection 真连。)
    """
    data = await _create(client)
    assert data["id"].startswith("ds-")
    assert data["status"] == "failed"
    assert data["type"] == "s3"
    assert data["creator"] == "admin"
    # camelCase 字段存在
    assert "createdAt" in data
    assert "updatedAt" in data


async def test_create_pending_branch(client: AsyncClient) -> None:
    """非真探活类型(hdfs)config 缺必填字段 → 初始 status=pending。"""
    data = await _create(
        client,
        name="缺字段的库",
        type="hdfs",
        config={"nameNode": "only-namenode"},
    )
    assert data["status"] == "pending"


async def test_create_database_dbkind_roundtrip(client: AsyncClient) -> None:
    """database 类型齐全 → connected，且 dbKind 原样回显。"""
    data = await _create(
        client,
        name="达梦库",
        type="database",
        dbKind="dameng",
        config={
            "host": "10.0.0.1",
            "port": 5236,
            "database": "BIZ",
            "username": "SYSDBA",
            "password": "pw",
        },
    )
    assert data["status"] == "connected"
    assert data["dbKind"] == "dameng"


async def test_list_and_filters(client: AsyncClient) -> None:
    """建多条 → 列表可见 → name 模糊 / type 精确筛选生效。"""
    await _create(client, name="对象存储甲")
    await _create(client, name="对象存储乙")
    await _create(
        client,
        name="数据库丙",
        type="database",
        dbKind="hive",
        config={
            "host": "h",
            "port": 10000,
            "database": "ods",
            "username": "u",
            "password": "p",
        },
    )

    # 全量列表
    resp = await client.get(
        "/api/v1/datasources", params={"current": 1, "pageSize": 10}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["total"] == 3
    assert len(body["data"]) == 3

    # name 模糊匹配
    resp = await client.get("/api/v1/datasources", params={"name": "对象存储"})
    body = resp.json()
    assert body["total"] == 2
    assert all("对象存储" in d["name"] for d in body["data"])

    # type 精确匹配
    resp = await client.get("/api/v1/datasources", params={"type": "database"})
    body = resp.json()
    assert body["total"] == 1
    assert body["data"][0]["name"] == "数据库丙"


async def test_pagination(client: AsyncClient) -> None:
    """分页 offset/limit 正确：第二页只剩 1 条。"""
    for i in range(3):
        await _create(client, name=f"分页-{i}")
    resp = await client.get(
        "/api/v1/datasources", params={"current": 2, "pageSize": 2}
    )
    body = resp.json()
    assert body["total"] == 3
    assert len(body["data"]) == 1


async def test_update(client: AsyncClient) -> None:
    """更新 name/status，仅改传入字段，其余保持。"""
    data = await _create(client)
    ds_id = data["id"]
    resp = await client.put(
        f"/api/v1/datasources/{ds_id}",
        json={"name": "改名后", "status": "failed"},
    )
    assert resp.status_code == 200
    updated = resp.json()["data"]
    assert updated["name"] == "改名后"
    assert updated["status"] == "failed"
    # 未传的字段保持原值
    assert updated["type"] == "s3"
    assert updated["description"] == "用于测试"


async def test_update_reprobes_status_when_status_omitted(
    client: AsyncClient,
) -> None:
    """未显式传 status 的更新会按最新 config 重新定状态。

    WHY:历史上 status 仅在 create 时定一次,「测试连接已通但列表仍显示陈旧失败」
    就是因为编辑保存从不刷新。补齐 config 后保存(不传 status)→ pending 应纠为
    connected,证明保存即校准。
    """
    data = await _create(
        client,
        name="HDFS补字段",
        type="hdfs",
        config={"nameNode": "hdfs://nn:8020"},  # 缺 path → pending
    )
    assert data["status"] == "pending"
    ds_id = data["id"]

    resp = await client.put(
        f"/api/v1/datasources/{ds_id}",
        json={"config": {"nameNode": "hdfs://nn:8020", "path": "/data"}},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "connected"


async def test_recheck_refreshes_stale_status(client: AsyncClient) -> None:
    """重新检测按存储 config 真探一次并回写状态,纠正陈旧快照。

    WHY:选项3 给列表一个显式「重新检测」入口。先把一个 connected 源人为置为
    陈旧 failed(模拟历史快照),recheck 后应按当前 config 纠回 connected。
    """
    data = await _create(
        client,
        name="HDFS校准",
        type="hdfs",
        config={"nameNode": "hdfs://nn:8020", "path": "/data"},
    )
    assert data["status"] == "connected"
    ds_id = data["id"]

    # 显式置陈旧 failed(显式 status 被尊重,不触发重探)
    resp = await client.put(
        f"/api/v1/datasources/{ds_id}", json={"status": "failed"}
    )
    assert resp.json()["data"]["status"] == "failed"

    # 重新检测:按存储 config 重探 → connected
    resp = await client.post(f"/api/v1/datasources/{ds_id}/recheck")
    assert resp.status_code == 200
    assert resp.json()["data"]["status"] == "connected"


async def test_recheck_not_found(client: AsyncClient) -> None:
    """对不存在的数据源重新检测 → 404 + {success:false}。"""
    resp = await client.post("/api/v1/datasources/ds-nope00/recheck")
    assert resp.status_code == 404
    assert resp.json()["success"] is False


async def test_update_not_found(client: AsyncClient) -> None:
    """更新不存在的数据源 → 404 + {success:false, message}。"""
    resp = await client.put(
        "/api/v1/datasources/ds-nope00", json={"name": "x"}
    )
    assert resp.status_code == 404
    body = resp.json()
    assert body["success"] is False
    assert isinstance(body["message"], str)


async def test_test_connection_success(client: AsyncClient) -> None:
    """test 接口(api 类型):PushConnector 本地永远就绪 → 裸响应 success=true。

    (§4.8:删除 randint 假成功;api 推送连接器 probe 返回真实就绪态,latency=0。)
    """
    resp = await client.post(
        "/api/v1/datasources/test",
        json={"type": "api", "config": {"url": "https://example.com/api"}},
    )
    assert resp.status_code == 200
    body = resp.json()
    # 端点返回裸 TestConnectionResult(无 data 信封)
    assert body["success"] is True
    assert body["latencyMs"] == 0
    assert isinstance(body["message"], str)


async def test_test_connection_s3_real_probe_unreachable(
    client: AsyncClient,
) -> None:
    """s3 真探活:不可达 endpoint → 裸响应 success=false(#18,不 500)。"""
    resp = await client.post(
        "/api/v1/datasources/test",
        json={"type": "s3", "config": dict(_VALID_S3_CONFIG)},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False
    assert isinstance(body["message"], str)


async def test_test_connection_failure(client: AsyncClient) -> None:
    """test 接口:config 缺字段 → 裸响应 success=false。"""
    resp = await client.post(
        "/api/v1/datasources/test",
        json={"type": "database", "config": {"host": "h"}},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is False


async def test_delete_and_then_404(client: AsyncClient) -> None:
    """删除 → {success:true}；再次删除 / 更新 → 404。"""
    data = await _create(client)
    ds_id = data["id"]

    resp = await client.delete(f"/api/v1/datasources/{ds_id}")
    assert resp.status_code == 200
    assert resp.json()["success"] is True

    # 列表中不再可见
    resp = await client.get("/api/v1/datasources")
    assert resp.json()["total"] == 0

    # 再删一次 → 404
    resp = await client.delete(f"/api/v1/datasources/{ds_id}")
    assert resp.status_code == 404
    assert resp.json()["success"] is False


# ===== 下载 / 导出 S3(与数据集版本 download/export-s3 行为对齐)=====
# 仅验类型守卫(404/400)+ 入参校验;真实 s3 联调走手动冒烟(本仓库 test s3 endpoint
# 是 127.0.0.1:1 假端点 → list_objects 必失败,不在单测里硬连)。失败回因亦由路由
# 兜底(列对象失败 → 400),不依赖外部网络。


async def test_download_not_found(client: AsyncClient) -> None:
    """download on 不存在的 id → 404 + 标准 success/message 信封。"""
    resp = await client.get("/api/v1/datasources/ds-nothing/download")
    assert resp.status_code == 404
    body = resp.json()
    assert body["success"] is False
    assert "数据源不存在" in body["message"]


async def test_download_rejects_non_s3(client: AsyncClient) -> None:
    """download on database 类型 → 400 + 类型不支持文案(按钮常驻策略后端兜底)。"""
    data = await _create(
        client,
        name="PG-下载测",
        type="database",
        dbKind="postgresql",
        config={
            "host": "10.0.0.1",
            "port": 5432,
            "database": "x",
            "username": "u",
            "password": "p",
        },
    )
    resp = await client.get(f"/api/v1/datasources/{data['id']}/download")
    assert resp.status_code == 400
    body = resp.json()
    assert body["success"] is False
    assert "暂不支持" in body["message"]
    assert "database" in body["message"]


async def test_export_s3_rejects_non_s3(client: AsyncClient) -> None:
    """export-s3 类型守卫:database → 400 类型不支持;s3 但无可用源端点
    (测试假 127.0.0.1:1)→ 400 列对象失败(目标 datasource 校验在 list 之后,
    与版本实现保持一致——先看源能不能读,再看目标合不合法)。"""
    data = await _create(
        client,
        name="PG-导出测",
        type="database",
        dbKind="postgresql",
        config={
            "host": "10.0.0.1",
            "port": 5432,
            "database": "x",
            "username": "u",
            "password": "p",
        },
    )
    resp = await client.post(
        f"/api/v1/datasources/{data['id']}/export-s3",
        json={"datasourceId": "ds-anything", "bucket": "any"},
    )
    assert resp.status_code == 400
    assert "暂不支持" in resp.json()["message"]

    # s3 类型:源 list_objects 必失败(假端点)→ 400 兜底
    s3 = await _create(client, name="假 s3", type="s3", config=dict(_VALID_S3_CONFIG))
    resp = await client.post(
        f"/api/v1/datasources/{s3['id']}/export-s3",
        json={"datasourceId": "ds-anything", "bucket": "any"},
    )
    assert resp.status_code == 400
    assert "列对象失败" in resp.json()["message"]
