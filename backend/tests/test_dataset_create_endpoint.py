import pytest


@pytest.mark.asyncio
async def test_create_empty_dataset(client):
    resp = await client.post("/api/v1/datasets", json={
        "name": "我的训练集", "dataType": "sql", "semanticType": "structured",
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["data"]["name"] == "我的训练集"
    assert body["data"]["versions"] == []   # 空数据集无版本
    assert body["data"]["id"].startswith("dset-")


@pytest.mark.asyncio
async def test_create_dataset_with_tags_and_train_type(client):
    resp = await client.post("/api/v1/datasets", json={
        "name": "带标签集", "trainType": "sft", "schemaVariant": "alpaca",
        "tags": ["金融", "结构化"],
    })
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert set(data["tags"]) == {"金融", "结构化"}
    assert data["trainType"] == "sft"
    assert data["schemaVariant"] == "alpaca"


@pytest.mark.asyncio
async def test_create_dataset_rejects_bad_semantic_type(client):
    resp = await client.post("/api/v1/datasets", json={
        "name": "非法语义", "semanticType": "not-a-real-type",
    })
    assert resp.status_code == 422
