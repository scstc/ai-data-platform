"""运行时环境能力探测:决定算子的"有效可运行状态"。

算子真正在 data-juicer venv(py3.11)的子进程里执行,故 GPU/Ray 能力必须探测
**DJ venv** 而非后端 venv(后端 py3.12 没装 torch)。探测失败一律视作"无该能力",
绝不抛出——目录/守门接口不能因探测异常而 500。

各能力的刷新策略不同:GPU/Ray 运行期不变,缓存进程生命周期;vLLM 服务会起停,
带 TTL 缓存;LLM 实时读配置。``.env`` 可用 ``*_FORCE`` 项强制覆盖探测结果(调试/CI)。
"""

from __future__ import annotations

import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path

from app.core.config import settings

# 子进程探测超时(秒):DJ venv 冷启 import torch 可能略慢
_PROBE_TIMEOUT = 30.0
# vLLM 服务探测缓存 TTL(秒):服务会起停,不能永久缓存
_VLLM_TTL = 30.0


@dataclass(frozen=True)
class Capabilities:
    """当前环境探测到的执行能力集。"""

    cuda: bool
    vllm: bool
    ray: bool
    llm: bool


def _dj_python() -> Path:
    """DJ venv 的 python 解释器(与 dj_process_bin 同目录)。

    Windows ``Scripts/dj-process.exe`` → ``Scripts/python.exe``;
    POSIX ``bin/dj-process`` → ``bin/python``。
    """
    p = Path(settings.dj_process_bin)
    return p.with_name("python.exe") if p.suffix == ".exe" else p.with_name("python")


def _probe_dj(snippet: str) -> bool:
    """在 DJ venv 跑一段打印 '1'/'0' 的探测代码;非 '1' 或异常 → False。"""
    py = _dj_python()
    if not py.exists():
        return False
    try:
        out = subprocess.run(
            [str(py), "-c", snippet],
            capture_output=True,
            text=True,
            timeout=_PROBE_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0 and out.stdout.strip().endswith("1")


# --- 各能力探测(带各自缓存) ------------------------------------------------
_cuda: bool | None = None
_ray: bool | None = None
_vllm_cache: tuple[float, bool] | None = None


def _detect_cuda() -> bool:
    global _cuda
    if settings.cuda_force is not None:
        return settings.cuda_force
    if _cuda is None:
        _cuda = _probe_dj(
            "import torch,sys;"
            "sys.stdout.write('1' if torch.cuda.is_available() else '0')"
        )
    return _cuda


def _detect_ray() -> bool:
    global _ray
    if settings.ray_force is not None:
        return settings.ray_force
    # Ray 需显式开启 + DJ venv 真装了 ray(executor 支持见引擎改造)
    if not settings.ray_enabled:
        return False
    if _ray is None:
        _ray = _probe_dj("import ray,sys;sys.stdout.write('1')")
    return _ray


def _detect_vllm() -> bool:
    global _vllm_cache
    if settings.vllm_force is not None:
        return settings.vllm_force
    base = settings.vllm_base_url
    if not base:
        return False
    now = time.monotonic()
    if _vllm_cache is not None and now - _vllm_cache[0] < _VLLM_TTL:
        return _vllm_cache[1]
    ok = False
    try:
        with urllib.request.urlopen(
            base.rstrip("/") + "/v1/models", timeout=3.0
        ) as resp:
            ok = 200 <= resp.status < 300
    except (urllib.error.URLError, OSError, ValueError):
        ok = False
    _vllm_cache = (now, ok)
    return ok


def get_capabilities() -> Capabilities:
    """当前环境能力集(各能力按自身策略缓存)。"""
    return Capabilities(
        cuda=_detect_cuda(),
        vllm=_detect_vllm(),
        ray=_detect_ray(),
        llm=bool(settings.openai_api_key),
    )


def capabilities_api() -> dict[str, bool]:
    """能力集出参(供前端「环境能力」指示)。"""
    return asdict(get_capabilities())


def reset_cache() -> None:
    """清空探测缓存(测试 / 手动刷新用)。"""
    global _cuda, _ray, _vllm_cache
    _cuda = _ray = _vllm_cache = None
