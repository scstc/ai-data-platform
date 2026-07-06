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
from app.models.dataset_version_table import DatasetVersionTable
from app.models.job_input import JobInput
from app.services import job_runner

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

    async def fake_run_quality_job(session, *, job_id, input_version, operators, member_configs=None, target_members=None, text_keys=None, **kwargs):
        # 镜像真实实现的副作用:stats_uri 回写输入版本 + 记血缘边(不产新版本)
        assert operators == [
            {"name": "text_length_filter", "params": {"min_len": 5}}
        ]
        input_version.stats_uri = stats_uri
        session.add(
            JobInput(job_id=job_id, dataset_version_id=input_version.id)
        )
        await session.commit()
        return input_version, "process: []", str(tmp_path / "run.log")

    # 质量任务后台异步执行(job_runner.spawn),被打桩的是 job_runner 里
    # 引用的 run_quality_job(非 api.v1.quality 模块——那里已不再 import 它)。
    monkeypatch.setattr(
        "app.services.job_runner.run_quality_job", fake_run_quality_job
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
    job_id = data["id"]
    # 等待后台 _run_job 协程跑完(走 fake_run_quality_job)
    from app.services import job_runner
    task = job_runner._task_by_job.get(job_id)
    if task is not None:
        await task
    # 重读最新状态
    resp = await client.get(f"/api/v1/jobs/{job_id}")
    data = resp.json()["data"]
    assert data["type"] == "quality"
    assert data["state"] == "success"
    assert data["progress"] == 100
    # 不产新版本:output 为 null,stats_uri 回写在输入版本上
    assert data["output"] is None
    assert {
        "datasetId": data["input"]["datasetId"],
        "datasetName": data["input"]["datasetName"],
        "versionId": data["input"]["versionId"],
        "versionNo": data["input"]["versionNo"],
    } == {
        "datasetId": DATASET_ID,
        "datasetName": "质量测试集",
        "versionId": VERSION_ID,
        "versionNo": 1,
    }
    # 输入概要带版本展示标签(v{日期} (#n));日期随运行日变化,只校验形态
    assert data["input"]["versionLabel"].startswith("v")
    assert data["input"]["versionLabel"].endswith("(#1)")

    # 输入版本被回写 stats_uri → stats 端点可用
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

    async def fake_fail(session, *, job_id, input_version, operators, text_keys=None, **kwargs):
        raise QualityError("dj-analyze 退出码 1")

    monkeypatch.setattr("app.services.job_runner.run_quality_job", fake_fail)

    resp = await client.post(
        "/api/v1/quality/jobs",
        json={
            "name": "失败任务",
            "datasetVersionId": VERSION_ID,
            "operators": [{"name": "text_length_filter"}],
        },
    )
    assert resp.status_code == 200
    job_id = resp.json()["data"]["id"]

    await job_runner.drain()
    data = (await client.get(f"/api/v1/jobs/{job_id}")).json()["data"]
    assert data["state"] == "failed"
    assert "dj-analyze" in data["error"]
    # 失败时未记血缘边(job_inputs);input 回退到 spec.dataset_version_id
    # 反查展示「指定过哪个版本」,标 fallback=True 区分于成功血缘
    assert data["input"]["versionId"] == VERSION_ID
    assert data["input"]["fallback"] is True

    resp = await client.get(f"/api/v1/dataset-versions/{VERSION_ID}/stats")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 多文件(成员级)质量评估:每个表成员独立 stats_uri,查询端点按 member 定位
# ---------------------------------------------------------------------------
MULTI_VERSION_ID = "dsv-multi1"


async def _seed_multi_member_version(
    session_factory: async_sessionmaker,
    *,
    member_a_stats: str | None,
    member_b_stats: str | None,
) -> None:
    """落一个 2 成员版本(无版本级 stats_uri,quality-members 走成员表路径)。"""
    async with session_factory() as session:
        session.add(Dataset(id="dset-multi1", name="多文件质量测试集"))
        session.add(
            DatasetVersion(
                id=MULTI_VERSION_ID,
                dataset_id="dset-multi1",
                version_no=1,
                storage_uri="s3://bucket/multi1/v1/",
                format="multi",
            )
        )
        session.add(
            DatasetVersionTable(
                id="dvt-a1",
                dataset_version_id=MULTI_VERSION_ID,
                table_name="member_a",
                storage_uri="s3://bucket/multi1/v1/member_a.jsonl",
                format="jsonl",
                stats_uri=member_a_stats,
            )
        )
        session.add(
            DatasetVersionTable(
                id="dvt-b1",
                dataset_version_id=MULTI_VERSION_ID,
                table_name="member_b",
                storage_uri="s3://bucket/multi1/v1/member_b.jsonl",
                format="jsonl",
                stats_uri=member_b_stats,
            )
        )
        await session.commit()


@pytest.mark.asyncio
async def test_quality_members_lists_all_members(
    client: AsyncClient, session_factory: async_sessionmaker, tmp_path: Path
) -> None:
    """quality-members:多成员版本列出各成员 + hasStats;无成员表旧版本合成单元素。"""
    _, stats_a = _write_files(tmp_path, [{"a": 1}], ["ta"])
    await _seed_multi_member_version(
        session_factory, member_a_stats=stats_a, member_b_stats=None
    )

    resp = await client.get(
        f"/api/v1/dataset-versions/{MULTI_VERSION_ID}/quality-members"
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()["data"]
    assert data == [
        {"memberName": "member_a", "hasStats": True},
        {"memberName": "member_b", "hasStats": False},
    ]

    # 旧版单文件版本(无成员表记录)→ 合成单元素 "data"
    storage_uri, stats_uri = _write_files(tmp_path, [{"a": 1}], ["t"])
    await _seed_version(session_factory, storage_uri=storage_uri, stats_uri=stats_uri)
    resp = await client.get(f"/api/v1/dataset-versions/{VERSION_ID}/quality-members")
    assert resp.json()["data"] == [{"memberName": "data", "hasStats": True}]

    # 版本不存在 → 404
    resp = await client.get("/api/v1/dataset-versions/dsv-none/quality-members")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_multi_member_stats_requires_member_param(
    client: AsyncClient, session_factory: async_sessionmaker, tmp_path: Path
) -> None:
    """多成员版本不传 member → 400(不猜);传 member 各自查各自,互不串扰。"""
    texts_a = [f"member-a-{i}" for i in range(3)]
    texts_b = [f"member-b-{i}" for i in range(5)]
    dir_a, dir_b = tmp_path / "a", tmp_path / "b"
    dir_a.mkdir()
    dir_b.mkdir()
    _, stats_a = _write_files(dir_a, [{"score": i} for i in range(3)], texts_a)
    _, stats_b = _write_files(
        dir_b, [{"score": i + 100} for i in range(5)], texts_b
    )
    await _seed_multi_member_version(
        session_factory, member_a_stats=stats_a, member_b_stats=stats_b
    )

    # 不传 member → 400
    for suffix in ("stats", "quality-report"):
        resp = await client.get(
            f"/api/v1/dataset-versions/{MULTI_VERSION_ID}/{suffix}"
        )
        assert resp.status_code == 400, suffix
        assert "member" in resp.json()["message"]

    # 传 member=member_a → 只见 member_a 的 3 条,得分互不串扰
    resp = await client.get(
        f"/api/v1/dataset-versions/{MULTI_VERSION_ID}/stats",
        params={"member": "member_a"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 3
    assert {row["stats"]["score"] for row in body["data"]} == {0, 1, 2}

    # 传 member=member_b → 只见 member_b 的 5 条
    resp = await client.get(
        f"/api/v1/dataset-versions/{MULTI_VERSION_ID}/stats",
        params={"member": "member_b"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 5
    assert {row["stats"]["score"] for row in body["data"]} == {100, 101, 102, 103, 104}

    # quality-report 同理按 member 隔离
    resp = await client.get(
        f"/api/v1/dataset-versions/{MULTI_VERSION_ID}/quality-report",
        params={"member": "member_a"},
    )
    assert resp.status_code == 200
    report_a = resp.json()["data"]
    assert report_a["rows"] == 3
    assert report_a["metrics"][0]["mean"] == pytest.approx(1.0)  # mean(0,1,2)

    resp = await client.get(
        f"/api/v1/dataset-versions/{MULTI_VERSION_ID}/quality-report",
        params={"member": "member_b"},
    )
    report_b = resp.json()["data"]
    assert report_b["rows"] == 5
    assert report_b["metrics"][0]["mean"] == pytest.approx(102.0)  # mean(100..104)

    # 不存在的成员 → 404
    resp = await client.get(
        f"/api/v1/dataset-versions/{MULTI_VERSION_ID}/stats",
        params={"member": "no_such_member"},
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_single_member_version_stats_without_member_param(
    client: AsyncClient, session_factory: async_sessionmaker, tmp_path: Path
) -> None:
    """成员表只有 1 行时,不传 member 也能查(自动选中,行为等同旧版单文件)。"""
    _, stats_a = _write_files(tmp_path, [{"score": 7}], ["only-one"])
    async with session_factory() as session:
        session.add(Dataset(id="dset-single-m", name="单成员测试集"))
        session.add(
            DatasetVersion(
                id="dsv-single-m",
                dataset_id="dset-single-m",
                version_no=1,
                storage_uri="s3://bucket/single/v1/",
                format="jsonl",
            )
        )
        session.add(
            DatasetVersionTable(
                id="dvt-single-m",
                dataset_version_id="dsv-single-m",
                table_name="only",
                storage_uri="s3://bucket/single/v1/only.jsonl",
                format="jsonl",
                stats_uri=stats_a,
            )
        )
        await session.commit()

    resp = await client.get("/api/v1/dataset-versions/dsv-single-m/stats")
    assert resp.status_code == 200, resp.text
    assert resp.json()["total"] == 1
