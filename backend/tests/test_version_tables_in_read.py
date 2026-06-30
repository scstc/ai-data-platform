import io

import pytest


@pytest.mark.asyncio
async def test_detail_exposes_table_members(client):
    ds = (await client.post("/api/v1/datasets", json={"name": "成员读"})).json()[
        "data"
    ]["id"]
    for tbl in ("users", "orders"):
        files = {"file": (f"{tbl}.jsonl", io.BytesIO(b'{"x":1}\n'), "application/x-ndjson")}
        r = await client.post(
            "/api/v1/datasets/upload", files=files, data={"datasetId": ds}
        )
        assert r.status_code == 200, r.text
    detail = (await client.get(f"/api/v1/datasets/{ds}")).json()["data"]
    # 两次上传 → 同一 draft 版本两个表成员
    assert len(detail["versions"]) == 1
    tables = detail["versions"][0]["tables"]
    assert {t["tableName"] for t in tables} == {"users", "orders"}
    # 每个成员带 storageUri/format
    assert all(t["format"] in ("jsonl", "parquet") for t in tables)
    assert all(t["storageUri"].startswith("s3://") for t in tables)


@pytest.mark.asyncio
async def test_single_upload_one_member(client):
    ds = (await client.post("/api/v1/datasets", json={"name": "单成员读"})).json()[
        "data"
    ]["id"]
    files = {"file": ("data.jsonl", io.BytesIO(b'{"a":1}\n{"a":2}\n'), "application/x-ndjson")}
    await client.post("/api/v1/datasets/upload", files=files, data={"datasetId": ds})
    detail = (await client.get(f"/api/v1/datasets/{ds}")).json()["data"]
    tables = detail["versions"][0]["tables"]
    assert len(tables) == 1
    assert tables[0]["tableName"] == "data"
