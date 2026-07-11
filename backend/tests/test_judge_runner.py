"""裁判任务编排(services/judge_runner.py)DB 级测试:LLM 服务故障不能悄悄
算成功。

此前:裁判 provider 调用失败时,全部行被静默置为 verdict=unscored,与
"completion 字段缺失、真的没法评"外观完全一样,job 仍以 state=success 收尾,
job.eval_report 里虽有一条 warning 字符串,但没有任何结构化信号能让上游/前端
区分"这批数据被评过且没通过"与"评审服务本身就没跑起来"。

本测试覆盖修复后的行为:
- 故障条目 EvalResult.verdict == "error"(不与 unscored 混同)。
- run_judge 必须抛 JudgeError(交给 job_runner 落 failed,不能悄悄 success)。
- Job.warnings(契约 C 的专用列)必须出现"LLM 服务故障"字样,不只是埋在
  job.eval_report 里等着被忽略。

按纪律:DB 级测试连 adp_test(共享慢库),本轮只写不跑,留给统一验证阶段。
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.models.dataset import Dataset
from app.models.dataset_version import DatasetVersion
from app.models.eval_result import EvalResult
from app.models.job import Job
from app.services import judge_runner


class _FailingJudgeProvider:
    """judge_answers 必然抛出,模拟裁判 LLM 服务故障(网络/超时/鉴权失败等)。"""

    async def judge_answers(self, items: list[dict]) -> list[dict]:
        raise RuntimeError("upstream 503")


def _write_completions(path: Path, n: int) -> None:
    rows = [
        {"prompt": f"问题{i}", "response": f"参考答案{i}", "completion": f"回答{i}"}
        for i in range(n)
    ]
    path.write_text(
        "\n".join(json.dumps(r, ensure_ascii=False) for r in rows) + "\n",
        encoding="utf-8",
    )


@pytest.mark.asyncio
async def test_judge_llm_failure_marks_error_and_fails_job(
    session_factory: async_sessionmaker, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """业务意图:裁判服务整体故障 ≠ 数据合法地评不出分(如缺 completion)。
    前者是运行环境问题、应该让任务失败并提示重试;后者是数据问题、任务仍可
    判成功。混为一谈会让运营方误信"这批模型回答已经评过并且没问题"。
    """
    data_path = tmp_path / "data.jsonl"
    _write_completions(data_path, 3)

    monkeypatch.setattr(
        judge_runner, "get_ai_provider", lambda *a, **kw: _FailingJudgeProvider()
    )

    async with session_factory() as session:
        dataset = Dataset(id="dset-judge1", name="judge-ds")
        version = DatasetVersion(
            id="dsv-judge1",
            dataset_id=dataset.id,
            version_no=1,
            storage_uri=str(data_path),
            format="jsonl",
            rows=3,
            size=data_path.stat().st_size,
        )
        job = Job(
            id="job-judge1",
            name="裁判任务",
            type="judge",
            state="running",
        )
        session.add_all([dataset, version, job])
        await session.commit()

        with pytest.raises(judge_runner.JudgeError, match="LLM 服务故障"):
            await judge_runner.run_judge(
                session, job=job, version=version, config={"use_llm": True}
            )

        # run_judge 未自行 commit(交给调用方 job_runner 统一提交),这里手动
        # 提交一次,模拟 job_runner 捕获 JudgeError 后 job.state="failed" +
        # 最终 commit 的效果,断言待提交的数据确实落地。
        await session.commit()

        results = (
            await session.execute(
                select(EvalResult).where(EvalResult.job_id == job.id)
            )
        ).scalars().all()
        assert len(results) == 3
        assert all(r.verdict == "error" for r in results)
        assert all(r.score is None for r in results)

        await session.refresh(job)
        assert job.warnings is not None
        assert any("LLM 服务故障" in w and "3 条未评分" in w for w in job.warnings)
