"""LLM 配置快照(数据集可溯源可复现 P0-①):任务执行时固化配置 + 代理按快照转发。

覆盖:
- resolve_llm_config(None) 完全等价 get_active_llm_config()(零快照场景零行为变化)。
- snapshot_active_llm_config 不含 api_key;无激活配置时返回 None。
- resolve_llm_config(snapshot) 锁定 model/base_url,api_key 恒从当前活跃配置现取
  (轮换密钥后旧任务重跑仍能用新 key,但端点/模型被复现口径锁死)。
- job_runner._run_job 在 running 后把快照回填进 job.spec["llm_snapshot"](判据是
  「键不存在」而非值空),并透传给 run_process_job。
- engine.build_config / engine._run_dj 收到 llm_snapshot 后按快照解析(spy)。
- llm-proxy 转发目标按 job.spec["llm_snapshot"] 解析:有快照锁定该端点,
  无快照(job 不存在 / spec 无该键)完全回退现取 —— 老任务零变化。

测试意图:这条链路是"复现"承诺的关键一环——只锁 spec 不改代理,复现是假的
(见任务背景)。故重点验证「快照写入」与「快照生效」两端都对上,且不改变
零快照场景下的既有行为(不能因为这次改造破坏所有老任务/老路径)。
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.dataset import Dataset
from app.models.dataset_version import DatasetVersion
from app.models.job import Job
from app.services import engine, job_runner
from app.services.llm_config import (
    ResolvedLLMConfig,
    get_active_llm_config,
    resolve_llm_config,
    set_active_cache,
    snapshot_active_llm_config,
)

pytestmark = pytest.mark.asyncio

DATASET_ID = "dset-llmsnap"
VERSION_ID = "dsv-llmsnap"
OPERATORS = [{"name": "text_length_filter", "params": {"min_len": 5}}]


@pytest.fixture(autouse=True)
def _reset_llm_cache() -> None:
    """llm_config._active 是模块级全局态,逐用例清空避免跨用例串味。"""
    set_active_cache(None)
    yield
    set_active_cache(None)


async def _seed(session_factory: async_sessionmaker) -> None:
    async with session_factory() as session:
        session.add(Dataset(id=DATASET_ID, name="快照测试集"))
        session.add(
            DatasetVersion(
                id=VERSION_ID,
                dataset_id=DATASET_ID,
                version_no=1,
                storage_uri="/data/datasets/llmsnap.jsonl",
                format="jsonl",
            )
        )
        await session.commit()


# 注:纯逻辑用例不请求 client/DB(快);需 DB 的用例显式请求 client(其自带
# seed_users + admin 登录,见 conftest)/session_factory,不再叠加 autouse fixture
# ——早期版本用 autouse `_admin_session(client, seed_users)` 会重复请求 seed_users
# 致 users_pkey 冲突。


# ── 1. resolve_llm_config / snapshot_active_llm_config 纯逻辑 ──


async def test_resolve_none_equals_active_config() -> None:
    """snapshot 为 None 时,resolve_llm_config 与 get_active_llm_config 完全等价。"""
    set_active_cache(
        ResolvedLLMConfig(
            base_url="https://a.example.com/v1", api_key="sk-a", model="m-a"
        )
    )
    assert resolve_llm_config(None) == get_active_llm_config()

    # 无激活配置(纯 env 回退)时同样等价
    set_active_cache(None)
    assert resolve_llm_config(None) == get_active_llm_config()


async def test_snapshot_none_without_active_provider() -> None:
    """没有激活配置(_active 缓存为空)时,快照没有意义 → 返回 None。"""
    set_active_cache(None)
    assert snapshot_active_llm_config() is None


async def test_snapshot_excludes_api_key() -> None:
    """快照只含 model/base_url,断言绝不含 api_key(不落库泄漏凭据)。"""
    set_active_cache(
        ResolvedLLMConfig(
            base_url="https://a.example.com/v1",
            api_key="sk-should-not-leak",
            model="m-a",
        )
    )
    snap = snapshot_active_llm_config()
    assert snap == {"model": "m-a", "base_url": "https://a.example.com/v1"}
    assert "api_key" not in snap
    assert "sk-should-not-leak" not in str(snap)


async def test_resolve_snapshot_locks_model_base_url_but_key_current() -> None:
    """快照覆盖 model/base_url;api_key 恒从当前活跃配置现取(密钥轮换后仍生效)。"""
    set_active_cache(
        ResolvedLLMConfig(
            base_url="https://old.example.com/v1", api_key="sk-old", model="m-old"
        )
    )
    snap = snapshot_active_llm_config()

    # 密钥轮换/切换供应商:当前活跃配置变了
    set_active_cache(
        ResolvedLLMConfig(
            base_url="https://new.example.com/v1", api_key="sk-new", model="m-new"
        )
    )
    resolved = resolve_llm_config(snap)
    assert resolved.model == "m-old"  # 锁定快照
    assert resolved.base_url == "https://old.example.com/v1"  # 锁定快照
    assert resolved.api_key == "sk-new"  # 恒现取,不是快照那一份(快照本就没存)


# ── 2. job_runner._run_job 回填快照并透传 ──


async def test_run_job_backfills_llm_snapshot_and_threads_to_runner(
    client: AsyncClient,
    session_factory: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """process 类 job 跑完后 job.spec["llm_snapshot"] 有值,且原样透传给 run_process_job。"""  # noqa: E501
    await _seed(session_factory)
    set_active_cache(
        ResolvedLLMConfig(
            base_url="https://active.example.com/v1",
            api_key="sk-active",
            model="m-active",
        )
    )

    captured: dict = {}

    async def fake_run(
        session,
        *,
        job_id,
        input_version,
        operators,
        text_keys,
        use_ray,
        media_keys,
        target_members,
        member_configs,
        llm_snapshot,
    ):
        captured["llm_snapshot"] = llm_snapshot
        return None, "process: []", "/tmp/run.log"

    monkeypatch.setattr("app.services.job_runner.run_process_job", fake_run)

    resp = await client.post(
        "/api/v1/jobs",
        json={
            "name": "快照回填",
            "type": "clean",
            "datasetVersionId": VERSION_ID,
            "operators": OPERATORS,
        },
    )
    assert resp.status_code == 200, resp.text
    job_id = resp.json()["data"]["id"]

    await job_runner.drain()

    detail = (await client.get(f"/api/v1/jobs/{job_id}")).json()["data"]
    assert detail["state"] == "success"

    async with session_factory() as session:
        job = await session.get(Job, job_id)
        # 老 spec(建任务时未含该键)跑完 _run_job 后被回填
        assert job.spec.get("llm_snapshot") == {
            "model": "m-active",
            "base_url": "https://active.example.com/v1",
        }
    # 透传给了 run_process_job(而非重新现取)
    assert captured["llm_snapshot"] == {
        "model": "m-active",
        "base_url": "https://active.example.com/v1",
    }


async def test_run_job_backfills_none_snapshot_when_no_active_provider(
    client: AsyncClient,
    session_factory: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """无激活配置时快照为 None——判据用「键不存在」而非「值空」,None 也要落库锁定。"""
    await _seed(session_factory)
    set_active_cache(None)  # 无激活 LLM 配置

    async def fake_run(
        session,
        *,
        job_id,
        input_version,
        operators,
        text_keys,
        use_ray,
        media_keys,
        target_members,
        member_configs,
        llm_snapshot,
    ):
        return None, "process: []", "/tmp/run.log"

    monkeypatch.setattr("app.services.job_runner.run_process_job", fake_run)

    resp = await client.post(
        "/api/v1/jobs",
        json={
            "name": "无激活配置",
            "type": "clean",
            "datasetVersionId": VERSION_ID,
            "operators": OPERATORS,
        },
    )
    job_id = resp.json()["data"]["id"]
    await job_runner.drain()

    async with session_factory() as session:
        job = await session.get(Job, job_id)
        assert job.state == "success"
        # 键必须存在(锁定"当时无激活配置"这个语义),值为 None
        assert "llm_snapshot" in job.spec
        assert job.spec["llm_snapshot"] is None


# ── 3. engine.build_config / engine._run_dj 收到快照(spy) ──


async def test_build_config_injects_model_from_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """build_config 对 needs_api 算子注入的 api_model 取自快照,而非当前活跃配置。"""
    set_active_cache(
        ResolvedLLMConfig(
            base_url="https://active.example.com/v1",
            api_key="sk-active",
            model="m-active",
        )
    )
    snap = {"model": "m-locked", "base_url": "https://locked.example.com/v1"}

    def _fake_get_operator(name: str):
        return {"params": [{"name": "api_model"}]}

    monkeypatch.setattr(engine.oc, "get_operator", _fake_get_operator)

    cfg = engine.build_config(
        project_name="p",
        input_path="in.jsonl",
        output_path="out.jsonl",
        operators=[{"name": "some_llm_mapper", "params": {}}],
        llm_snapshot=snap,
    )
    injected = cfg["process"][0]["some_llm_mapper"]["api_model"]
    assert injected == "m-locked"  # 快照的模型,不是当前活跃的 m-active


async def test_run_dj_env_reflects_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    """_run_dj 透传 llm_snapshot 给 _subprocess_env:子进程 env 按快照解析。"""
    set_active_cache(
        ResolvedLLMConfig(
            base_url="https://active.example.com/v1",
            api_key="sk-current",
            model="m-active",
        )
    )
    snap = {"model": "m-locked", "base_url": "https://locked.example.com/v1"}

    captured: dict = {}

    class _P:
        pid = 4242
        returncode = None

        async def communicate(self):
            self.returncode = 0
            return b"", b""

        async def wait(self):
            return 0

    async def _fake_create(*args, **kwargs):
        captured.update(kwargs)
        return _P()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create)
    code, _ = await engine._run_dj(Path("/tmp/x.yaml"), llm_snapshot=snap)
    assert code == 0
    env = captured["env"]
    # base_url 锁定快照;api_key 恒现取当前活跃配置(密钥永不落快照)
    assert env["OPENAI_BASE_URL"] == "https://locked.example.com/v1"
    assert env["OPENAI_API_KEY"] == "sk-current"


# ── 4. llm-proxy 按快照转发(修正 A) ──


class _FakeUpstreamResponse:
    status_code = 200
    headers = {"content-type": "application/json"}
    content = b'{"model":"m","usage":{"prompt_tokens":1,"completion_tokens":2}}'

    def json(self):
        return {"model": "m", "usage": {"prompt_tokens": 1, "completion_tokens": 2}}


def _patch_httpx_capture(monkeypatch: pytest.MonkeyPatch) -> dict:
    """把 llm_proxy 模块内的 httpx.AsyncClient 替换成记录请求 URL 的桩。"""
    captured: dict = {}

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def post(self, url, *, content, headers):
            captured["url"] = url
            return _FakeUpstreamResponse()

    monkeypatch.setattr("app.api.v1.llm_proxy.httpx.AsyncClient", _FakeAsyncClient)
    return captured


async def test_proxy_forwards_to_snapshot_base_url(
    client: AsyncClient,
    session_factory: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """有快照的 job:转发目标锁定快照 base_url,即便当前活跃配置已切走。"""
    async with session_factory() as session:
        session.add(
            Job(
                id="job-snap1",
                name="snap",
                type="clean",
                state="running",
                progress=50,
                spec={
                    "llm_snapshot": {
                        "model": "m-locked",
                        "base_url": "https://locked.example.com/v1",
                    }
                },
            )
        )
        await session.commit()

    # 当前活跃配置已经换了供应商——代理不该用这个
    set_active_cache(
        ResolvedLLMConfig(
            base_url="https://current.example.com/v1",
            api_key="sk-current",
            model="m-current",
        )
    )

    captured = _patch_httpx_capture(monkeypatch)

    resp = await client.post(
        "/api/v1/llm-proxy/job-snap1/chat/completions",
        json={"model": "m-locked", "messages": []},
    )
    assert resp.status_code == 200, resp.text
    assert captured["url"] == "https://locked.example.com/v1/chat/completions"


async def test_proxy_falls_back_to_active_config_without_snapshot(
    client: AsyncClient,
    session_factory: async_sessionmaker,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """老任务零变化:job 不存在 / spec 无 llm_snapshot 键 → 完全等价现取活跃配置。"""
    set_active_cache(
        ResolvedLLMConfig(
            base_url="https://current.example.com/v1",
            api_key="sk-current",
            model="m-current",
        )
    )
    captured = _patch_httpx_capture(monkeypatch)

    # 4a. job 根本不存在(如内联过滤路径的伪 job_id)
    resp = await client.post(
        "/api/v1/llm-proxy/job-unknown/chat/completions",
        json={"model": "m-current", "messages": []},
    )
    assert resp.status_code == 200, resp.text
    assert captured["url"] == "https://current.example.com/v1/chat/completions"

    # 4b. job 存在但 spec 无 llm_snapshot 键(早于本特性的老任务)
    async with session_factory() as session:
        session.add(
            Job(
                id="job-old1",
                name="old",
                type="clean",
                state="running",
                progress=50,
                spec={"datasetVersionId": VERSION_ID, "operators": OPERATORS},
            )
        )
        await session.commit()

    captured.clear()
    resp = await client.post(
        "/api/v1/llm-proxy/job-old1/chat/completions",
        json={"model": "m-current", "messages": []},
    )
    assert resp.status_code == 200, resp.text
    assert captured["url"] == "https://current.example.com/v1/chat/completions"
