"""流水线(pipeline)API 测试:CRUD、预置模板只读、执行(execute)分发。

- 预置模板(pipeline_presets)不入库,GET /pipelines 与库内自建流水线合并展示。
- execute:clean 场景走 jobs.py _start_job(memberConfigs),打桩 job_runner.spawn
  避免真跑 dj-process 子进程;合成/增强(LLM 场景)缺 goal → 400,蒸馏无 goal 概念
  故不受此限(行为完全由算子链自身参数决定)。
"""

from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.dataset import Dataset
from app.models.dataset_version import DatasetVersion
from app.models.dataset_version_table import DatasetVersionTable

DATASET_ID = "dset-pl1"
VERSION_ID = "dsv-pl1"


async def _seed_version_with_members(
    session_factory: async_sessionmaker, *, member_names: list[str]
) -> None:
    """落一个带表成员的数据集版本(clean execute 需要按成员建 memberConfigs)。"""
    async with session_factory() as session:
        session.add(Dataset(id=DATASET_ID, name="流水线测试集"))
        session.add(
            DatasetVersion(
                id=VERSION_ID,
                dataset_id=DATASET_ID,
                version_no=1,
                storage_uri=f"s3://bucket/{DATASET_ID}/v1/",
                format="jsonl",
            )
        )
        for name in member_names:
            session.add(
                DatasetVersionTable(
                    id=f"dvt-{name}",
                    dataset_version_id=VERSION_ID,
                    table_name=name,
                    storage_uri=f"s3://bucket/{DATASET_ID}/v1/{name}.jsonl",
                    format="jsonl",
                )
            )
        await session.commit()


def _custom_payload(scenario: str = "clean", goal: dict | None = None) -> dict:
    return {
        "name": "自定义清洗流水线",
        "description": "去 HTML + 规范化空格",
        "scenario": scenario,
        "spec": {
            "operators": [
                {"name": "clean_html_mapper", "params": None},
                {"name": "whitespace_normalization_mapper", "params": None},
            ],
            "goal": goal,
            "textKeys": None,
        },
    }


# ---------------------------------------------------------------------------
# 预置模板
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_list_pipelines_includes_presets_first(client: AsyncClient) -> None:
    """预置模板排最前,scenario 过滤生效。"""
    resp = await client.get("/api/v1/pipelines", params={"scenario": "clean"})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    ids = [item["id"] for item in body["data"]]
    assert ids[0] == "preset-standard-clean"
    assert "preset-dedup-clean" in ids
    assert all(item["isPreset"] for item in body["data"][:2])

    # 蒸馏场景预置模板
    resp = await client.get("/api/v1/pipelines", params={"scenario": "distillation"})
    body = resp.json()
    ids = [item["id"] for item in body["data"]]
    assert "preset-distill-dedup" in ids
    assert "preset-distill-rule-quality" in ids


@pytest.mark.asyncio
async def test_get_preset_detail(client: AsyncClient) -> None:
    resp = await client.get("/api/v1/pipelines/preset-standard-clean")
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data["name"] == "标准文本清洗"
    assert data["isPreset"] is True
    op_names = [op["name"] for op in data["spec"]["operators"]]
    assert "whitespace_normalization_mapper" in op_names
    assert "chinese_convert_mapper" in op_names


@pytest.mark.asyncio
async def test_preset_update_and_delete_rejected(client: AsyncClient) -> None:
    resp = await client.put(
        "/api/v1/pipelines/preset-standard-clean", json={"name": "改名"}
    )
    assert resp.status_code == 400
    assert resp.json()["success"] is False

    resp = await client.delete("/api/v1/pipelines/preset-standard-clean")
    assert resp.status_code == 400
    assert resp.json()["success"] is False


