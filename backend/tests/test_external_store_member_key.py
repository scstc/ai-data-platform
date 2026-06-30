import app.services.external_store as es


def test_member_key_scheme(monkeypatch):
    captured = {}

    async def fake_upload_object(cfg, bucket, key, *a, **k):
        captured["bucket"] = bucket
        captured["key"] = key

    monkeypatch.setattr(es, "upload_object", fake_upload_object)
    monkeypatch.setattr(es, "platform_config", lambda: object())

    import asyncio
    uri = asyncio.run(es.upload_parquet_member("dset-abc", 2, "orders", b"x"))
    assert captured["key"] == "dset-abc/v2/orders.parquet"
    assert uri == f"s3://{es.settings.storage_minio_upload_bucket}/dset-abc/v2/orders.parquet"
