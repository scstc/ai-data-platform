"""质量评估 API 测试(#6)。

- 逐条 stats:分页 / 与数据文件按行号对齐 / text 截断 200 / 无 stats 404。
- 质量报告:数值型指标聚合(均值/分位数/20 桶直方图),非数值指标跳过。
- POST /quality/jobs:校验分支(未知算子/非 filter/资源不可执行/版本不存在)
  与成功路径(monkeypatch 掉 run_quality_job 子进程层),以及 GET /jobs 的
  type 过滤与 input 回带。

数据集/版本经 session_factory 直接落库;数据与 stats 文件落 tmp_path。
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
from app.models.job_input import JobInput

DATASET_ID = "dset-q1"
VERSION_ID = "dsv-q1"


@pytest.fixture(autouse=True)
def _datasets_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """stats/storage 路径校验要求文件落在受管目录内,把根指到 tmp_path。"""
    monkeypatch.setattr(settings, "datasets_dir", str(tmp_path))


def _write_files(
    tmp_path: Path, stats_rows: list[dict], texts: list[str]
) -> tuple[str, str]:
    """造数据文件与 stats 文件(DJ 形态:每行 {"__dj__stats__": {...}})。"""
    data_path = tmp_path / "data.jsonl"
    stats_path = tmp_path / "data_stats.jsonl"
    with data_path.open("w", encoding="utf-8") as fp:
        for text in texts:
            fp.write(json.dumps({"text": text}, ensure_ascii=False) + "\n")
    with stats_path.open("w", encoding="utf-8") as fp:
        for stats in stats_rows:
            fp.write(
                json.dumps({"__dj__stats__": stats}, ensure_ascii=False) + "\n"
            )
    return str(data_path), str(stats_path)


async def _seed_version(
    session_factory: async_sessionmaker,
    *,
    storage_uri: str,
    stats_uri: str | None,
) -> None:
    """直接落库数据集 + 版本。"""
    async with session_factory() as session:
        session.add(Dataset(id=DATASET_ID, name="质量测试集"))
        session.add(
            DatasetVersion(
                id=VERSION_ID,
                dataset_id=DATASET_ID,
                version_no=1,
                storage_uri=storage_uri,
                stats_uri=stats_uri,
                format="jsonl",
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_stats_pagination_and_alignment(
    client: AsyncClient, session_factory: async_sessionmaker, tmp_path: Path
) -> None:
    """分页窗口正确、text 与 stats 按行号对齐且截断 200、metrics 归集。"""
    n = 25
    texts = [f"样本{i}-" + "x" * 300 for i in range(n)]
    stats_rows = [{"text_len": i, "lang": "zh"} for i in range(n)]
    storage_uri, stats_uri = _write_files(tmp_path, stats_rows, texts)
    await _seed_version(
        session_factory, storage_uri=storage_uri, stats_uri=stats_uri
    )

    resp = await client.get(
        f"/api/v1/dataset-versions/{VERSION_ID}/stats",
        params={"current": 2, "pageSize": 10},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    assert body["total"] == n
    assert body["metrics"] == ["lang", "text_len"]
    assert len(body["data"]) == 10
    # 第二页首行 = 绝对行号 10,text/stats 同行对齐
    first = body["data"][0]
    assert first["index"] == 10
    assert first["text"].startswith("样本10-")
    assert len(first["text"]) == 200  # 截断 200 字符
    assert first["stats"] == {"text_len": 10, "lang": "zh"}
    # 末页不足一页
    resp = await client.get(
        f"/api/v1/dataset-versions/{VERSION_ID}/stats",
        params={"current": 3, "pageSize": 10},
    )
    body = resp.json()
    assert len(body["data"]) == 5
    assert body["data"][-1]["index"] == 24


@pytest.mark.asyncio
async def test_stats_404_without_stats_uri(
    client: AsyncClient, session_factory: async_sessionmaker, tmp_path: Path
) -> None:
    """未做过质量评估(无 stats_uri)的版本:stats 与 report 均 404。"""
    storage_uri, _ = _write_files(tmp_path, [{"a": 1}], ["t"])
    await _seed_version(session_factory, storage_uri=storage_uri, stats_uri=None)

    for suffix in ("stats", "quality-report"):
        resp = await client.get(
            f"/api/v1/dataset-versions/{VERSION_ID}/{suffix}"
        )
        assert resp.status_code == 404, suffix
        body = resp.json()
        assert body["success"] is False
        assert body["message"] == "该版本尚未进行质量评估"

    # 版本本身不存在 → 404
    resp = await client.get("/api/v1/dataset-versions/dsv-none/stats")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_quality_report_aggregation(
    client: AsyncClient, session_factory: async_sessionmaker, tmp_path: Path
) -> None:
    """数值指标聚合正确(0..99:均值/分位数/20 桶各 5),非数值指标跳过。"""
    n = 100
    stats_rows = [
        {"text_len": i, "lang": "zh", "word_list": ["a", "b"]} for i in range(n)
    ]
    storage_uri, stats_uri = _write_files(
        tmp_path, stats_rows, ["t"] * n
    )
    await _seed_version(
        session_factory, storage_uri=storage_uri, stats_uri=stats_uri
    )

    resp = await client.get(
        f"/api/v1/dataset-versions/{VERSION_ID}/quality-report"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    data = body["data"]
    assert data["rows"] == n
    # 字符串/列表型指标被跳过,只剩 text_len
    assert [m["name"] for m in data["metrics"]] == ["text_len"]
    metric = data["metrics"][0]
    assert metric["count"] == n
    assert metric["mean"] == pytest.approx(49.5)
    assert metric["min"] == 0
    assert metric["max"] == 99
    assert metric["p25"] == pytest.approx(24.75)
    assert metric["p50"] == pytest.approx(49.5)
    assert metric["p75"] == pytest.approx(74.25)
    hist = metric["histogram"]
    assert len(hist) == 20
    assert all(b["count"] == 5 for b in hist)
    assert hist[0]["x0"] == 0
    assert hist[-1]["x1"] == pytest.approx(99)


@pytest.mark.asyncio
async def test_create_quality_job_validations(
    client: AsyncClient, session_factory: async_sessionmaker, tmp_path: Path
) -> None:
    """校验分支:空算子/未知算子/非 filter/资源不可执行 400,版本不存在 404。"""
    storage_uri, stats_uri = _write_files(tmp_path, [{"a": 1}], ["t"])
    await _seed_version(
        session_factory, storage_uri=storage_uri, stats_uri=None
    )

    def payload(ops: list[dict], version_id: str = VERSION_ID) -> dict:
        return {
            "name": "质量评估",
            "datasetVersionId": version_id,
            "operators": ops,
        }

    # 空算子
    resp = await client.post("/api/v1/quality/jobs", json=payload([]))
    assert resp.status_code == 400
    # 未知算子
    resp = await client.post(
        "/api/v1/quality/jobs", json=payload([{"name": "no_such_filter"}])
    )
    assert resp.status_code == 400
    assert "未知算子" in resp.json()["message"]
    # 非 filter 类算子(mapper)
    resp = await client.post(
        "/api/v1/quality/jobs",
        json=payload([{"name": "chinese_convert_mapper"}]),
    )
    assert resp.status_code == 400
    assert "filter" in resp.json()["message"]
    # filter 但资源不可执行(needs_compute)
    resp = await client.post(
        "/api/v1/quality/jobs", json=payload([{"name": "alphanumeric_filter"}])
    )
    assert resp.status_code == 400
    # 版本不存在
    resp = await client.post(
        "/api/v1/quality/jobs",
        json=payload([{"name": "text_length_filter"}], version_id="dsv-none"),
    )
    assert resp.status_code == 404
    assert resp.json()["success"] is False


@pytest.mark.asyncio
async def test_create_quality_job_rejects_binary_version(
    client: AsyncClient, session_factory: async_sessionmaker
) -> None:
    """二进制版本提交质量评估 → 提前 400(与加工一致,不建 job、不跑引擎)。"""
    async with session_factory() as session:
        session.add(Dataset(id="dset-bin-q", name="二进制集"))
        session.add(
            DatasetVersion(
                id="dsv-bin-q",
                dataset_id="dset-bin-q",
                version_no=1,
                storage_uri="s3://x/clip.mp4",
                format="mp4",
                rows=None,
                origin="hosted",
            )
        )
        await session.commit()

    resp = await client.post(
        "/api/v1/quality/jobs",
        json={
            "name": "质量评估",
            "datasetVersionId": "dsv-bin-q",
            "operators": [{"name": "text_length_filter"}],
        },
    )
    assert resp.status_code == 400
    assert "二进制" in resp.json()["message"]


@pytest.mark.asyncio
async def test_create_quality_job_success_and_type_filter(
    client: AsyncClient,
    session_factory: async_sessionmaker,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """成功路径(子进程层打桩):回写 stats_uri + 血缘边,响应带 input、
    output 为 null;GET /jobs 列表/详情带 input 且 type 过滤生效。"""
    storage_uri, stats_uri = _write_files(tmp_path, [{"text_len": 3}], ["t"])
    await _seed_version(
        session_factory, storage_uri=storage_uri, stats_uri=None
    )

    async def fake_run_quality_job(session, *, job_id, input_version, operators):
        # 镜像真实实现的副作用:回写 stats_uri + 记血缘边
        assert operators == [
            {"name": "text_length_filter", "params": {"min_len": 5}}
        ]
        input_version.stats_uri = stats_uri
        session.add(
            JobInput(job_id=job_id, dataset_version_id=input_version.id)
        )
        await session.commit()
        return "process: []", str(tmp_path / "run.log")

    monkeypatch.setattr(
        "app.api.v1.quality.run_quality_job", fake_run_quality_job
    )

    resp = await client.post(
        "/api/v1/quality/jobs",
        json={
            "name": "文本长度评估",
            "datasetVersionId": VERSION_ID,
            "operators": [
                {"name": "text_length_filter", "params": {"min_len": 5}}
            ],
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["success"] is True
    data = body["data"]
    assert data["type"] == "quality"
    assert data["state"] == "success"
    assert data["progress"] == 100
    assert data["output"] is None  # 质量任务不产新版本
    assert data["input"] == {
        "datasetId": DATASET_ID,
        "datasetName": "质量测试集",
        "versionId": VERSION_ID,
        "versionNo": 1,
    }
    job_id = data["id"]

    # 版本 stats_uri 已回写 → stats 端点可用
    resp = await client.get(f"/api/v1/dataset-versions/{VERSION_ID}/stats")
    assert resp.status_code == 200
    assert resp.json()["total"] == 1

    # 列表 type 过滤 + input 回带
    resp = await client.get("/api/v1/jobs", params={"type": "quality"})
    body = resp.json()
    assert body["total"] == 1
    assert body["data"][0]["id"] == job_id
    assert body["data"][0]["input"]["versionId"] == VERSION_ID
    resp = await client.get("/api/v1/jobs", params={"type": "clean"})
    assert resp.json()["total"] == 0

    # 详情带 input
    resp = await client.get(f"/api/v1/jobs/{job_id}")
    assert resp.json()["data"]["input"]["datasetId"] == DATASET_ID


@pytest.mark.asyncio
async def test_create_quality_job_engine_failure(
    client: AsyncClient,
    session_factory: async_sessionmaker,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """执行失败:任务转 failed 并记录错误,版本 stats_uri 不被回写。"""
    from app.services.quality import QualityError

    storage_uri, _ = _write_files(tmp_path, [{"a": 1}], ["t"])
    await _seed_version(
        session_factory, storage_uri=storage_uri, stats_uri=None
    )

    async def fake_fail(session, *, job_id, input_version, operators):
        raise QualityError("dj-analyze 退出码 1")

    monkeypatch.setattr("app.api.v1.quality.run_quality_job", fake_fail)

    resp = await client.post(
        "/api/v1/quality/jobs",
        json={
            "name": "失败任务",
            "datasetVersionId": VERSION_ID,
            "operators": [{"name": "text_length_filter"}],
        },
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["state"] == "failed"
    assert "dj-analyze" in data["error"]
    assert data["input"] is None  # 失败时未记血缘边

    resp = await client.get(f"/api/v1/dataset-versions/{VERSION_ID}/stats")
    assert resp.status_code == 404
