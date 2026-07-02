"""加工引擎:把算子编排生成 data-juicer YAML → 子进程跑 dj-process → 产出新版本。

设计见 docs/plan/02(集成)与 03(模型)。后端(py3.12)通过子进程调用
data-juicer venv(py3.11)的 dj-process,进程隔离、规避版本冲突。
多 job 并发由信号量限流;单 job 内并行由 np 控制。
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import signal
import tempfile
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.dataset_version import DatasetVersion
from app.models.dataset_version_table import DatasetVersionTable
from app.models.job_input import JobInput
from app.services import operator_catalog as oc
from app.services.external_store import (
    cached_bytes,
    materialized_version,
    parse_s3_uri,
    persist_manifest_output,
    platform_config,
    upload_file_to_uploads,
    upload_parquet_file_to_uploads,
)
from app.services.landing import MANIFEST_FORMAT, parquet_bytes_to_records
from app.services.llm_config import get_active_llm_config

# 多 job 并发上限
_semaphore = asyncio.Semaphore(settings.engine_concurrency)

# 运行中子进程注册表(单 worker 进程内有效):job_id -> dj-process 子进程。
# 由 _run_dj 在进程起止时维护,供 terminate_job 停止任务。
_running_procs: dict[str, asyncio.subprocess.Process] = {}


def _subprocess_env() -> dict[str, str] | None:
    """dj-process 子进程环境。

    平台配了 LLM 时,把 OPENAI_* 注入子进程环境——needs_api 算子经 DJ 的 openai
    客户端从环境变量读取凭证(pydantic 只把 .env 读进 settings,不写 os.environ,
    故不显式注入子进程就拿不到)。未配 LLM 则返回 None,子进程直接继承当前环境。
    """
    cfg = get_active_llm_config()
    if not cfg.api_key:
        return None
    env = dict(os.environ)
    env["OPENAI_API_KEY"] = cfg.api_key
    if cfg.base_url:
        env["OPENAI_BASE_URL"] = cfg.base_url
    return env


def _kill_proc_tree(proc: asyncio.subprocess.Process) -> None:
    """整组杀:连同 dj-process fork 出的子孙(如 uv/pip 装依赖)一起 SIGKILL。

    子进程以 start_new_session=True 自成进程组(pgid==pid),故对进程组发信号即可。
    只杀 dj 自己会留孤儿继续跑、还堵住 stdout 管道让 communicate() 卡死(漏临时目录 +
    占住并发槽)。进程已退出 / 取不到组 → 退回单进程杀,异常吞掉(已死即达成目的)。
    """
    if proc.returncode is not None:
        return
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        try:
            proc.kill()
        except ProcessLookupError:
            pass


def terminate_job(job_id: str) -> bool:
    """杀掉某 job 正在跑的 dj-process 子进程(连同其子孙);返回是否确有进程被杀。"""
    proc = _running_procs.get(job_id)
    if proc is None:
        return False
    _kill_proc_tree(proc)
    return True


# 多模态引擎(torch)就绪缓存:媒体/manifest 加工需 torch,无则提前拦截而非运行时拉装
_multimodal_ready: bool | None = None


async def multimodal_ready() -> bool:
    """DJ venv 是否装了 torch(媒体/manifest 加工所需)。结果缓存,避免每次起子进程探测。

    纯文本加工不需 torch、永不调用本检查;只有 manifest 输入才据此门控,
    没装 torch 的部署直接 400,绝不让 DJ 运行时 ``uv pip install torch`` 卡死。
    """
    global _multimodal_ready
    if _multimodal_ready is not None:
        return _multimodal_ready
    dj_python = str(Path(settings.dj_process_bin).with_name("python"))
    try:
        proc = await asyncio.create_subprocess_exec(
            dj_python,
            "-c",
            "import torch",
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()
    except OSError:
        return False  # 探测本身失败(如 python 不存在)不缓存,下次再试
    _multimodal_ready = proc.returncode == 0
    return _multimodal_ready


class EngineError(RuntimeError):
    """加工执行失败(dj-process 非零退出 / 无产物 / 超时)。"""


def _new_version_id() -> str:
    return f"dsv-{secrets.token_hex(3)}"


def _new_member_id() -> str:
    return f"dsvt-{secrets.token_hex(3)}"


def build_config(
    *,
    project_name: str,
    input_path: str,
    output_path: str,
    operators: list[dict[str, Any]],
    text_key: str | None = None,
    text_keys: list[str] | None = None,
    executor_type: str | None = None,
    ray_address: str | None = None,
    media_keys: dict[str, str] | None = None,
) -> dict[str, Any]:
    """把算子编排序列化为 data-juicer 合法配置(dict)。

    operators: [{name, params}]; 无参算子 process 项值为 None(DJ 接受)。

    配了 LLM 时,为带 ``api_model`` 参数的算子(needs_api)注入平台配置的模型名
    ——DJ 该参数默认写死 ``gpt-4o``,不覆盖会向自定义端点请求不存在的模型而失败;
    用户在表单里显式填了 ``api_model`` 则尊重用户值。

    text_key / text_keys:数据主文本字段名。DJ 默认 text_key='text',数据无 text
    字段时(如新闻用 title)必须显式指定,否则 load_dataset 报 'no key [text]'。
    text_keys(列表、可多字段,用户显式选择)优先于 text_key(单字段,自动探测);
    两者皆空时不写(用 DJ 默认 ['text'])。

    executor_type / ray_address(G6 分布式):仅当 executor_type 为 'ray'/'ray_partitioned'
    时写入(DJ config.py 合法 choices);'default'/None 不写,保持 DJ 缺省单机。
    media_keys(G7 多模态):{image_key/audio_key/video_key: 字段名};仅写非空值,
    供 manifest 加工显式指定媒体字段(默认 images/audios/videos 与本平台 manifest 对齐)。
    注意:**绝不注入 export_stats**——它不是 DJ 的 jsonargparse 配置键(只是 Exporter
    构造参数,default 模式恒 True),写进 YAML 会被当未知参数拒绝致 dj-process 崩溃。
    """
    cfg = get_active_llm_config()
    process: list[dict[str, Any]] = []
    for op in operators:
        params = dict(op.get("params") or {})
        if cfg.api_key:
            meta = oc.get_operator(op["name"])
            valid = {p["name"] for p in meta.get("params", [])} if meta else set()
            # DJ 各算子模型参数名不统一(api_model / api_or_hf_model),默认都写死
            # gpt-4o;不覆盖会向自定义端点请求不存在的模型而失败。用户显式填了则尊重。
            for model_key in ("api_model", "api_or_hf_model"):
                if model_key in valid and model_key not in params:
                    params[model_key] = cfg.model
        process.append({op["name"]: (params or None)})
    # 路径统一正斜杠:DJ 用 POSIX shlex 解析 dataset_path,Windows 反斜杠
    # 会被当转义符吞掉,路径残缺后被误判成 huggingface 数据集
    result = {
        "project_name": project_name,
        "dataset_path": Path(input_path).as_posix(),
        "np": settings.engine_np,
        "export_path": Path(output_path).as_posix(),
        "process": process,
    }
    # 自定义算子(算子市场上传):流水线含自定义算子时,把其源文件绝对路径喂给 DJ
    # 的 custom_operator_paths(见 data-juicer/data_juicer/config/config.py
    # load_custom_operators),使 dj-process 子进程启动时动态注册进 OPERATORS。
    custom_paths: list[str] = []
    for op in operators:
        meta = oc.get_operator(op["name"])
        if meta and meta.get("is_custom") and meta.get("source_object_key"):
            path = (
                Path(settings.upload_dir)
                / "custom_operators"
                / meta["source_object_key"]
            )
            posix = path.as_posix()
            if posix not in custom_paths:
                custom_paths.append(posix)
    if custom_paths:
        result["custom_operator_paths"] = custom_paths
    if text_keys:
        # 用户显式指定(可多字段)优先;DJ 配置项是 text_keys(复数、列表)
        result["text_keys"] = list(text_keys)
    elif text_key:
        # 自动探测的单字段:DJ 默认 ["text"],非 text 字段须显式指定
        result["text_keys"] = [text_key]
    # G6:仅在切 ray 时写 executor_type(default 不写,保持 DJ 缺省)
    if executor_type in ("ray", "ray_partitioned"):
        result["executor_type"] = executor_type
        result["ray_address"] = ray_address or "auto"
    # G7:媒体字段键(只写白名单内的非空值)
    if media_keys:
        for k in ("image_key", "audio_key", "video_key"):
            if media_keys.get(k):
                result[k] = media_keys[k]
    return result



# 仅运行期有意义、对用户无价值的内部键:中转输入路径(S3 对象的一次性本地副本)、
# 本地工作产出路径、job_id 充当的 project_name、单机并行度 np。落库展示前剥掉,
# 用户看到的只剩"配方"(算子链 + 清洗字段)。磁盘上喂给 dj-process 的 job.yaml 不受影响。
_DISPLAY_DROP_KEYS = frozenset(
    {"project_name", "dataset_path", "export_path", "np"}
)


def config_yaml_for_display(yaml_text: str) -> str:
    """把执行用 YAML 清理成面向用户的展示版:剥掉内部中转/运行期键。

    解析失败或非 dict(理论上不会)时原样返回,绝不因展示美化而丢真实配置。
    """
    try:
        cfg = yaml.safe_load(yaml_text)
    except yaml.YAMLError:
        return yaml_text
    if not isinstance(cfg, dict):
        return yaml_text
    kept = {k: v for k, v in cfg.items() if k not in _DISPLAY_DROP_KEYS}
    return yaml.safe_dump(kept, allow_unicode=True, sort_keys=False)


async def _run_dj(yaml_path: Path, *, job_id: str | None = None) -> tuple[int, str]:
    """异步起 dj-process 子进程,返回 (退出码, 合并日志)。

    传 job_id 时把子进程登记进 _running_procs(供 terminate_job 停止);进程结束即注销。
    超过 settings.engine_job_timeout 秒(>0 时)则杀进程并抛 EngineError。
    """
    proc = await asyncio.create_subprocess_exec(
        settings.dj_process_bin,
        "--config",
        str(yaml_path),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        # 自成进程组:停止/超时时可整组杀,连带 dj fork 出的子孙(uv/pip 等)
        start_new_session=True,
        # 配了 LLM 时把 OPENAI_* 注入,供 needs_api 算子的 openai 客户端读取
        env=_subprocess_env(),
    )
    if job_id is not None:
        _running_procs[job_id] = proc
    timeout = settings.engine_job_timeout if settings.engine_job_timeout > 0 else None
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        _kill_proc_tree(proc)
        await proc.wait()
        raise EngineError(
            f"加工超时(超过 {settings.engine_job_timeout}s),已终止"
        ) from None
    finally:
        if job_id is not None:
            _running_procs.pop(job_id, None)
    return proc.returncode or 0, out.decode("utf-8", "replace")


def _read_jsonl_head(path: Path, limit: int) -> list[dict[str, Any]]:
    """读 jsonl 文件前 limit 个非空行,逐行 json.loads。limit<=0 时读全部。"""
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            rows.append(json.loads(line))
            if limit > 0 and len(rows) >= limit:
                break
    return rows


def _read_head_records(path: Path, n: int) -> list[dict[str, Any]]:
    """取数据文件前 N 行 dict(jsonl 逐行 / parquet 读表),供 text_key 探测。"""
    if path.suffix == ".parquet":
        return parquet_bytes_to_records(path.read_bytes(), limit=n)
    return _read_jsonl_head(path, n)


# 常见文本字段名:数据无 text 时按此优先级匹配主文本字段(text_key)
_TEXT_KEY_CANDIDATES = (
    "text",
    "content",
    "body",
    "title",
    "sentence",
    "document",
    "passage",
    "prompt",
    "question",
    "answer",
    "response",
    "description",
    "raw",
)


def detect_text_key(records: list[dict[str, Any]]) -> str | None:
    """从前若干条记录推断主文本字段名,供 build_config 设 text_key。

    data-juicer 默认 text_key='text';数据无 text 时(如新闻用 title)必须显式指定,
    否则 load_dataset 报 'no key [text]'。优先级:已知文本字段名 > 平均值最长的字符串字段。
    仅看值为 str 的字段;无字符串字段返回 None(交给 DJ 默认/由其报错)。
    """
    if not records:
        return None
    str_lens: dict[str, list[int]] = {}
    for r in records[:50]:
        if not isinstance(r, dict):
            continue
        for k, v in r.items():
            if isinstance(v, str):
                # 防御:剥离首键可能残留的 U+FEFF BOM(CSV 转 JSON 粘到列名上)
                str_lens.setdefault(k.lstrip("﻿"), []).append(len(v))
    if not str_lens:
        return None
    for cand in _TEXT_KEY_CANDIDATES:
        if cand in str_lens:
            return cand
    # 兜底:平均长度最长的字符串字段(最可能是正文)
    return max(str_lens, key=lambda k: sum(str_lens[k]) / len(str_lens[k]))


def _column_union(*row_lists: list[dict[str, Any]]) -> list[str]:
    """按出现顺序求多组样本的键并集(dict.fromkeys 保序去重)。"""
    keys: dict[str, None] = {}
    for rows in row_lists:
        for row in rows:
            keys.update(dict.fromkeys(row.keys()))
    return list(keys)


async def filter_records(
    records: list[dict[str, Any]],
    operators: list[dict[str, Any]],
    *,
    project_name: str = "ingest-filter",
) -> tuple[list[dict[str, Any]], str]:
    """对一批记录跑算子流水线过滤/清洗,返回 (存活记录, 运行日志)。

    供采集连接器在 fetch 之后、land 之前内联调用:records 进 → 落临时 jsonl →
    dj-process 跑算子 → 读回存活记录。无算子或无记录则原样返回。
    全程在临时目录内完成,不建 DatasetVersion、不写 DB。dj-process 失败抛
    EngineError(由连接器转 IngestError,诚实失败不伪成功)。
    """
    if not operators or not records:
        return records, ""
    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)
        in_path = tmp_dir / "in.jsonl"
        out_path = tmp_dir / "out.jsonl"
        yaml_path = tmp_dir / "job.yaml"
        in_path.write_text(
            "".join(
                json.dumps(r, ensure_ascii=False, default=str) + "\n"
                for r in records
            ),
            encoding="utf-8",
        )
        cfg = build_config(
            project_name=project_name,
            input_path=str(in_path),
            output_path=str(out_path),
            operators=operators,
            text_key=detect_text_key(records),
        )
        yaml_path.write_text(
            yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
        async with _semaphore:
            code, log = await _run_dj(yaml_path)
        if code != 0 or not out_path.exists():
            tail = "\n".join(log.strip().splitlines()[-8:])
            raise EngineError(f"算子过滤失败(dj-process 退出码 {code})\n{tail}")
        # limit<=0 读全部存活行(全部被过滤掉时 out.jsonl 为空 → 返回 [])
        kept = _read_jsonl_head(out_path, 0)
    return kept, log


async def run_preview(
    session: AsyncSession,
    *,
    input_version: DatasetVersion,
    operators: list[dict[str, Any]],
    sample_size: int = 20,
    text_keys: list[str] | None = None,
) -> dict[str, Any]:
    """在输入版本的前 sample_size 行上试跑算子流水线,返回加工前后样本。

    全程在临时目录内完成,不建 DatasetVersion、不写 DB。失败抛 EngineError。
    返回 {before, after, beforeCount, afterCount, columns}。

    输入经 materialized_version 解析:受管版本直接用本地路径,hosted 版本按需
    从 S3 拉取并规范化为临时 jsonl(用完即清理)。
    """
    # 经解析器拿本地可读路径(hosted 在此下载;managed 透传本地路径)
    async with materialized_version(input_version, session) as src_path:
        # 输入数据文件缺失 → 抛 EngineError(让上层转 400,不让 FileNotFound 冒成 500)
        if not src_path.exists():
            raise EngineError(f"输入版本数据文件不存在:{input_version.storage_uri}")

        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            out_path = tmp_dir / "data.jsonl"
            yaml_path = tmp_dir / "job.yaml"
            # 试跑样例必须与 src 同目录:多模态 manifest 的相对媒体路径由 DJ rel2abs
            # 按 jsonl 所在目录解析,媒体文件就在 src 旁(物化时下载),另起目录会找不到。
            sample_path = (
                src_path.parent / f"preview-sample-{secrets.token_hex(4)}.jsonl"
            )

            # 读输入版本前 sample_size 个非空行:既落盘成试跑输入,也作 before 展示
            before = _read_jsonl_head(src_path, sample_size)
            sample_path.write_text(
                "".join(
                    json.dumps(row, ensure_ascii=False) + "\n" for row in before
                ),
                encoding="utf-8",
            )

            try:
                cfg = build_config(
                    project_name="preview",
                    input_path=str(sample_path),
                    output_path=str(out_path),
                    operators=operators,
                    text_key=None if text_keys else detect_text_key(before),
                    text_keys=text_keys,
                )
                yaml_path.write_text(
                    yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False),
                    encoding="utf-8",
                )

                async with _semaphore:
                    code, log = await _run_dj(yaml_path)

                if code != 0 or not out_path.exists():
                    tail = "\n".join(log.strip().splitlines()[-8:])
                    raise EngineError(f"dj-process 退出码 {code}\n{tail}")

                # 产出总行数(全量统计),after 仅取前 sample_size 条用于展示
                after_count = sum(
                    1 for line in out_path.open(encoding="utf-8") if line.strip()
                )
                after = _read_jsonl_head(out_path, sample_size)
                columns = _column_union(before, after)
            finally:
                sample_path.unlink(missing_ok=True)

    return {
        "before": before,
        "after": after,
        "beforeCount": len(before),
        "afterCount": after_count,
        "columns": columns,
    }


async def _get_version_members(
    session: AsyncSession, version_id: str
) -> list[DatasetVersionTable]:
    """查询版本的所有表成员，按 table_name 排序。"""
    stmt = (
        select(DatasetVersionTable)
        .where(DatasetVersionTable.dataset_version_id == version_id)
        .order_by(DatasetVersionTable.table_name)
    )
    return (await session.execute(stmt)).scalars().all()


async def _materialize_member(
    session: AsyncSession, member: DatasetVersionTable
) -> Path:
    """物化单个成员文件，返回本地路径。

    对于 s3:// URI，下载到临时文件；对于本地路径，直接返回。
    """
    uri = member.storage_uri
    if uri.startswith("s3://"):
        bucket, key = parse_s3_uri(uri)
        cfg = platform_config()
        # 下载到临时文件
        suffix = f".{member.format}"
        tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        try:
            obj_data = await cached_bytes(cfg, bucket, key)
            tmp.write(obj_data)
            tmp.flush()
            return Path(tmp.name)
        finally:
            tmp.close()
    else:
        # 本地路径（managed 存储）
        return Path(uri)


def _get_member_output_path(
    dataset_id: str, version_no: int, table_name: str, format: str
) -> Path:
    """获取成员产出文件路径：datasets/<id>/v<n>/<table>.<format>"""
    out_dir = Path(settings.datasets_dir) / dataset_id / f"v{version_no}"
    out_dir.mkdir(parents=True, exist_ok=True)
    return out_dir / f"{table_name}.{format}"


async def run_process_job(
    session: AsyncSession,
    *,
    job_id: str,
    input_version: DatasetVersion,
    operators: list[dict[str, Any]] | None = None,
    text_keys: list[str] | None = None,
    use_ray: bool = False,
    media_keys: dict[str, str] | None = None,
    target_members: list[str] | None = None,
    member_configs: list[dict[str, Any]] | None = None,
) -> tuple[DatasetVersion, str, str]:
    """对输入版本的指定成员跑算子流水线,产出新版本。

    text_keys:用户显式指定的清洗作用字段(可多字段);留空则按字段名优先级自动探测。
    use_ray(G6):切 DJ ray executor(调用方须先经 capabilities.ray 门控)。
    media_keys(G7):{image_key/audio_key/video_key:字段名},仅 manifest 输入注入。
    target_members:要处理的成员名列表;None=处理所有成员(向后兼容,需配合 operators)。
    member_configs:新版成员独立配置,格式 [{member_name, operators, text_keys?}, ...]。
                   优先于 operators+target_members 模式;指定时 operators/text_keys 参数被忽略。
    返回 (新版本, 生成的 yaml 文本, 运行日志路径)。失败抛 EngineError。
    """
    # 1. 查询输入版本的成员
    members = await _get_version_members(session, input_version.id)

    # 如果版本无成员表（旧版本），回退到原逻辑
    if not members:
        # 旧版调用必须提供 operators
        if not operators:
            raise EngineError("旧版单文件版本必须提供 operators 参数")
        return await _run_process_job_legacy(
            session,
            job_id=job_id,
            input_version=input_version,
            operators=operators,
            text_keys=text_keys,
            use_ray=use_ray,
            media_keys=media_keys,
        )

    # 2. 确定处理模式：优先使用 member_configs，否则回退到 operators 统一配置
    if member_configs:
        # 新版：每个成员独立配置
        config_map = {cfg["member_name"]: cfg for cfg in member_configs}
        members_to_process = [m for m in members if m.table_name in config_map]

        if not members_to_process:
            raise EngineError("未找到 member_configs 中指定的成员")
    else:
        # 旧版：target_members + operators
        if not operators:
            raise EngineError("未提供 member_configs 时必须提供 operators 参数")

        if target_members is not None:
            members_to_process = [m for m in members if m.table_name in target_members]
        else:
            members_to_process = members

        if not members_to_process:
            raise EngineError("未找到要处理的成员")

        # 为所有成员使用相同的 operators
        config_map = {
            m.table_name: {"operators": operators, "text_keys": text_keys}
            for m in members_to_process
        }

    # 3. 创建新版本目录和基础结构
    dataset_id = input_version.dataset_id
    max_vno = await session.scalar(
        select(func.max(DatasetVersion.version_no)).where(
            DatasetVersion.dataset_id == dataset_id
        )
    )
    new_vno = (max_vno or 0) + 1
    out_dir = Path(settings.datasets_dir) / dataset_id / f"v{new_vno}"
    out_dir.mkdir(parents=True, exist_ok=True)

    # 4. 对每个成员独立处理
    all_logs: list[str] = []
    all_yamls: list[str] = []
    new_members_data: list[dict[str, Any]] = []

    for member in members_to_process:
        # 获取该成员的配置
        member_cfg = config_map[member.table_name]
        member_operators = member_cfg["operators"]
        member_text_keys = member_cfg.get("text_keys")

        # 物化成员文件
        input_path = await _materialize_member(session, member)

        # 确定输出格式（保持输入格式）
        out_format = member.format if member.format in ("parquet", "jsonl") else "jsonl"
        output_path = _get_member_output_path(
            dataset_id, new_vno, member.table_name, out_format
        )
        yaml_path = out_dir / f"{member.table_name}_job.yaml"

        # 构建 DJ 配置（使用该成员的算子）
        detected_key = (
            None
            if member_text_keys
            else detect_text_key(_read_head_records(input_path, 50))
        )
        cfg = build_config(
            project_name=f"{job_id}-{member.table_name}",
            input_path=str(input_path),
            output_path=str(output_path),
            operators=member_operators,
            text_key=detected_key,
            text_keys=member_text_keys,
            executor_type="ray" if use_ray else None,
            media_keys=media_keys if member.format == MANIFEST_FORMAT else None,
        )
        yaml_content = yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False)
        yaml_path.write_text(yaml_content, encoding="utf-8")

        # 收集该成员的 YAML（带标识）
        all_yamls.append(f"# Member: {member.table_name}\n{yaml_content}")

        # 运行 DJ
        async with _semaphore:
            code, log = await _run_dj(
                yaml_path, job_id=f"{job_id}-{member.table_name}"
            )

        # 日志中注明使用的算子
        operator_names = [op["name"] for op in member_operators]
        all_logs.append(
            f"=== {member.table_name} ===\n算子: {operator_names}\n{log}"
        )

        if code != 0 or not output_path.exists():
            tail = "\n".join(log.strip().splitlines()[-8:])
            raise EngineError(
                f"成员 {member.table_name} 处理失败(dj-process 退出码 {code})\n{tail}"
            )

        # 上传产出文件并记录成员数据
        from app.services.external_store import (
            upload_jsonl_member,
            upload_parquet_member,
        )

        if out_format == "parquet":
            data_bytes = output_path.read_bytes()
            storage_uri = await upload_parquet_member(
                dataset_id, new_vno, member.table_name, data_bytes
            )
            rows = len(parquet_bytes_to_records(data_bytes))
        else:
            data_bytes = output_path.read_bytes()
            storage_uri = await upload_jsonl_member(
                dataset_id, new_vno, member.table_name, data_bytes
            )
            rows = sum(
                1 for line in output_path.open(encoding="utf-8") if line.strip()
            )

        new_members_data.append(
            {
                "table_name": member.table_name,
                "storage_uri": storage_uri,
                "format": out_format,
                "rows": rows,
                "size": len(data_bytes),
                "schema_variant": member.schema_variant,
            }
        )

    # 5. 创建新版本和成员记录
    version = DatasetVersion(
        id=_new_version_id(),
        dataset_id=dataset_id,
        version_no=new_vno,
        storage_uri=f"s3://{settings.storage_minio_upload_bucket}/{dataset_id}/v{new_vno}/",
        format="multi" if len(new_members_data) > 1 else new_members_data[0]["format"],
        rows=sum(m["rows"] for m in new_members_data),
        size=sum(m["size"] for m in new_members_data),
        origin="managed",
        produced_by_job_id=job_id,
        note=f"加工产出(来自 v{input_version.version_no})",
    )
    session.add(version)
    await session.flush()

    for m_data in new_members_data:
        member_rec = DatasetVersionTable(
            id=_new_member_id(),
            dataset_version_id=version.id,
            **m_data,
        )
        session.add(member_rec)

    session.add(JobInput(job_id=job_id, dataset_version_id=input_version.id))
    await session.commit()
    await session.refresh(version)

    # 6. 汇总日志
    combined_log = "\n\n".join(all_logs)
    log_path = out_dir / "run.log"
    log_path.write_text(combined_log, encoding="utf-8")

    # 返回所有成员的 YAML（用 --- 分隔，便于查看每个成员的完整配置）
    combined_yaml = "\n\n---\n\n".join(all_yamls)

    return version, combined_yaml, str(log_path)


async def _run_process_job_legacy(
    session: AsyncSession,
    *,
    job_id: str,
    input_version: DatasetVersion,
    operators: list[dict[str, Any]],
    text_keys: list[str] | None = None,
    use_ray: bool = False,
    media_keys: dict[str, str] | None = None,
) -> tuple[DatasetVersion, str, str]:
    """旧版单文件处理逻辑（无成员表的版本）。"""
    dataset_id = input_version.dataset_id
    max_vno = await session.scalar(
        select(func.max(DatasetVersion.version_no)).where(
            DatasetVersion.dataset_id == dataset_id
        )
    )
    new_vno = (max_vno or 0) + 1
    out_dir = Path(settings.datasets_dir) / dataset_id / f"v{new_vno}"
    out_dir.mkdir(parents=True, exist_ok=True)
    is_parquet = input_version.format == "parquet"
    out_path = out_dir / ("data.parquet" if is_parquet else "data.jsonl")
    yaml_path = out_dir / "job.yaml"
    log_path = out_dir / "run.log"

    # 输入经解析器拿本地路径:hosted 按需从 S3 拉取并规范化(临时),managed 透传。
    # 产出仍写受管存储(origin=managed),源不动;血缘 JobInput 指向 hosted 输入版本。
    async with materialized_version(input_version, session) as input_path:
        # 用户显式指定 text_keys 则用之;否则自动探测主文本字段(数据无 text 字段时
        # 如新闻用 title,不显式指定 DJ load_dataset 会报错)
        detected_key = (
            None if text_keys else detect_text_key(_read_head_records(input_path, 50))
        )
        cfg = build_config(
            project_name=job_id,
            input_path=str(input_path),
            output_path=str(out_path),
            operators=operators,
            text_key=detected_key,
            text_keys=text_keys,
            executor_type="ray" if use_ray else None,
            # 媒体键仅对 manifest 输入有意义,避免污染纯文本/parquet YAML
            media_keys=(
                media_keys if input_version.format == MANIFEST_FORMAT else None
            ),
        )
        yaml_text = yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False)
        yaml_path.write_text(yaml_text, encoding="utf-8")

        # 并发信号量由调用方(job_runner)持有;此处只负责跑子进程(可被 terminate_job 停止)
        code, log = await _run_dj(yaml_path, job_id=job_id)

        # manifest 输入:产物里的媒体引用指向物化临时目录(用完即清),趁临时文件还在,
        # 把媒体回传平台 MinIO、清单改写为对象引用 → 产物仍是自包含的 manifest 版本。
        manifest_out: tuple[str, int, int] | None = None
        if code == 0 and out_path.exists() and input_version.format == MANIFEST_FORMAT:
            manifest_out = await persist_manifest_output(
                jsonl_path=out_path, dataset_id=dataset_id, version_no=new_vno
            )
    log_path.write_text(log, encoding="utf-8")

    if code != 0 or not out_path.exists():
        tail = "\n".join(log.strip().splitlines()[-8:])
        raise EngineError(f"dj-process 退出码 {code}\n{tail}")

    if manifest_out is not None:
        # 媒体加工产出:storage_uri 指向平台 MinIO 上的清单,与媒体批量接入版本同形
        storage_uri, rows, size = manifest_out
        version = DatasetVersion(
            id=_new_version_id(),
            dataset_id=dataset_id,
            version_no=new_vno,
            storage_uri=storage_uri,
            format=MANIFEST_FORMAT,
            rows=rows,
            size=size,
            origin="managed",
            produced_by_job_id=job_id,
            note=f"加工产出(来自 v{input_version.version_no})",
        )
    else:
        stats_path = out_dir / "data_stats.jsonl"
        # 产出文件上传 MinIO(治理产出必须持久化到对象存储,不能只在本地;
        # 读路径 preview/download/materialize 已按 s3:// scheme 走,无需改动)
        if is_parquet:
            rows = len(parquet_bytes_to_records(out_path.read_bytes()))
            storage_uri = await upload_parquet_file_to_uploads(
                dataset_id, new_vno, out_path
            )
            out_fmt = "parquet"
        else:
            rows = sum(1 for line in out_path.open(encoding="utf-8") if line.strip())
            storage_uri = await upload_file_to_uploads(dataset_id, new_vno, out_path)
            out_fmt = "jsonl"
        version = DatasetVersion(
            id=_new_version_id(),
            dataset_id=dataset_id,
            version_no=new_vno,
            storage_uri=storage_uri,
            stats_uri=str(stats_path) if stats_path.exists() else None,
            format=out_fmt,
            rows=rows,
            size=out_path.stat().st_size,
            origin="managed",
            produced_by_job_id=job_id,
            note=f"加工产出(来自 v{input_version.version_no})",
        )
    session.add(version)
    session.add(JobInput(job_id=job_id, dataset_version_id=input_version.id))
    await session.commit()
    await session.refresh(version)
    return version, yaml_text, str(log_path)
