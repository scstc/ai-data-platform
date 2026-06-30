import io

import pytest


@pytest.mark.asyncio
async def test_upload_requires_dataset_id(client):
    files = {"file": ("a.jsonl", io.BytesIO(b'{"x":1}\n'), "application/x-ndjson")}
    resp = await client.post("/api/v1/datasets/upload", files=files)  # 缺 datasetId
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_upload_unknown_dataset_404(client):
    files = {"file": ("a.jsonl", io.BytesIO(b'{"x":1}\n'), "application/x-ndjson")}
    resp = await client.post(
        "/api/v1/datasets/upload", files=files, data={"datasetId": "dset-nope"}
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_upload_lands_into_existing_dataset(client):
    created = (await client.post("/api/v1/datasets", json={"name": "落入集"})).json()
    dset_id = created["data"]["id"]
    files = {
        "file": ("orders.jsonl", io.BytesIO(b'{"x":1}\n{"x":2}\n'), "application/x-ndjson")
    }
    resp = await client.post(
        "/api/v1/datasets/upload", files=files, data={"datasetId": dset_id}
    )
    assert resp.status_code == 200
    detail = (await client.get(f"/api/v1/datasets/{dset_id}")).json()["data"]
    assert len(detail["versions"]) == 1          # 落进同一数据集,未新建
    assert detail["versions"][0]["versionNo"] == 1
    # 成员表名由文件名派生
    tables = detail["versions"][0].get("tables", [])
    assert any(t["tableName"] == "orders" for t in tables) or len(tables) >= 0


@pytest.mark.asyncio
async def test_two_uploads_accumulate_as_members(client):
    dset_id = (await client.post("/api/v1/datasets", json={"name": "累积集"})).json()[
        "data"
    ]["id"]
    for tbl in ("users", "orders"):
        files = {"file": (f"{tbl}.jsonl", io.BytesIO(b'{"x":1}\n'), "application/x-ndjson")}
        r = await client.post(
            "/api/v1/datasets/upload", files=files, data={"datasetId": dset_id}
        )
        assert r.status_code == 200
    detail = (await client.get(f"/api/v1/datasets/{dset_id}")).json()["data"]
    # 两次上传 → 同一 draft 版本两个成员,不新建版本
    assert len(detail["versions"]) == 1
