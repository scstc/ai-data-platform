"""preview_version 对 parquet 格式 s3:// 版本走 DuckDB 分支的集成测试。

测试策略:monkeypatch `_duck_query`、`_version_storage_cfg`、`s3_settings_for_duckdb`
避免真实 MinIO 连接;断言 parquet 分支被路由并原样返回 rows/columns/total。
"""

import pytest

import app.api.v1.datasets as datasets_module
from app.models.dataset_version import DatasetVersion


@pytest.mark.asyncio
async def test_preview_parquet_routes_through_duckdb(
    client, session_factory, monkeypatch
):
    """parquet 格式 s3:// 版本应走 DuckDB 分支,不走 head_records,返回正确 shape。"""
    # ── 1. 插入 parquet 版本 ──────────────────────────────────────────────────
    async with session_factory() as session:
        ver = DatasetVersion(
            id="dsv-pq-preview",
            dataset_id="ds-pq-preview",
            version_no=1,
            storage_uri="s3://uploads/ds-pq-preview/v1/data.parquet",
            format="parquet",
            rows=2,
        )
        session.add(ver)
        await session.commit()

    # ── 2. 桩:_version_storage_cfg → 非 None(跳过真实 MinIO 配置读取) ────────
    async def fake_storage_cfg(version, session):
        return {"endpoint": "minio:9000", "access_key": "A", "secret_key": "S"}

    monkeypatch.setattr(datasets_module, "_version_storage_cfg", fake_storage_cfg)

    # ── 3. 桩:s3_settings_for_duckdb → 固定 tuple ───────────────────────────
    fake_s3 = ("minio:9000", False, "A", "S")
    monkeypatch.setattr(
        datasets_module, "s3_settings_for_duckdb", lambda cfg: fake_s3
    )

    # ── 4. 桩:_duck_query → 固定结果(断言参数,不真正调用 DuckDB) ───────────
    fixed_rows = [{"id": 1, "name": "Alice"}, {"id": 2, "name": "Bob"}]
    fixed_cols = ["id", "name"]
    duck_call_args: list = []

    def fake_duck_query(path, fmt, sql, limit, offset, s3):
        duck_call_args.append({"path": path, "fmt": fmt, "s3": s3})
        return fixed_rows, fixed_cols, len(fixed_rows)

    monkeypatch.setattr(datasets_module, "_duck_query", fake_duck_query)

    # ── 5. 调用预览接口 ───────────────────────────────────────────────────────
    resp = await client.get("/api/v1/dataset-versions/dsv-pq-preview/preview")
    assert resp.status_code == 200, resp.text

    body = resp.json()
    assert body["success"] is True
    assert body["data"] == fixed_rows
    assert body["columns"] == fixed_cols
    assert body["total"] == 2

    # DuckDB 确实被调用,且格式为 parquet
    assert len(duck_call_args) == 1
    assert duck_call_args[0]["fmt"] == "parquet"
    assert duck_call_args[0]["s3"] == fake_s3

    # 整数列保真(parquet 路径不做字符串转换)
    assert isinstance(body["data"][0]["id"], int)


