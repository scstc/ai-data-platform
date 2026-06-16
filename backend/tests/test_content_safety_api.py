"""内容安全审核 API 测试(#4)。

走真实审核引擎(useLlm=false → 规则 + 内置词表 + PII,避免依赖外部 LLM):
- POST /content-safety/jobs 建 review job,同步跑完 → success。
- 报告有命中(flaggedRows/byCategory/bySource),产出打标版本(origin=review,
  每行带 safety 字段)。
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
    assert data["state"] == "success"
    job_id = data["id"]
    # 打标版本作为产物挂在 output 上
    assert data["output"] is not None
    tagged_version_id = data["output"]["versionId"]
    assert data["input"]["versionId"] == VERSION_ID

    # 报告:命中行数与各维度计数
    resp = await client.get(f"/api/v1/content-safety/jobs/{job_id}/report")
    assert resp.status_code == 200
    report = resp.json()["data"]
    assert report["taggedVersionId"] == tagged_version_id
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

    # 打标版本落库且 origin=review;每行带 safety 字段
    async with session_factory() as session:
        v = await session.get(DatasetVersion, tagged_version_id)
        assert v is not None
        assert v.origin == "review"
        assert v.produced_by_job_id == job_id
        assert v.version_no == 2
        tagged_lines = [
            json.loads(ln)
            for ln in Path(v.storage_uri).read_text(encoding="utf-8").splitlines()
            if ln.strip()
        ]
    assert len(tagged_lines) == 4
    assert all("safety" in r for r in tagged_lines)
    assert tagged_lines[0]["safety"]["flagged"] is True
    assert "gambling" in tagged_lines[0]["safety"]["categories"]
    assert tagged_lines[2]["safety"]["flagged"] is False

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

    resp = await client.get(f"/api/v1/content-safety/jobs/{job_id}/report")
    rr = resp.json()["data"]["reviewReport"]
    assert rr["totalRows"] == 5
    assert rr["scannedRows"] == 2
    assert rr["sampleLimitApplied"] is True
    assert rr["flaggedRows"] == 2
