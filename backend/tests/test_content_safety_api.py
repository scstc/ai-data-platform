"""内容安全审核 API 测试(#4)。

走真实审核引擎(useLlm=false → 规则 + 内置词表 + PII,避免依赖外部 LLM):
- POST /content-safety/jobs 建 review job,同步跑完 → success。
- 报告有命中(flaggedRows/byCategory/bySource),命中行删除产出净化版本
  (origin=review;被删行另写 removed 存档,净化版仅保留干净行)。
- GET report / findings(分页 + 过滤),GET jobs type=review 列表。
- 版本不存在 → 404。

数据集/版本经 session_factory 直接落库;被审 jsonl 落 tmp_path(受管目录)。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import settings
from app.models.dataset import Dataset
from app.models.dataset_version import DatasetVersion
from app.services import job_runner

DATASET_ID = "dset-cs1"
VERSION_ID = "dsv-cs1"


@pytest.fixture(autouse=True)
def _datasets_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """打标产物落受管目录:把 datasets_dir 指到 tmp_path。"""
    monkeypatch.setattr(settings, "datasets_dir", str(tmp_path))


async def _seed_version(
    session_factory: async_sessionmaker, tmp_path: Path, rows: list[dict]
) -> str:
    """落库数据集 + 版本,并把行写到受管目录下的 jsonl,返回数据文件路径。"""
    data_dir = tmp_path / DATASET_ID / "v1"
    data_dir.mkdir(parents=True, exist_ok=True)
    data_path = data_dir / "data.jsonl"
    with data_path.open("w", encoding="utf-8") as fp:
        for row in rows:
            fp.write(json.dumps(row, ensure_ascii=False) + "\n")
    async with session_factory() as session:
        session.add(Dataset(id=DATASET_ID, name="审核测试集"))
        session.add(
            DatasetVersion(
                id=VERSION_ID,
                dataset_id=DATASET_ID,
                version_no=1,
                storage_uri=str(data_path),
                format="jsonl",
                rows=len(rows),
            )
        )
        await session.commit()
    return str(data_path)


@pytest.mark.asyncio
async def test_review_job_rejects_binary_version(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    """二进制版本提交审核 → 提前 400(与加工一致,不建 job、不跑引擎)。"""
    async with session_factory() as session:
        session.add(Dataset(id="dset-bin-cs", name="二进制集"))
        session.add(
            DatasetVersion(
                id="dsv-bin-cs",
                dataset_id="dset-bin-cs",
                version_no=1,
                storage_uri="s3://x/clip.mp4",
                format="mp4",
                rows=None,
                origin="hosted",
            )
        )
        await session.commit()

    resp = await client.post(
        "/api/v1/content-safety/jobs",
        json={"datasetVersionId": "dsv-bin-cs", "config": {"useLlm": False}},
    )
    assert resp.status_code == 400
    assert "二进制" in resp.json()["message"]


@pytest.mark.asyncio
async def test_review_job_full_flow(
    client: AsyncClient, session_factory: async_sessionmaker, tmp_path: Path
) -> None:
    """端到端:建 review job → 报告有命中 → findings 分页/过滤 → 打标版本存在。"""
    rows = [
        {"text": "这是一个赌博网站推广,快来下注"},  # flagged_words: gambling
        {"text": "联系电话 13800001111"},  # pii: phone
        {"text": "今天天气真好,适合散步"},  # 正常
        {"text": "包含 SECRET 内部代号"},  # custom word: secret
    ]
    await _seed_version(session_factory, tmp_path, rows)

    resp = await client.post(
        "/api/v1/content-safety/jobs",
        json={
            "datasetVersionId": VERSION_ID,
            "name": "首次审核",
            "config": {
                "useLlm": False,
                "usePii": True,
                "useFlaggedWords": True,
                "customWords": ["secret"],
                "customRegex": [],
            },
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    data = body["data"]
    assert data["type"] == "review"
    job_id = data["id"]

    # 审核异步执行(spawn 后立即返回 pending):等后台任务落终态再断言产物
    await job_runner.drain()

    # 报告:状态终态 + 产物版本 + 命中行数与各维度计数
    resp = await client.get(f"/api/v1/content-safety/jobs/{job_id}/report")
    assert resp.status_code == 200
    report = resp.json()["data"]
    assert report["state"] == "success", report.get("error")
    tagged_version_id = report["taggedVersionId"]
    assert tagged_version_id is not None
    rr = report["reviewReport"]
    assert rr["totalRows"] == 4
    assert rr["scannedRows"] == 4
    assert rr["flaggedRows"] == 3  # 赌博 + 手机号 + secret
    assert rr["sampleLimitApplied"] is False
    assert rr["byCategory"].get("gambling") == 1
    assert rr["byCategory"].get("pii") == 1
    assert rr["bySource"].get("flagged_words") == 1
    assert rr["bySource"].get("pii") == 1
    assert rr["bySource"].get("keyword") == 1
    # 命中处置固定为删除:3 命中行删除,另写 removed 存档
    assert rr["action"] == "delete"
    assert rr["deletedRows"] == 3
    assert rr["removedArchives"]  # 非空

    # findings 分页:全部命中按 row_index 升序
    resp = await client.get(
        f"/api/v1/content-safety/jobs/{job_id}/findings",
        params={"current": 1, "pageSize": 10},
    )
    assert resp.status_code == 200
    fbody = resp.json()
    assert fbody["total"] == 3
    indices = [f["rowIndex"] for f in fbody["data"]]
    assert indices == sorted(indices)

    # findings 过滤:source=pii 只剩手机号那条
    resp = await client.get(
        f"/api/v1/content-safety/jobs/{job_id}/findings",
        params={"source": "pii"},
    )
    fbody = resp.json()
    assert fbody["total"] == 1
    assert fbody["data"][0]["category"] == "pii"
    assert fbody["data"][0]["detail"] == "phone"

    # 净化版本落库且 origin=review;命中行已删除,仅保留干净行
    async with session_factory() as session:
        v = await session.get(DatasetVersion, tagged_version_id)
        assert v is not None
        assert v.origin == "review"
        assert v.produced_by_job_id == job_id
        assert v.version_no == 2
        # 全量扫描 + 命中已剔净 → 产出净化版自动判 passed(可发布)
        assert v.scan_verdict == "passed"
        assert v.verdict_source == "auto"
        assert v.publish_status == "draft"
        kept_lines = [
            json.loads(ln)
            for ln in Path(v.storage_uri).read_text(encoding="utf-8").splitlines()
            if ln.strip()
        ]
        # 被审版本自身回写 failed(3 命中,供发布门直接拦截)
        src = await session.get(DatasetVersion, VERSION_ID)
        assert src.scan_verdict == "failed"
    # 净化版仅剩 1 条干净行(其余 3 条命中被删),仍带 safety 字段
    assert len(kept_lines) == 1
    assert all("safety" in r for r in kept_lines)
    assert kept_lines[0]["safety"]["flagged"] is False
    # 被删行存档:3 条,均为命中行
    removed_path = Path(v.storage_uri).with_name("data.removed.jsonl")
    removed_lines = [
        json.loads(ln)
        for ln in removed_path.read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    assert len(removed_lines) == 3
    assert all(r["safety"]["flagged"] is True for r in removed_lines)

    # type=review 列表
    resp = await client.get("/api/v1/content-safety/jobs")
    lbody = resp.json()
    assert lbody["total"] == 1
    assert lbody["data"][0]["id"] == job_id


@pytest.mark.asyncio
async def test_review_job_version_not_found(client: AsyncClient) -> None:
    """版本不存在 → 404。"""
    resp = await client.post(
        "/api/v1/content-safety/jobs",
        json={"datasetVersionId": "dsv-none", "config": {"useLlm": False}},
    )
    assert resp.status_code == 404
    assert resp.json()["success"] is False


@pytest.mark.asyncio
async def test_review_job_sample_limit(
    client: AsyncClient, session_factory: async_sessionmaker, tmp_path: Path
) -> None:
    """sampleLimit 生效:只扫前 N 行,报告显式标注 sampleLimitApplied。"""
    rows = [{"text": "赌博推广"} for _ in range(5)]
    await _seed_version(session_factory, tmp_path, rows)

    resp = await client.post(
        "/api/v1/content-safety/jobs",
        json={
            "datasetVersionId": VERSION_ID,
            "config": {"useLlm": False, "useFlaggedWords": True, "sampleLimit": 2},
        },
    )
    assert resp.status_code == 200, resp.text
    job_id = resp.json()["data"]["id"]
    await job_runner.drain()

    resp = await client.get(f"/api/v1/content-safety/jobs/{job_id}/report")
    rr = resp.json()["data"]["reviewReport"]
    assert rr["totalRows"] == 5
    assert rr["scannedRows"] == 2
    assert rr["sampleLimitApplied"] is True
    assert rr["flaggedRows"] == 2


@pytest.mark.asyncio
async def test_review_verdict_passed_on_clean_full_scan(
    client: AsyncClient, session_factory: async_sessionmaker, tmp_path: Path
) -> None:
    """全量扫描 + 零命中 → 打标版本自动判 passed(可发布)。"""
    rows = [{"text": "今天天气真好,适合散步"} for _ in range(3)]
    await _seed_version(session_factory, tmp_path, rows)

    resp = await client.post(
        "/api/v1/content-safety/jobs",
        json={
            "datasetVersionId": VERSION_ID,
            "config": {"useLlm": False, "usePii": True, "useFlaggedWords": True},
        },
    )
    assert resp.status_code == 200, resp.text
    job_id = resp.json()["data"]["id"]
    await job_runner.drain()
    report = (
        await client.get(f"/api/v1/content-safety/jobs/{job_id}/report")
    ).json()["data"]
    assert report["state"] == "success", report.get("error")
    tagged_id = report["taggedVersionId"]
    async with session_factory() as session:
        v = await session.get(DatasetVersion, tagged_id)
        assert v.scan_verdict == "passed"
        assert v.verdict_source == "auto"


@pytest.mark.asyncio
async def test_review_verdict_unscanned_on_clean_partial_scan(
    client: AsyncClient, session_factory: async_sessionmaker, tmp_path: Path
) -> None:
    """零命中但只扫了样本前缀 → 自动判 unscanned,不允许据此发布(发布门防漏)。"""
    rows = [{"text": "今天天气真好,适合散步"} for _ in range(5)]
    await _seed_version(session_factory, tmp_path, rows)

    resp = await client.post(
        "/api/v1/content-safety/jobs",
        json={
            "datasetVersionId": VERSION_ID,
            "config": {"useLlm": False, "useFlaggedWords": True, "sampleLimit": 2},
        },
    )
    assert resp.status_code == 200, resp.text
    job_id = resp.json()["data"]["id"]
    await job_runner.drain()
    report = (
        await client.get(f"/api/v1/content-safety/jobs/{job_id}/report")
    ).json()["data"]
    assert report["state"] == "success", report.get("error")
    tagged_id = report["taggedVersionId"]
    async with session_factory() as session:
        v = await session.get(DatasetVersion, tagged_id)
        # 部分扫描即便零命中也不能 certify 整版安全
        assert v.scan_verdict == "unscanned"