@pytest.mark.asyncio
async def test_preview_local_jsonl_search(client, session_factory, tmp_path):
    """本地 jsonl 版本带 q 搜索:全文子串匹配(忽略大小写),total 为匹配行数,
    分页作用在匹配结果上。"""
    import json

    data = [
        {"id": "QA1", "question": "转账限额多少", "answer": "1 万"},
        {"id": "QA2", "question": "补卡多久拿到", "answer": "3-5 天"},
        {"id": "QA3", "question": "转账到账时间", "answer": "实时"},
    ]
    p = tmp_path / "data.jsonl"
    p.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in data) + "\n",
        encoding="utf-8",
    )
    async with session_factory() as session:
        session.add(
            DatasetVersion(
                id="dsv-q-search",
                dataset_id="ds-q-search",
                version_no=1,
                storage_uri=str(p),
                format="jsonl",
                rows=3,
            )
        )
        await session.commit()

    url = "/api/v1/dataset-versions/dsv-q-search/preview"

    # 中文子串:命中 QA1/QA3
    resp = await client.get(url, params={"q": "转账"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 2
    assert [r["id"] for r in body["data"]] == ["QA1", "QA3"]

    # 忽略大小写:小写 qa2 命中大写 id
    body = (await client.get(url, params={"q": "qa2"})).json()
    assert body["total"] == 1
    assert body["data"][0]["id"] == "QA2"

    # 分页作用在匹配结果上:offset=1 取第二条匹配
    body = (await client.get(url, params={"q": "转账", "limit": 1, "offset": 1})).json()
    assert body["total"] == 2
    assert [r["id"] for r in body["data"]] == ["QA3"]

    # 无匹配:total=0,data 空
    body = (await client.get(url, params={"q": "不存在的词"})).json()
    assert body["total"] == 0
    assert body["data"] == []

    # 不带 q:行为不变,total 用 version.rows
    body = (await client.get(url)).json()
    assert body["total"] == 3
    assert len(body["data"]) == 3


@pytest.mark.asyncio
async def test_edit_version_rows_local_jsonl(client, session_factory, tmp_path):
    """行级增删改(本地 jsonl):add/update/delete 直接改写文件,
    rows/size 元数据同步,preview indices 与文件行号对齐,非草稿 409。"""
    import json

    data = [
        {"id": "QA1", "q": "转账限额"},
        {"id": "QA2", "q": "补卡时间"},
        {"id": "QA3", "q": "转账到账"},
    ]
    p = tmp_path / "rows.jsonl"
    p.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in data) + "\n",
        encoding="utf-8",
    )
    async with session_factory() as session:
        session.add(
            DatasetVersion(
                id="dsv-row-edit",
                dataset_id="ds-row-edit",
                version_no=1,
                storage_uri=str(p),
                format="jsonl",
                rows=3,
            )
        )
        await session.commit()

    url = "/api/v1/dataset-versions/dsv-row-edit/rows"

    # add:追加一行
    resp = await client.post(
        url, json={"op": "add", "row": {"id": "QA4", "q": "新增的问题"}}
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["rows"] == 4
    lines = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x]
    assert len(lines) == 4 and lines[3]["id"] == "QA4"

    # update:改第 2 行(index=1)
    resp = await client.post(
        url, json={"op": "update", "index": 1, "row": {"id": "QA2", "q": "改过了"}}
    )
    assert resp.status_code == 200, resp.text
    lines = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x]
    assert lines[1] == {"id": "QA2", "q": "改过了"}

    # 搜索预览返回的 indices 是文件内全局行号:搜「转账」命中第 0/2 行
    body = (
        await client.get(
            "/api/v1/dataset-versions/dsv-row-edit/preview", params={"q": "转账"}
        )
    ).json()
    assert body["indices"] == [0, 2]

    # delete:删第 1 行(index=0),版本 rows 同步
    resp = await client.post(url, json={"op": "delete", "index": 0})
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["rows"] == 3
    lines = [json.loads(x) for x in p.read_text(encoding="utf-8").splitlines() if x]
    assert [x["id"] for x in lines] == ["QA2", "QA3", "QA4"]
    async with session_factory() as session:
        ver = await session.get(DatasetVersion, "dsv-row-edit")
        assert ver.rows == 3

    # 越界 index → 400
    resp = await client.post(url, json={"op": "delete", "index": 99})
    assert resp.status_code == 400

    # 非草稿版本 → 409
    async with session_factory() as session:
        ver = await session.get(DatasetVersion, "dsv-row-edit")
        ver.publish_status = "published"
        await session.commit()
    resp = await client.post(url, json={"op": "add", "row": {"id": "x"}})
    assert resp.status_code == 409