# ---------------------------------------------------------------------------
# CRUD(用户自建流水线)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_create_get_update_delete_pipeline(client: AsyncClient) -> None:
    resp = await client.post("/api/v1/pipelines", json=_custom_payload())
    assert resp.status_code == 200, resp.text
    created = resp.json()["data"]
    assert created["id"].startswith("pl-")
    assert created["isPreset"] is False
    assert created["scenario"] == "clean"
    pipeline_id = created["id"]

    resp = await client.get(f"/api/v1/pipelines/{pipeline_id}")
    assert resp.status_code == 200
    assert resp.json()["data"]["name"] == "自定义清洗流水线"

    resp = await client.put(
        f"/api/v1/pipelines/{pipeline_id}", json={"name": "改名后的流水线"}
    )
    assert resp.status_code == 200, resp.text
    updated = resp.json()["data"]
    assert updated["name"] == "改名后的流水线"
    # 未提交的字段(spec)保持不变
    assert len(updated["spec"]["operators"]) == 2

    resp = await client.delete(f"/api/v1/pipelines/{pipeline_id}")
    assert resp.status_code == 200
    assert resp.json()["success"] is True

    resp = await client.get(f"/api/v1/pipelines/{pipeline_id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_update_delete_not_found(client: AsyncClient) -> None:
    resp = await client.put("/api/v1/pipelines/pl-none", json={"name": "x"})
    assert resp.status_code == 404
    resp = await client.delete("/api/v1/pipelines/pl-none")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# execute:clean 场景
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_execute_clean_pipeline_builds_member_configs(
    client: AsyncClient,
    session_factory: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """clean 场景一键执行:按版本成员建 memberConfigs,产出 job 回指 pipelineId。"""
    await _seed_version_with_members(session_factory, member_names=["a", "b"])

    spawned: list[str] = []
    monkeypatch.setattr(
        "app.services.job_runner.spawn", lambda job_id: spawned.append(job_id)
    )

    create_resp = await client.post("/api/v1/pipelines", json=_custom_payload())
    pipeline_id = create_resp.json()["data"]["id"]

    resp = await client.post(
        f"/api/v1/pipelines/{pipeline_id}/execute",
        json={"datasetVersionId": VERSION_ID},
    )
    assert resp.status_code == 200, resp.text
    job = resp.json()["data"]
    assert job["type"] == "clean"
    assert job["pipelineId"] == pipeline_id
    assert job["state"] == "pending"
    assert spawned == [job["id"]]

    # 任务详情同样带回 pipelineId
    resp = await client.get(f"/api/v1/jobs/{job['id']}")
    assert resp.json()["data"]["pipelineId"] == pipeline_id


@pytest.mark.asyncio
async def test_execute_preset_dedup_pipeline(
    client: AsyncClient,
    session_factory: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """预置模板同样可执行(id 不落库,仍能被解析出来跑)。"""
    await _seed_version_with_members(session_factory, member_names=["only"])
    monkeypatch.setattr("app.services.job_runner.spawn", lambda job_id: None)

    resp = await client.post(
        "/api/v1/pipelines/preset-dedup-clean/execute",
        json={"datasetVersionId": VERSION_ID},
    )
    assert resp.status_code == 200, resp.text
    job = resp.json()["data"]
    assert job["pipelineId"] == "preset-dedup-clean"
    assert job["name"].startswith("去重专项-")


@pytest.mark.asyncio
async def test_execute_not_found(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/v1/pipelines/pl-none/execute", json={"datasetVersionId": VERSION_ID}
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# execute:LLM 场景(合成/增强)缺 goal → 400
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_execute_llm_scenario_without_goal_rejected(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    await _seed_version_with_members(session_factory, member_names=["only"])
    create_resp = await client.post(
        "/api/v1/pipelines", json=_custom_payload(scenario="synthesis", goal=None)
    )
    pipeline_id = create_resp.json()["data"]["id"]

    resp = await client.post(
        f"/api/v1/pipelines/{pipeline_id}/execute",
        json={"datasetVersionId": VERSION_ID},
    )
    assert resp.status_code == 400
    assert "goal" in resp.json()["message"]


# ---------------------------------------------------------------------------
# execute:蒸馏无 goal 概念,goal=None 也能正常执行(行为由算子链自身参数决定)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_execute_distillation_pipeline_without_goal(
    client: AsyncClient,
    session_factory: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _seed_version_with_members(session_factory, member_names=["only"])
    monkeypatch.setattr("app.services.job_runner.spawn", lambda job_id: None)

    payload = {
        "name": "随机采样蒸馏流水线",
        "scenario": "distillation",
        "spec": {
            "operators": [
                {"name": "random_selector", "params": {"select_ratio": 0.3}},
            ],
            "goal": None,
            "textKeys": None,
        },
    }
    create_resp = await client.post("/api/v1/pipelines", json=payload)
    pipeline_id = create_resp.json()["data"]["id"]

    resp = await client.post(
        f"/api/v1/pipelines/{pipeline_id}/execute",
        json={"datasetVersionId": VERSION_ID},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["data"]["type"] == "distillation"
