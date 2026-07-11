"""内容安全审核引擎(services/review.py)单测:LLM 故障告警 + 坏正则跳过去重。

不连库(scan_version 只吃 rows/config/provider,纯内存),覆盖两条此前的
静默降级点:
- LLM 服务故障时,warnings 里必须出现「LLM 服务故障,N 条未评分」这个可被
  下游(review_runner)按契约 C 转写进 Job.warnings 的措辞,而不是此前"降级
  但用户完全看不见"的哑巴状态。
- 一条运行期抛异常的自定义正则,不能因为它在每一行都命中同一个异常,就在
  warnings 里重复出现 N 次(N=扫描行数)——业务上这是"这条规则坏了,该修"
  的一次性信号,刷屏会淹没其他真正有用的 warning。
"""

from __future__ import annotations

import pytest

from app.services import review


class _FailingProvider:
    """moderate_texts 必然抛出,模拟 LLM 服务故障(网络/超时/鉴权失败等)。"""

    async def moderate_texts(self, texts: list[str]) -> list[dict]:
        raise RuntimeError("connection refused")


class _CleanProvider:
    """moderate_texts 正常返回,但没有一行命中(用于反证:非故障路径不应
    出现"LLM 服务故障"字样,才能证明该 warning 确实只在故障时才出现)。
    """

    async def moderate_texts(self, texts: list[str]) -> list[dict]:
        return [{"flagged": False} for _ in texts]


@pytest.mark.asyncio
async def test_llm_failure_produces_distinguishable_warning() -> None:
    """业务意图:LLM 故障时,行为上与"LLM 扫过且干净"完全一样(两者都不会
    给任何行打 llm 命中)——如果不主动 warning,运营方看到的报告会误以为
    这批数据已经过 LLM 审核且通过,而实际上 LLM 根本没跑起来。
    """
    rows = [{"text": "正常文本"} for _ in range(3)]
    config = {"useLlm": True, "usePii": False, "useFlaggedWords": False}

    _findings, _tagged, report = await review.scan_version(
        rows, config, provider=_FailingProvider()
    )

    assert any(
        w.startswith("LLM 服务故障") and "3 条未评分" in w for w in report["warnings"]
    ), report["warnings"]


@pytest.mark.asyncio
async def test_llm_success_does_not_emit_failure_warning() -> None:
    """反证:LLM 正常跑完(即使全部判定不违规)不应出现"LLM 服务故障"字样,
    否则该 warning 就失去了区分"故障"与"扫过且干净"的意义。
    """
    rows = [{"text": "正常文本"}]
    config = {"useLlm": True, "usePii": False, "useFlaggedWords": False}

    _findings, _tagged, report = await review.scan_version(
        rows, config, provider=_CleanProvider()
    )

    assert not any("LLM 服务故障" in w for w in report["warnings"])


@pytest.mark.asyncio
async def test_bad_regex_skip_warning_deduplicated_across_rows() -> None:
    """业务意图:一条自定义正则在运行期对每一行都抛异常(如变量宽度环视等
    Python re 不支持的构造在 compile 阶段可能通过、search 阶段才炸),这是
    "这条规则坏了"这一个事实,不是"这一行有个问题"——warning 必须按规则名
    去重,只出现一次,否则扫 500 行就是 500 条一模一样的 warning,把真正有
    用的信息淹没。
    """
    # re.compile 本身不会失败,但用 monkeypatch 让 search 在运行期抛异常,
    # 模拟"编译期看似合法、运行期才出问题"的正则(如灾难性回溯超时场景的
    # 简化版——这里直接模拟异常本身,不依赖真实超时耗时)。
    import re

    class _BoomPattern:
        def search(self, text: str) -> None:
            raise RecursionError("simulated catastrophic backtracking")

    original_compile = re.compile

    def _fake_compile(pattern: str, *a, **kw):
        if pattern == "BOOM":
            return _BoomPattern()
        return original_compile(pattern, *a, **kw)

    rows = [{"text": f"第 {i} 行文本"} for i in range(5)]
    config = {
        "useLlm": False,
        "usePii": False,
        "useFlaggedWords": False,
        "customRegex": [{"name": "坏规则", "pattern": "BOOM"}],
    }

    import unittest.mock

    with unittest.mock.patch("re.compile", side_effect=_fake_compile):
        _findings, _tagged, report = await review.scan_version(rows, config)

    skip_warnings = [w for w in report["warnings"] if "坏规则" in w]
    assert len(skip_warnings) == 1, skip_warnings
    assert "已跳过" in skip_warnings[0]
